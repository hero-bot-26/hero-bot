# -*- coding: utf-8 -*-
"""입고-발매 대시보드 · 입고 계획 — 무탠 **전 상품** 입고예정(오더시트) vs 입고확정(WMS) → 앱 시트 `_OPS_INBOUND`.

왜 (2026-10-29, 사용자 지시)
  히어로 앱 `/inbound`(26FW 입고 보드, 히어로 15종)를 입고-발매 대시보드로 옮기고 전 상품으로 넓힌다.
  판정은 inbound_board.build_inbound_board(all_products=True) 를 그대로 탄다 — FIFO 차수 배분·D+3 유예·
  시트 실입고/ASN 게이트·예정 외 처리가 히어로 보드와 한 벌이다. 히어로 앱의 const INBOUND_BOARD 는 안 건드린다.

원천
  입고예정 = 무탠본부_오더시트 '생산관리' AK/AL(금주입고일·량) · 시트 실입고 AO/AP
  입고확정 = 히어로 실적(자동) '입고일자별'(DBX 잡, 무탠 전 브랜드 · 품번-컬러 · 전일까지)
  ASN 게이트 = `_OPS_ASN`(asn_board, 전 상품) — 그래서 워크플로에서 asn_board **뒤**에 돈다.

사용법
  python -m soo.hero_ops.inbound_plan            # 드라이런
  python -m soo.hero_ops.inbound_plan --apply    # `_OPS_INBOUND` 기입
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops import inbound_board as IB
from soo.hero_ops.asn_ingest import APP_SHEET_ID, load_asn_from_sheet

HERO = Path(__file__).resolve().parents[2]
TAB = "_OPS_INBOUND"
ASN_TAB = "_OPS_ASN"
HEADER = ["hero", "sku", "style", "name", "color", "status", "plan_total", "actual_total", "sheet_total",
          "ordered_total", "asn_total", "late_days", "next_date", "lots", "extra"]
MIN_SKUS = 1500          # 10/29 실측 ~5,000 SKU — 이보다 적으면 원천 부분 로딩으로 보고 쓰지 않는다


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def to_grid(board: dict) -> list[list]:
    """SKU 1행. 차수·예정 외는 JSON 문자열(앱이 그대로 파싱) — 열을 늘리지 않고 구조를 싣는다."""
    out = []
    for h in board["heroes"]:
        for s in h["skus"]:
            lots = [[p["date"], p["qty"], p.get("recv", 0), p.get("srecv", 0), ",".join(p.get("recv_dates", []))]
                    for p in s["planned"]]
            extra = [[a["date"], a["qty"]] for a in s.get("leftover", [])]
            out.append([h["name"], s["sku"], s["style"], s["name"], s["color"], s["status"],
                        s["plan_total"], s["actual_total"], s["sheet_total"], s["ordered_total"],
                        s.get("asn_total", 0), "" if s["late_days"] is None else s["late_days"],
                        s["next_date"] or "",
                        json.dumps(lots, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(extra, ensure_ascii=False, separators=(",", ":"))])
    return out


def write(sheets, grid: list[list], label: str) -> None:
    sid = APP_SHEET_ID
    meta = sheets.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties(title,sheetId,gridProperties)").execute()
    hit = next((s["properties"] for s in meta["sheets"] if s["properties"]["title"] == TAB), None)
    need = len(grid) + 1000
    if hit is None:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [{"addSheet": {"properties": {
            "title": TAB, "hidden": True,
            "gridProperties": {"rowCount": need, "columnCount": len(HEADER), "frozenRowCount": 2}}}}]}).execute()
    elif hit["gridProperties"]["rowCount"] < len(grid) + 2:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": hit["sheetId"], "gridProperties": {"rowCount": need}},
            "fields": "gridProperties.rowCount"}}]}).execute()
    body = [[label] + [""] * (len(HEADER) - 1), HEADER] + grid
    last = _col(len(HEADER))
    # ★덮어쓴 뒤 꼬리만 지운다 — '먼저 지우고 쓰기'는 중간에 끊기면 빈 탭을 남긴다([[CLAUDE 1-3]])
    sheets.spreadsheets().values().update(spreadsheetId=sid, range=f"'{TAB}'!A1",
                                          valueInputOption="RAW", body={"values": body}).execute()
    sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{TAB}'!A{len(body) + 1}:{last}", body={}).execute()
    back = sheets.spreadsheets().values().get(spreadsheetId=sid, range=f"'{TAB}'!B3:B").execute().get("values", [])
    if len(back) != len(grid):
        raise RuntimeError(f"[{TAB}] 되읽기 {len(back)}행 ≠ 기입 {len(grid)}행")
    print(f"[{TAB}] 기입 {len(grid):,}행 · 되읽기 일치")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sheets = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))["sheets"]
    today = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)).date()

    dbx = IB.load_dbx_actuals(sheets)
    if not dbx:
        raise RuntimeError("'입고일자별'(WMS 입고확정)을 못 읽었다 — 시트 실입고로 대체하면 전 상품 확정이 비어 보이니 쓰지 않는다")
    wms_max = max(x["date"] for v in dbx.values() for x in v)
    asn = load_asn_from_sheet(sheets, cutoff=IB.CUTOFF.isoformat(), tab=ASN_TAB)
    board = IB.build_inbound_board(sheets, as_of=today, dbx_actuals=dbx, asn=asn, all_products=True)
    grid = to_grid(board)

    st = collections.Counter(r[HEADER.index("status")] for r in grid)
    n_hero = sum(1 for r in grid if r[0])
    print(f"[{TAB}] SKU {len(grid):,}(히어로 {n_hero:,}) · 상태 {dict(st)} · ASN 게이트 {len(asn):,} SKU · WMS ~{wms_max}")
    if len(grid) < MIN_SKUS:
        raise RuntimeError(f"SKU {len(grid)} < {MIN_SKUS} — 생산관리 부분 로딩 의심, 쓰지 않는다")
    label = (f"무탠 전 상품 입고 계획 · 입고예정=오더시트 생산관리 AK/AL · 입고확정=WMS 입고일자별(~{wms_max}) "
             f"· 컷오프 {board['cutoff']} · 유예 D+{board['grace_days']} · 생성 {today.isoformat()} "
             f"{(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)).strftime('%H:%M')} KST")
    if a.apply:
        write(sheets, grid, label)
    else:
        print("(드라이런 — --apply 로 기입)")


if __name__ == "__main__":
    main()
