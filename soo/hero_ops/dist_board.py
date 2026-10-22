# -*- coding: utf-8 -*-
"""입고-발매 대시보드 · 오프라인 분배 — 무탠 전 상품의 물류센터 → 매장 출고 현황.

원천 (2026-10-22 MCP 실측)
  WMS 출고 `pbo.moms.ui_gireport_detail` 중 **ORD_TYPE='사내' · STOCK_TRANSFER_CODE='M06'** = 매장 분배.
  · 매장 = `ETC1`(SAP 보관장소 코드, 3000=홍대 · 3010=강남 …) → `musinsa.order_group.shop.shop_code` → **shop_no**
    ★shop_code 하나에 무탠 매장과 29CM·트레이딩 매장이 같이 걸린 코드가 12개 있다(3000 = 홍대점 / 이구갤러리 더현대).
      max(shop_nm) 로 붙이면 홍대 출고가 '이구갤러리'로 둔갑한다 → 무탠 매장 > 셀렉트샵·킥스·아울렛 순으로 고르고
      29CM·트레이딩은 후보에서 뺀다.
  · ★브랜드명이 센터·시기마다 다르게 적힌다(`musinsastandard`·`musinsastandardwoman`·`무신사 스탠다드 우먼`·
    `무신사스탠다드 지비지에이치` …). 한글명 하나로 거르면 주력 센터(신여주1) 흐름이 통째로 빠져 10/14 이전이
    0 으로 보였다 → 영문 접두·한글 두 표기 모두로 거른다.
  · STOCK_TRANSFER_CODE 가 빈 대량 행(9월 129만 장)은 센터 간 이동(`CTC…` 오더, ETC2=목적 센터) — 분배가 아니다.
  · 품번이 두 형식이다 — 신 `MMJPLAZ02VR036`(품번9+컬러2+사이즈) · 옛 `MWJJK004/MR/S` → 둘 다 `품번-컬러` 로 접는다.

매장 기준 (사용자 지시 2026-10-22 "부문 대시보드 쿼리랑 통일")
  [브랜드부문] 2026 부문 대시보드 '오프라인' 탭의 매장 표(C=매장명 · D=shop_no, 43곳)를 그대로 쓴다 —
  사람이 유지하는 목록이라 코드에 박지 않는다([[CLAUDE 1-2]]). 그 목록 밖으로 나간 출고(새 매장 · 무신사 스토어 ·
  킥스 등)는 버리지 않고 `in_list=N` 으로 남긴다([[CLAUDE 1-12]]) — 10/22 실측으로 롯데백화점 창원점(145)이
  4.7만 장을 받는데 탭엔 아직 없다.

조회 경로 = DBX 쿼리 중계 spec 'dist'(CI·로컬은 SQL Warehouse 직접 조회 금지). ★DIST_SQL 은 글자 그대로 중계 키다 —
  고치면 dash_audit/dbx_relay/deploy.py --apply 를 다시 돌린다.

사용법
  python -m soo.hero_ops.dist_board            # 드라이런
  python -m soo.hero_ops.dist_board --apply    # 앱 시트 `_OPS_DIST`(품번-컬러 요약) · `_OPS_STORES`(매장) 기입
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops.imc_triggers import MUTAN_SHEET, MUTAN_TAB

HERO = Path(__file__).resolve().parents[2]
APP_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"
TAB = "_OPS_DIST"
STORE_TAB = "_OPS_STORES"
DASH_ID = "1fLvLu742PtnkuAzWISWzpUo03slgxrSaL1g_TIQ1RT4"     # [브랜드부문] 2026 부문 대시보드 (자동화)
DASH_TAB = "오프라인"
LOOKBACK_DAYS = 45
MIN_ROWS = 5000          # 실측 ~8.7만 행(45일) — 이보다 적으면 부분 결과로 보고 쓰지 않는다

DIST_SQL = """
WITH sh AS (
  SELECT shop_code, shop_no, shop_nm,
         ROW_NUMBER() OVER (PARTITION BY shop_code ORDER BY
           CASE WHEN shop_type = 'offline' AND shop_nm LIKE '무신사 스탠다드%' THEN 0
                WHEN shop_type IN ('offline', 'selectshop', 'kicks', 'outlet') THEN 1 ELSE 9 END, shop_no) AS rn
  FROM musinsa.order_group.shop
  WHERE shop_type NOT IN ('29cm', 'trading')
),
gi AS (
  SELECT CASE WHEN GDS_CD LIKE '%/%' THEN concat(split(GDS_CD, '/')[0], '-', split(GDS_CD, '/')[1])
              ELSE concat(substr(GDS_CD, 1, 9), '-', substr(GDS_CD, 10, 2)) END AS sku,
         ETC1 AS shop_code, ORD_STATUS, ORD_QTY, ACT_QTY, ACT_DATE, PLN_DATE
  FROM pbo.moms.ui_gireport_detail
  WHERE PLN_DATE >= date_format(date_sub(current_date(), 45), 'yyyyMMdd')
    AND ORD_TYPE = '사내' AND STOCK_TRANSFER_CODE = 'M06'
    AND (lower(BRD_NM) LIKE 'musinsastandard%' OR BRD_NM LIKE '무신사 스탠다드%' OR BRD_NM LIKE '무신사스탠다드%')
    AND ORD_STATUS NOT IN ('출고취소')
)
SELECT gi.sku,
       CAST(s.shop_no AS STRING) AS shop_no,
       gi.shop_code,
       max(s.shop_nm) AS shop_nm,
       CAST(sum(CASE WHEN gi.ACT_QTY > 0 THEN gi.ACT_QTY ELSE 0 END) AS BIGINT) AS done_qty,
       CAST(sum(CASE WHEN gi.ORD_STATUS IN ('출고대기', '미출고', '가송장', '부분출고')
                     THEN greatest(gi.ORD_QTY - coalesce(gi.ACT_QTY, 0), 0) ELSE 0 END) AS BIGINT) AS pend_qty,
       min(CASE WHEN gi.ACT_QTY > 0 THEN gi.ACT_DATE END) AS first_dt,
       max(CASE WHEN gi.ACT_QTY > 0 THEN gi.ACT_DATE END) AS last_dt,
       min(CASE WHEN gi.ORD_STATUS IN ('출고대기', '미출고', '가송장', '부분출고') THEN gi.PLN_DATE END) AS pend_pln
