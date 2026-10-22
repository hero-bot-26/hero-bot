# -*- coding: utf-8 -*-
"""입고-발매 대시보드 · 입고 — 무탠 **전 상품** ASN(입하 통보) → 앱 시트 `_OPS_ASN`.

왜 따로 두나 (2026-10-22, 사용자 결정)
  대시보드는 전 상품, **슬랙 ASN 알림은 히어로 유지**. 히어로 `_ASN` 탭은 asn_notify·inbound_board·`/api/asn`
  세 소비자가 읽으니([[CLAUDE 1-1]]) 그 탭을 넓히지 않고 탭을 하나 더 만든다. 조회·판정 로직은 asn_ingest 를
  그대로 쓴다(fetch_asn(all_brand=True) · to_grid · mark_duplicates · 연결률 가드) — 규칙이 두 벌로 갈리지 않게.

무탠 판정 = asn_ingest.MUTAN_CTE (26FW 히어로 실적 노트북 입고·재고 쿼리와 같은 datamart.goods brand_nm 7종).
  10/22 실측(45일): 3,537행 · ASN 524건 · 653품번 · 통보 3,449,594 · WMS 확정 3,151,160 · 유예 지난 통보 연결률 100%.

조회 경로 = DBX 쿼리 중계 spec 'asn_all'(매시 :40, 07~19시). 히어로 spec 'asn' 과 같은 휴지 시간대 규칙.

사용법
  python -m soo.hero_ops.asn_board            # 드라이런
  python -m soo.hero_ops.asn_board --apply    # `_OPS_ASN` 기입
"""
from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops import asn_ingest as A
from soo.hero_ops.inbound_board import build_pumbon2hero

HERO = Path(__file__).resolve().parents[2]
TAB = "_OPS_ASN"
MIN_ROWS = 500           # 실측 ~3,500행(45일) — 이보다 적으면 부분 결과로 보고 쓰지 않는다


def write(sheets, grid: list[list], as_of: str) -> None:
    sid = A.APP_SHEET_ID
    meta = sheets.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties(title,sheetId,gridProperties)").execute()
    hit = next((s["properties"] for s in meta["sheets"] if s["properties"]["title"] == TAB), None)
    need = len(grid) + 1000
    if hit is None:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [{"addSheet": {"properties": {
            "title": TAB, "hidden": True,
            "gridProperties": {"rowCount": need, "columnCount": len(A.HEADER), "frozenRowCount": 2}}}}]}).execute()
    elif hit["gridProperties"]["rowCount"] < len(grid) + 2:
        # ★그리드를 박아 두면 넘친다([[CLAUDE 1-2]]) — 쓰기 전에 키운다
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": hit["sheetId"], "gridProperties": {"rowCount": need}},
            "fields": "gridProperties.rowCount"}}]}).execute()
    label = (f"무탠 전 상품 ASN(입하 통보) · pbo.moms.iif_bam_asn · 무탠 판정=datamart.goods brand_nm 7종 "
             f"· 최근 {A.LOOKBACK_DAYS}일 · 수량=EINQTY · 생성 {as_of}")
    body = [[label] + [""] * (len(A.HEADER) - 1), A.HEADER] + grid
    sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{TAB}'!A1:{A.LAST_COL}", body={}).execute()
    sheets.spreadsheets().values().update(spreadsheetId=sid, range=f"'{TAB}'!A1",
                                          valueInputOption="RAW", body={"values": body}).execute()
    back = sheets.spreadsheets().values().get(spreadsheetId=sid, range=f"'{TAB}'!D3:D").execute().get("values", [])
    if len(back) != len(grid):
        raise RuntimeError(f"[{TAB}] 되읽기 {len(back)}행 ≠ 기입 {len(grid)}행")
    print(f"[{TAB}] 기입 {len(grid):,}행 · 되읽기 일치 · {as_of}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sheets = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))["sheets"]
    p2h = build_pumbon2hero(sheets)          # 화면의 히어로 딱지용(전 상품이라 대부분은 빈칸)

    from soo.hero_ops import dbx_relay
    dbx_relay.configure(sheets, "asn_all", max_age_h=2)
    try:
        rows = A.fetch_asn([], A.LOOKBACK_DAYS, all_brand=True)
    except dbx_relay.RelayStale as e:
        # 히어로 ASN 과 같은 규칙 — 중계는 07~19시만 돈다. 밤 낡음은 실패가 아니라 건너뜀.
        h = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)).hour
        if h >= A.RELAY_IDLE_FROM or h < A.RELAY_IDLE_UNTIL:
            print(f"[{TAB}] 건너뜀 — 중계 휴지 시간대({h}시 KST): {e}")
            return
        raise
    finally:
        dbx_relay._cfg["sheets"] = None
    if len(rows) < MIN_ROWS:
        raise RuntimeError(f"전 상품 ASN {len(rows)}행 < {MIN_ROWS} — 부분 결과 의심, 쓰지 않는다")

    today = A._kst_today()
    grid = A.mark_duplicates(A.to_grid(rows, p2h), today)
    linked, aged = A.link_stats(grid, today)
    if aged >= 20 and linked < aged * A.MIN_LINK_RATIO:
        miss = [g for g in grid if g[A.C["wms_linked"]] != "Y"][:5]
        raise RuntimeError(f"WMS 연결률 {linked}/{aged} < {A.MIN_LINK_RATIO:.0%} — 미연결 예: "
                           + ", ".join(f"{g[A.C['asn_no']]}:{g[A.C['sku']]}" for g in miss))
    print(f"[{TAB}] {A.summarize(grid, today)} · 히어로 {sum(1 for g in grid if g[A.C['hero']]):,}행")
    as_of = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")
    if a.apply:
        write(sheets, grid, as_of)
    else:
        print("(드라이런 — --apply 로 기입)")


if __name__ == "__main__":
    main()
