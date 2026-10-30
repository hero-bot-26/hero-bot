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
HEADER = ["date", "kind", "sku", "style", "name", "color", "hero", "season", "qty", "late", "asn_no", "supplier", "cat"]
# 화면 카테고리 태그(2026-10-30 사용자: 맨/우먼/키즈/잡화/신발/언더웨어/홈). 정본은 PLM `데이터` 탭의 `대복종`·`브랜드라인`
#   ([[CLAUDE 1-11]] 문자열 추론 금지) — 대복종이 먼저(키즈 언더웨어 = 언더웨어, ASN 알림 MD 팀장 배정과 같은 순서).
#   PLM 에 없는 품번(지난 시즌 등)만 품번 앞 2자로 폴백, 그것도 모르면 '기타'.
CAT_BIG = {"Underwear": "언더웨어", "Shoes": "신발", "Goods": "홈", "Accessory": "잡화"}
CAT_LINE = {"MAN": "맨", "WOMAN": "우먼", "KIDS": "키즈", "ACCESSORY": "잡화"}
CAT_PREFIX = {"MM": "맨", "MW": "우먼", "MK": "키즈", "ME": "잡화", "MH": "홈"}
EARLY_DAYS = 60          # 예정일보다 이만큼 앞선 입고까지만 그 차수를 채운 것으로 본다
GRACE = IB.CONFIRM_GRACE_DAYS   # 예정일 + 3일이 지나도 안 덮였으면 지연(WMS 확정은 하루 늦게 온다)
ASN_LATE_DAYS = 2        # ASN 입고 예정일 + 2일이 지나도 WMS 확정이 없으면 지연
OLD_DAYS = 60            # 예정일이 이만큼 지났는데 하나도 안 온 차수 = '오래된 미입고'(late=O, 지연 카드에서 뺀다)
ASN_REISSUE_DAYS = 14   # 같은 컬러·같은 통보 수량 ASN 이 이 안에 또 오면 다시 올린 것(옛 통보 미취소)으로 본다
ASN_NET_DAYS = 30       # ASN 날짜부터 이 안에 같은 컬러로 들어온(ASN 에 안 이어진) WMS 입고는 그 통보분으로 본다
MIN_ROWS = 3000          # 10/29 실측 ~1.5만 행 — 이보다 적으면 원천 부분 로딩으로 보고 쓰지 않는다


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _ymd(s: str) -> str:
    s = str(s).strip().split(".")[0].replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else ""


def load_asn_pending(sheets, linked: dict | None = None) -> dict[str, list[dict]]:
    """`_OPS_ASN` → 품번-컬러 → [ASN 잔량(통보 − 중복 − WMS 확정)]. 취소·잔량 0 은 뺀다.
    linked 를 주면 WMS 에 이어진 확정분 {품번-컬러: [{date, recv_dates, qty}]} 을 채운다(net_asn 용)."""
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
        if linked is not None and IB._num(g("recv_qty")) > 0:
            linked.setdefault(str(g("sku")).strip(), []).append({
                "date": _ymd(e), "qty": IB._num(g("recv_qty")),
                "recv_dates": [d for d in (_ymd(x) for x in str(g("recv_dates")).split(",")) if d]})
        pend =IB._num(g("qty")) - IB._num(g("dup_qty")) - IB._num(g("recv_qty"))
        if pend <= 0:
            continue
        out[str(g("sku")).strip()].append({
            "date": f"{e[:4]}-{e[4:6]}-{e[6:8]}", "qty": pend, "asn_qty": IB._num(g("qty")), "recv": IB._num(g("recv_qty")), "asn_no": str(g("asn_no")),
            "supplier": str(g("supplier")), "name": str(g("name")), "color": str(g("color_nm")),
            "style": str(g("style")), "hero": str(g("hero"))})
    return out