FROM gi LEFT JOIN sh s ON s.shop_code = gi.shop_code AND s.rn = 1
GROUP BY gi.sku, s.shop_no, gi.shop_code
"""

HEADER = ["sku", "style", "color", "name", "team", "n_shops", "n_list", "done_qty", "pend_qty",
          "first_dt", "last_dt", "pend_pln", "shops"]
STORE_HEADER = ["shop_no", "shop_nm", "in_list", "done_qty", "pend_qty", "n_sku", "last_dt"]


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _kst_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) + _dt.timedelta(hours=9)


def fetch(sheets, log=print) -> list[list]:
    from soo.hero_ops import dbx_relay
    dbx_relay.configure(sheets, "dist", max_age_h=30)
    try:
        rows = dbx_relay.rows(DIST_SQL, log)
    finally:
        dbx_relay._cfg["sheets"] = None
    if len(rows) < MIN_ROWS:
        raise RuntimeError(f"분배 {len(rows)}행 < {MIN_ROWS} — 부분 결과 의심")
    return rows


def load_store_list(sheets) -> dict[str, str]:
    """부문 대시보드 '오프라인' 탭의 매장 표 → {shop_no: 매장명}. 헤더 행(E='거래액')을 라벨로 찾는다."""
    v = sheets.spreadsheets().values().get(spreadsheetId=DASH_ID, range=f"'{DASH_TAB}'!A1:E400").execute().get("values", [])
    start = next((i for i, r in enumerate(v) if len(r) > 4 and str(r[4]).strip() == "거래액"), None)
    if start is None:
        raise RuntimeError(f"'{DASH_TAB}' 탭에서 매장 표 헤더(E='거래액')를 못 찾음 — 탭 구조가 바뀌었다")
    out: dict[str, str] = {}
    for r in v[start + 1:]:
        nm = str(r[2]).strip() if len(r) > 2 else ""
        no = str(r[3]).strip() if len(r) > 3 else ""
        if not nm and not no:
            break                       # 첫 블록(실적 표)만 — 아래 반복 블록(전년·목표)은 같은 매장의 재등장
        if no.isdigit() and nm:
            out[no] = nm
    if len(out) < 20:
        raise RuntimeError(f"매장 목록 {len(out)}곳 — 기대치(40곳 안팎)와 다르다, 탭을 확인할 것")
    print(f"[stores] 부문 대시보드 매장 {len(out)}곳")
    return out


def load_names(sheets) -> dict[str, tuple[str, str]]:
    """아이템마스터 → {운영품번(품번-컬러): (상품명, 팀)}."""
    v = sheets.spreadsheets().values().get(spreadsheetId=MUTAN_SHEET, range=f"'{MUTAN_TAB}'!D12:I30000").execute().get("values", [])
    out = {}
    for r in v:
        def c(i): return str(r[i]).strip() if i < len(r) and r[i] is not None else ""
        if c(0) and c(0) not in out:
            out[c(0)] = (c(3), c(5))
    return out


def build(rows: list[list], stores: dict[str, str], names: dict) -> tuple[list[list], list[list], collections.Counter]:
    by_sku: dict[str, list] = collections.defaultdict(list)
    st = collections.defaultdict(lambda: {"nm": "", "done": 0, "pend": 0, "skus": set(), "last": ""})
    cnt = collections.Counter()
    for sku, shop_no, shop_code, shop_nm, done, pend, f, l, pp in rows:
        sku = str(sku or "").strip()
        if len(sku) < 10:
            cnt["bad_sku"] += 1
            continue
        key = str(shop_no or "").strip() or f"?{shop_code}"          # 매장 마스터 미매칭은 코드로 남긴다
        done, pend = int(float(done or 0)), int(float(pend or 0))
        by_sku[sku].append((key, done, pend, str(f or ""), str(l or ""), str(pp or "")))
        s = st[key]
        s["nm"] = stores.get(key) or (str(shop_nm or "").replace("무신사 스탠다드 ", "") if shop_nm else f"미매칭 코드 {shop_code}")
        s["done"] += done; s["pend"] += pend; s["skus"].add(sku); s["last"] = max(s["last"], str(l or ""))
        cnt["in_list" if key in stores else ("unmatched" if key.startswith("?") else "off_list")] += done
    grid = []
    for sku, xs in by_sku.items():
        nm, team = names.get(sku, ("", ""))
        got = [x for x in xs if x[1] > 0]
        grid.append([sku, sku[:9], sku[10:], nm, team, len(got), sum(1 for x in got if x[0] in stores),
                     sum(x[1] for x in xs), sum(x[2] for x in xs),
                     min((x[3] for x in got if x[3]), default=""), max((x[4] for x in got if x[4]), default=""),
                     min((x[5] for x in xs if x[5]), default=""),
                     # 매장별 "shop_no:출고:대기" — 화면 드릴다운용(행 폭을 고정하려고 한 칸에 접는다)
                     ",".join(f"{k}:{d}:{p}" for k, d, p, *_ in sorted(xs, key=lambda x: -x[1]))])
    grid.sort(key=lambda r: (r[10], r[7]), reverse=True)
    sgrid = [[k, v["nm"], "Y" if k in stores else "N", v["done"], v["pend"], len(v["skus"]), v["last"]]
             for k, v in sorted(st.items(), key=lambda kv: -kv[1]["done"])]
    for k, nm in stores.items():                 # 45일간 한 장도 안 받은 목록 매장도 보이게
        if k not in st:
            sgrid.append([k, nm, "Y", 0, 0, 0, ""])
    return grid, sgrid, cnt


def _write(sheets, tab: str, header: list, grid: list[list], label: str) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=APP_SHEET_ID, fields="sheets.properties(title,sheetId,gridProperties)").execute()
    hit = next((s["properties"] for s in meta["sheets"] if s["properties"]["title"] == tab), None)
    need = len(grid) + 500
    if hit is None:
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"addSheet": {"properties": {
            "title": tab, "hidden": True, "gridProperties": {"rowCount": need, "columnCount": len(header), "frozenRowCount": 2}}}}]}).execute()
    elif hit["gridProperties"]["rowCount"] < len(grid) + 2:
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": hit["sheetId"], "gridProperties": {"rowCount": need}}, "fields": "gridProperties.rowCount"}}]}).execute()
    body = [[label] + [""] * (len(header) - 1), header] + grid
    sheets.spreadsheets().values().clear(spreadsheetId=APP_SHEET_ID, range=f"'{tab}'!A1:{_col(len(header))}", body={}).execute()
    # ★RAW — 품번·shop_no 가 숫자로 추론돼 앞자리 0·형식이 바뀌지 않게([[CLAUDE 1-8]])
    sheets.spreadsheets().values().update(spreadsheetId=APP_SHEET_ID, range=f"'{tab}'!A1",
                                          valueInputOption="RAW", body={"values": body}).execute()
    back = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{tab}'!A3:A").execute().get("values", [])
    if len(back) != len(grid):
        raise RuntimeError(f"[{tab}] 되읽기 {len(back)}행 ≠ 기입 {len(grid)}행")
    print(f"[{tab}] 기입 {len(grid):,}행 · 되읽기 일치")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sheets = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))["sheets"]
    now = _kst_now()
    rows = fetch(sheets)
    stores = load_store_list(sheets)
    grid, sgrid, cnt = build(rows, stores, load_names(sheets))
    tot = sum(v for k, v in cnt.items() if k != "bad_sku") or 1
    print(f"[dist] 품번-컬러 {len(grid):,} · 매장 {len(sgrid)} · 출고 {tot:,}장 — 목록 매장 {cnt['in_list']/tot:.1%}"
          f" · 목록 밖 {cnt['off_list']:,} · 미매칭 {cnt['unmatched']:,} · 품번 이상 {cnt['bad_sku']}행")
    off = [s for s in sgrid if s[2] == "N" and s[3] >= 1000]
    if off:   # 목록에 없는데 많이 받는 곳 = 새 매장일 가능성 — 부문 대시보드 탭에 추가할 후보
        print("[dist] ⚠ 목록 밖인데 1,000장 이상 받은 곳: " + " · ".join(f"{s[1]}({s[0]}) {s[3]:,}" for s in off))
    if a.apply:
        label = (f"오프라인 분배 · WMS 출고 M06(물류센터→매장) · 최근 {LOOKBACK_DAYS}일 · 매장 기준=부문 대시보드 '오프라인' 탭"
                 f" · 생성 {now:%Y-%m-%d %H:%M} KST")
        _write(sheets, TAB, HEADER, grid, label)
        _write(sheets, STORE_TAB, STORE_HEADER, sgrid, label)
    else:
        print("(드라이런 — --apply 로 기입)")


if __name__ == "__main__":
    main()
