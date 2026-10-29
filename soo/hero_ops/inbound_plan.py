# -*- coding: utf-8 -*-
"""입고-발매 대시보드 · 입고 — 무탠 **전 상품** "언제 · 무슨 컬러가 · 얼마나 들어오나" → 앱 시트 `_OPS_INBOUND`.

왜 (2026-10-29, 사용자 지시)
  히어로 앱 `/inbound`(입고 보드)를 옮기고(→ 전 상품), 이어서 대시보드의 '입고 예정'·'입고(ASN)' 두 탭을 하나로 합쳤다.
  목적은 소싱 관리가 아니라 **정확히 언제 무슨 컬러가 얼마나 들어오나**. 그래서 수량마다 지금 가장 확실한 날짜 하나만 붙인다:
    입고  = WMS 입고확정일(이미 들어옴)
    ASN   = 업체 ASN 입고 예정일(아직 WMS 확정 전인 통보 잔량)
    예정  = 오더시트 생산관리 입고예정일(위 둘로 아직 안 덮인 나머지)
  PO 번호로 묶지 않는다 — 오더시트 PO(PO26…)와 ASN PO(4500…)는 체계가 다르고, 목적상 SKU 수량 흐름이면 충분하다.

배분 (SKU = 품번-컬러)
  오더시트 차수(발주량 상한 적용 — inbound_board._cap_plans)를 날짜순으로, WMS 입고 + ASN 잔량을 날짜순으로 채운다.
  단 입고/ASN 은 그 차수 예정일보다 EARLY_DAYS 넘게 앞선 것으로는 못 채운다(9월 입고가 27SS 1월 차수를 지우지 않게).
  못 채운 차수 잔량만 '예정' 줄로 남고, 예정일 + GRACE 가 지났으면 지연.

원천
  오더시트 '생산관리' AK/AL·AO/AP·AE(준비시즌) · 히어로 실적(자동) '입고일자별'(WMS, 무탠 전 브랜드) · `_OPS_ASN`(asn_board)
  → 워크플로에서 asn_board **뒤**에 돈다.

사용법
  python -m soo.hero_ops.inbound_plan            # 드라이런
  python -m soo.hero_ops.inbound_plan --apply    # `_OPS_INBOUND` 기입
"""
from __future__ import annotations

import argparse
import collections
import datetime
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops import inbound_board as IB
from soo.hero_ops.asn_ingest import APP_SHEET_ID, LAST_COL as ASN_LAST_COL

HERO = Path(__file__).resolve().parents[2]
TAB = "_OPS_INBOUND"
ASN_TAB = "_OPS_ASN"
HEADER = ["date", "kind", "sku", "style", "name", "color", "hero", "season", "qty", "late", "asn_no", "supplier"]
EARLY_DAYS = 60          # 예정일보다 이만큼 앞선 입고까지만 그 차수를 채운 것으로 본다
GRACE = IB.CONFIRM_GRACE_DAYS   # 예정일 + 3일이 지나도 안 덮였으면 지연(WMS 확정은 하루 늦게 온다)
ASN_LATE_DAYS = 2        # ASN 입고 예정일 + 2일이 지나도 WMS 확정이 없으면 지연
MIN_ROWS = 3000          # 10/29 실측 ~1.5만 행 — 이보다 적으면 원천 부분 로딩으로 보고 쓰지 않는다


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def load_asn_pending(sheets) -> dict[str, list[dict]]:
    """`_OPS_ASN` → 품번-컬러 → [ASN 잔량(통보 − 중복 − WMS 확정)]. 취소·잔량 0 은 뺀다."""
    v = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{ASN_TAB}'!A2:{ASN_LAST_COL}",
                                           valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    if len(v) < 2:
        raise RuntimeError(f"[{ASN_TAB}] 비었다 — 전 상품 ASN 스텝(asn_board) 먼저")
    h = [str(c).strip() for c in v[0]]
    need = ["asn_no", "sku", "style", "name", "color_nm", "hero", "eindt", "qty", "recv_qty", "dup_qty", "cancelled", "supplier"]
    miss = [k for k in need if k not in h]
    if miss:
        raise RuntimeError(f"[{ASN_TAB}] 열 없음 {miss}")
    out = collections.defaultdict(list)
    for r in v[1:]:
        g = lambda k: r[h.index(k)] if h.index(k) < len(r) else ""
        if str(g("cancelled")).upper() == "Y":
            continue
        e = str(g("eindt")).split(".")[0]
        if len(e) != 8:
            continue
        pend = IB._num(g("qty")) - IB._num(g("dup_qty")) - IB._num(g("recv_qty"))
        if pend <= 0:
            continue
        out[str(g("sku")).strip()].append({
            "date": f"{e[:4]}-{e[4:6]}-{e[6:8]}", "qty": pend, "recv": IB._num(g("recv_qty")), "asn_no": str(g("asn_no")),
            "supplier": str(g("supplier")), "name": str(g("name")), "color": str(g("color_nm")),
            "style": str(g("style")), "hero": str(g("hero"))})
    return out