def net_asn(asn: dict, wms: dict, linked: dict) -> tuple[dict, collections.Counter]:
    """ASN 잔량에서 그 뒤 같은 컬러로 들어온 WMS 입고(ASN 에 안 이어진 것)를 지운다.
    업체가 같은 물량을 ASN 으로 여러 번 다시 올리고 옛 통보를 취소하지 않으면, WMS 는 한 번만 들어오는데
    옛 통보가 전부 'ASN 지연'으로 남는다(MWBCTBZ03 10/13·13·14·16 재통보 → 10/23 입고, 2026-10-30 사용자 지적).
    WMS 한 개는 통보 한 개만 지운다 — 이미 ASN 에 이어진 확정분(recv_qty)을 먼저 빼고, 남은 입고를 날짜순으로.
    그 전에 다시 올린 통보(같은 컬러·같은 통보 수량·ASN_REISSUE_DAYS 안)는 가장 늦은 것 하나만 남긴다."""
    cnt = collections.Counter()
    out = {}
    for sku, arr in asn.items():
        arr = sorted(arr, key=lambda x: x["date"])
        uniq = []
        for a in arr:
            prev = next((u for u in reversed(uniq) if u.get("asn_qty") and u["asn_qty"] == a.get("asn_qty")
                         and (datetime.date.fromisoformat(a["date"])
                              - datetime.date.fromisoformat(u["date"])).days <= ASN_REISSUE_DAYS), None)
            if prev is not None:
                cnt["재통보로 합친 잔량"] += prev["qty"]
                uniq.remove(prev)
            uniq.append(a)
        arr = uniq
        pool = [dict(w) for w in wms.get(sku, [])]
        for L in sorted(linked.get(sku, []), key=lambda x: x["date"]):
            need = L["qty"]
            for pick in ([w for w in pool if w["date"] in L["recv_dates"]], [w for w in pool if w["date"] >= L["date"]]):
                for w in pick:
                    take = min(need, w["qty"]); w["qty"] -= take; need -= take
                    if need <= 0:
                        break
                if need <= 0:
                    break
        kept = []
        for a in sorted(arr, key=lambda x: x["date"]):
            hi = (datetime.date.fromisoformat(a["date"]) + datetime.timedelta(days=ASN_NET_DAYS)).isoformat()
            left = a["qty"]
            for w in pool:
                if left <= 0:
                    break
                if w["qty"] <= 0 or not (a["date"] <= w["date"] <= hi):
                    continue
                take = min(left, w["qty"]); w["qty"] -= take; left -= take
            cnt["지운 ASN 잔량"] += a["qty"] - left
            if left > 0:
                kept.append(dict(a, qty=left))
        if kept:
            out[sku] = kept
    return out, cnt