def build_events(board: dict, asn: dict, today: datetime.date) -> tuple[list[list], collections.Counter]:
    t = today.isoformat()
    late_plan = (today - datetime.timedelta(days=GRACE)).isoformat()
    late_asn = (today - datetime.timedelta(days=ASN_LATE_DAYS)).isoformat()
    out, cnt, seen = [], collections.Counter(), set()
    # 일부만 확정되고 날짜가 지난 ASN 의 잔량은 '덜 보낸 것'(수량 차이)이지 앞으로 올 물량이 아니다 → 뺀다
    asn = {k: [a for a in v if not (a.get("recv") and a["date"] <= late_asn)] for k, v in asn.items()}

    def emit(date, kind, s, qty, season, late=False, asn_no="", supplier=""):
        out.append([date, kind, s["sku"], s["style"], s["name"], s["color"], s["hero"], season, qty,
                    "Y" if late else "", asn_no, supplier])
        cnt[kind + (" 지연" if late else "")] += qty

    for h in board["heroes"]:
        for s0 in h["skus"]:
            s = {"sku": s0["sku"], "style": s0["style"], "name": s0["name"], "color": s0["color"], "hero": h["name"]}
            seen.add(s0["sku"])
            lots = [dict(p) for p in sorted(s0["planned"], key=lambda p: p["date"])]
            pool = ([{"date": a["date"], "qty": a["qty"], "kind": "입고"} for a in s0["actual"]]
                    + [dict(a, kind="ASN") for a in asn.get(s0["sku"], [])])
            pool.sort(key=lambda a: (a["date"], a["kind"] != "입고"))
            for a in pool:
                a["left"] = a["qty"]; a["season"] = ""
            for p in lots:
                need = p["qty"]
                lo = (datetime.date.fromisoformat(p["date"]) - datetime.timedelta(days=EARLY_DAYS)).isoformat()
                for a in pool:
                    if need <= 0:
                        break
                    if a["left"] <= 0 or a["date"] < lo:
                        continue
                    take = min(need, a["left"])
                    a["left"] -= take; need -= take
                    a["season"] = a["season"] or p.get("season", "")
                p["left"] = need
            # 시트 실입고(AO/AP)는 줄로 내지 않고 **남은 차수를 덮는 데만** 쓴다 — WMS 가 컬러 없는 바코드로 받아
            #   '(컬러 미상)' 줄로 이미 나온 물량(예: 힛탠다드 타이즈 10/23 33,700)이 컬러 줄에서 '지연'으로 남지 않게.
            #   WMS·ASN 이 먼저 채우고 나서 두 번째로 돈다(앞에 두면 WMS 가 뒤 차수로 밀린다).
            sp = [dict(a) for a in sorted(s0.get("sheet_actual", []), key=lambda a: a["date"])]
            for p in lots:
                lo = (datetime.date.fromisoformat(p["date"]) - datetime.timedelta(days=EARLY_DAYS)).isoformat()
                for a in sp:
                    if p["left"] <= 0:
                        break
                    if a["qty"] <= 0 or a["date"] < lo:
                        continue
                    take = min(p["left"], a["qty"])
                    a["qty"] -= take; p["left"] -= take
            sku_season = next((p.get("season") for p in reversed(lots) if p.get("season")), "")
            for a in pool:
                if a["kind"] == "입고":
                    emit(a["date"], "입고", s, a["qty"], a["season"] or sku_season)
                else:
                    emit(a["date"], "ASN", s, a["qty"], a["season"] or sku_season, a["date"] <= late_asn,
                         a["asn_no"], a["supplier"])
            for p in lots:
                if p["left"] > 0:
                    emit(p["date"], "예정", s, p["left"], p.get("season", ""), p["date"] <= late_plan)
    # 오더시트에도 WMS 에도 없는 ASN(신규 품번 등) — 통보 그대로 싣는다
    for sku, arr in asn.items():
        if sku in seen:
            continue
        for a in arr:
            s = {"sku": sku, "style": a["style"], "name": a["name"], "color": a["color"], "hero": a["hero"]}
            emit(a["date"], "ASN", s, a["qty"], "", a["date"] <= late_asn, a["asn_no"], a["supplier"])
            cnt["ASN(오더시트 없음)"] += a["qty"]
    # 같은 날·같은 SKU·같은 근거 줄은 합친다(분할 차수가 같은 날짜로 남은 것)
    agg = collections.OrderedDict()
    for r in out:
        k = (r[0], r[1], r[2], r[9], r[10])
        if k in agg:
            agg[k][8] += r[8]
            agg[k][7] = agg[k][7] or r[7]
        else:
            agg[k] = r
    out = sorted(agg.values(), key=lambda r: (r[0], r[3], r[2]))
    return out, cnt


def write(sheets, grid: list[list], label: str) -> None:
    sid = APP_SHEET_ID
    meta = sheets.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties(title,sheetId,gridProperties)").execute()
    hit = next((s["properties"] for s in meta["sheets"] if s["properties"]["title"] == TAB), None)
    need = len(grid) + 2000
    if hit is None:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [{"addSheet": {"properties": {
            "title": TAB, "hidden": True,
            "gridProperties": {"rowCount": need, "columnCount": len(HEADER), "frozenRowCount": 2}}}}]}).execute()
    elif hit["gridProperties"]["rowCount"] < len(grid) + 2:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": hit["sheetId"], "gridProperties": {"rowCount": need}},
            "fields": "gridProperties.rowCount"}}]}).execute()
    body = [[label] + [""] * (len(HEADER) - 1), HEADER] + grid
    # ★덮어쓴 뒤 꼬리만 지운다 — '먼저 지우고 쓰기'는 중간에 끊기면 잘린 탭을 남긴다([[CLAUDE 1-3]]).
    #   열 수가 줄었을 수 있어(구 SKU 형식 15열) 오른쪽 꼬리도 지운다.
    sheets.spreadsheets().values().update(spreadsheetId=sid, range=f"'{TAB}'!A1",
                                          valueInputOption="RAW", body={"values": body}).execute()
    sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{TAB}'!A{len(body) + 1}:AZ", body={}).execute()
    sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{TAB}'!{_col(len(HEADER) + 1)}1:AZ", body={}).execute()
    back = sheets.spreadsheets().values().get(spreadsheetId=sid, range=f"'{TAB}'!C3:C").execute().get("values", [])
    if len(back) != len(grid):
        raise RuntimeError(f"[{TAB}] 되읽기 {len(back)}행 ≠ 기입 {len(grid)}행")
    print(f"[{TAB}] 기입 {len(grid):,}행 · 되읽기 일치")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sheets = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))["sheets"]
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    today = now.date()

    dbx = IB.load_dbx_actuals(sheets)
    if not dbx:
        raise RuntimeError("'입고일자별'(WMS 입고확정)을 못 읽었다 — 입고 줄이 통째로 비니 쓰지 않는다")
    wms_max = max(x["date"] for v in dbx.values() for x in v)
    asn = load_asn_pending(sheets)
    asn_gate = {k: {"qty": sum(x["qty"] for x in v), "dates": sorted({x["date"] for x in v})} for k, v in asn.items()}
    board = IB.build_inbound_board(sheets, as_of=today, dbx_actuals=dbx, asn=asn_gate, all_products=True)
    grid, cnt = build_events(board, asn, today)

    print(f"[{TAB}] {len(grid):,}행 · " + " · ".join(f"{k} {v:,}개" for k, v in sorted(cnt.items()))
          + f" · WMS ~{wms_max}")
    if len(grid) < MIN_ROWS:
        raise RuntimeError(f"{len(grid)}행 < {MIN_ROWS} — 원천 부분 로딩 의심, 쓰지 않는다")
    label = (f"무탠 전 상품 입고 · 입고=WMS 입고일자별(~{wms_max}) · ASN=업체 통보 잔량 · 예정=오더시트 생산관리(발주량 상한) "
             f"· 컷오프 {board['cutoff']} · 생성 {now.strftime('%Y-%m-%d %H:%M')} KST")
    if a.apply:
        write(sheets, grid, label)
    else:
        print("(드라이런 — --apply 로 기입)")


if __name__ == "__main__":
    main()