def build_events(board: dict, asn: dict, today: datetime.date,
                 wms: dict | None = None) -> tuple[list[list], collections.Counter]:
    t = today.isoformat()
    late_plan = (today - datetime.timedelta(days=GRACE)).isoformat()
    late_asn = (today - datetime.timedelta(days=ASN_LATE_DAYS)).isoformat()
    old_plan = (today - datetime.timedelta(days=OLD_DAYS)).isoformat()
    out, cnt, seen = [], collections.Counter(), set()
    # 일부만 확정되고 날짜가 지난 ASN 의 잔량은 '덜 보낸 것'(수량 차이)이지 앞으로 올 물량이 아니다 → 뺀다
    asn = {k: [a for a in v if not (a.get("recv") and a["date"] <= late_asn)] for k, v in asn.items()}

    def emit(date, kind, s, qty, season, late=False, asn_no="", supplier=""):
        code = late if isinstance(late, str) else ("Y" if late else "")
        out.append([date, kind, s["sku"], s["style"], s["name"], s["color"], s["hero"], season, qty,
                    code, asn_no, supplier])
        cnt[kind + {"Y": " 지연", "S": " 수량 차이", "O": " 오래된 미입고"}.get(code, "")] += qty

    for h in board["heroes"]:
        for s0 in h["skus"]:
            s = {"sku": s0["sku"], "style": s0["style"], "name": s0["name"], "color": s0["color"], "hero": h["name"]}
            seen.add(s0["sku"])
            lots = [dict(p) for p in sorted(s0["planned"], key=lambda p: p["date"])]
            # 보드 실적은 컷오프(6/1) 뒤 WMS 만 담는다 → 컷오프 전에 일찍 들어온 차수가 '지연'으로 남았다
            #   (MEFSC0A51-BK 6/1 예정 1,000 ← 5/22 입고, 2026-10-30). 첫 차수 −EARLY_DAYS 까지 WMS 를 다시 붙이고,
            #   그 앞당긴 입고는 차수를 채운 만큼만 줄로 낸다(옛 시즌 입고가 화면에 쏟아지지 않게).
            pre = []
            if wms is not None and lots and s0.get("dbx_key") and s0.get("from"):
                lo0 = (datetime.date.fromisoformat(lots[0]["date"]) - datetime.timedelta(days=EARLY_DAYS)).isoformat()
                pre = [{"date": a["date"], "qty": a["qty"], "kind": "입고", "pre": True}
                       for a in wms.get(s0["dbx_key"], []) if lo0 <= a["date"] < s0["from"]]
            pool = (pre + [{"date": a["date"], "qty": a["qty"], "kind": "입고"} for a in s0["actual"]]
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
                if a.get("pre"):
                    if a["qty"] - a["left"] > 0:
                        emit(a["date"], "입고", s, a["qty"] - a["left"], a["season"] or sku_season)
                        cnt["컷오프 전 입고로 채움"] += a["qty"] - a["left"]
                elif a["kind"] == "입고":
                    emit(a["date"], "입고", s, a["qty"], a["season"] or sku_season)
                else:
                    emit(a["date"], "ASN", s, a["qty"], a["season"] or sku_season, a["date"] <= late_asn,
                         a["asn_no"], a["supplier"])
            for p in lots:
                if p["left"] > 0:
                    # 지난 예정 잔량을 셋으로 가른다(2026-10-30 사용자 지시 — '날짜 지났는데 안 들어왔어요'가 너무 많다):
                    #   S 수량 차이 = 그 차수가 일부라도 들어왔다(늦은 게 아니라 덜 온 것)
                    #   O 오래된 미입고 = 하나도 안 왔고 예정일이 OLD_DAYS 넘게 지났다(취소·이월 행 정리 대상)
                    #   Y 지연 = 그 밖(진짜 늦는 것)
                    late = ("" if p["date"] > late_plan else "S" if p["left"] < p["qty"]
                            else "O" if p["date"] < old_plan else "Y")
                    emit(p["date"], "예정", s, p["left"], p.get("season", ""), late)
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


def load_categories(sheets) -> dict[str, str]:
    """PLM `데이터` 탭 → {style_no: 카테고리}. 대복종 → 브랜드라인 순. 같은 style 이 여러 행이면 먼저 찬 값."""
    from soo.hero_ops import asn_notify as N
    v = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{N.PLM_TAB}'!A1:AZ",
                                           valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    if not v:
        return {}
    h = [str(c).strip() for c in v[0]]
    if "style_no" not in h or "대복종" not in h:
        print(f"[카테고리] `{N.PLM_TAB}` 에 style_no/대복종 열이 없다 {h[:12]} → 품번 접두 폴백만")
        return {}
    I = {k: i for i, k in enumerate(h)}
    g = lambda r, k: str(r[I[k]]).strip() if k in I and I[k] < len(r) else ""
    out = {}
    for r in v[1:]:
        st = g(r, "style_no")
        if not st or out.get(st):
            continue
        line = g(r, "브랜드라인").upper()
        out[st] = CAT_BIG.get(g(r, "대복종")) or next((c for k, c in CAT_LINE.items() if f"({k})" in line), "")
    return out


def add_categories(grid: list[list], cats: dict[str, str]) -> collections.Counter:
    src = collections.Counter()
    for r in grid:
        c = cats.get(r[3], "")
        src["PLM" if c else "접두" if r[3][:2] in CAT_PREFIX else "기타"] += 1
        r.append(c or CAT_PREFIX.get(r[3][:2], "기타"))
    return src


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
    linked = {}
    asn = load_asn_pending(sheets, linked)
    asn, ncnt = net_asn(asn, dbx, linked)
    print(f"[ASN] 뒤에 들어온 WMS 로 지운 잔량 {ncnt['지운 ASN 잔량']:,}개")
    asn_gate = {k: {"qty": sum(x["qty"] for x in v), "dates": sorted({x["date"] for x in v})} for k, v in asn.items()}
    board = IB.build_inbound_board(sheets, as_of=today, dbx_actuals=dbx, asn=asn_gate, all_products=True)
    grid, cnt = build_events(board, asn, today, dbx)
    csrc = add_categories(grid, load_categories(sheets))
    print("[카테고리] 근거 " + " · ".join(f"{k} {v:,}행" for k, v in csrc.most_common())
          + " · " + " · ".join(f"{k} {v:,}" for k, v in collections.Counter(r[-1] for r in grid).most_common()))

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
