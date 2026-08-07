# -*- coding: utf-8 -*-
"""오프라인 매장별 탭(`오프라인매장별` / `오프라인매장총계`)을 즉시 채운다.

노트북 셀 `(5) 오프라인 매장별`과 **같은 쿼리**를 SQL warehouse로 돌려 시트에 쓴다.
평시엔 노트북(09:30 잡)이 채우고, 이 스크립트는 신규 도입·복구·검증용이다.
★쿼리를 노트북 셀과 동일하게 유지할 것(선례: _populate_pdp_daily.py).

사용:
  DATABRICKS_HOST=... DATABRICKS_TOKEN=... python _populate_offline_shops.py [--dry]
"""
import datetime
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from soo.auth import build_services, get_credentials
from soo.hero_ops.sales_rollup import SALES_SHEET_ID

ROOT = Path(__file__).resolve().parent
WAREHOUSE = "c0ee970a9c3ed562"
BRANDS = ("('musinsastandard','musinsastandardhome',"
          "'musinsastandardwoman','musinsastandardkids')")


def _api(path, data=None):
    host, tok = os.environ["DATABRICKS_HOST"], os.environ["DATABRICKS_TOKEN"]
    req = urllib.request.Request(
        host + path, data=json.dumps(data).encode() if data else None,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))


def dbx_sql(q):
    r = _api("/api/2.0/sql/statements",
             {"warehouse_id": WAREHOUSE, "statement": q, "wait_timeout": "50s"})
    sid = r["statement_id"]
    while r["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(5)
        r = _api(f"/api/2.0/sql/statements/{sid}")
    if r["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(json.dumps(r["status"], ensure_ascii=False)[:500])
    cols = [c["name"] for c in r["manifest"]["schema"]["columns"]]
    return cols, (r["result"].get("data_array") or [])


def periods(today):
    """노트북 params와 같은 창(YTD/MTD/WEEK/FWTD + 전년). end=전일."""
    e = today - datetime.timedelta(days=1)
    f = lambda d: d.strftime("%Y%m%d")                                   # noqa: E731
    ly = lambda d: d.replace(year=d.year - 1)                            # noqa: E731
    mon = e - datetime.timedelta(days=e.weekday())
    return [
        ("YTD", f(datetime.date(e.year, 1, 1)), f(e), f(datetime.date(e.year - 1, 1, 1)), f(ly(e))),
        ("MTD", f(e.replace(day=1)), f(e), f(ly(e.replace(day=1))), f(ly(e))),
        ("WEEK", f(mon), f(e), f(ly(mon)), f(ly(e))),
        ("FWTD", f(datetime.date(e.year, 7, 1)), f(e), f(datetime.date(e.year - 1, 7, 1)), f(ly(e))),
    ]


def build_query(hero_only, pers, goods_filter_sql):
    parts = []
    for nm, s, e, sly, ely in pers:
        cur = f"DATE(pos.sales_date) BETWEEN TO_DATE('{s}','yyyyMMdd') AND TO_DATE('{e}','yyyyMMdd')"
        lyw = f"DATE(pos.sales_date) BETWEEN TO_DATE('{sly}','yyyyMMdd') AND TO_DATE('{ely}','yyyyMMdd')"
        gfj = "  JOIN gf ON CAST(pos.goods_no AS STRING) = CAST(gf.goods_no AS STRING)" if hero_only else ""
        mj = "  LEFT JOIN m ON CAST(pos.goods_no AS STRING) = CAST(m.goods_no AS STRING)" if hero_only else ""
        sty = ("COALESCE(NULLIF(SPLIT(m.style_no,'-')[0],''), CAST(pos.goods_no AS STRING))"
               if hero_only else "'전체'")
        parts.append(f"""
  SELECT '{nm}' AS period, CAST(pos.shop_no AS STRING) AS shop_no,
         ANY_VALUE(sh.shop_nm) AS shop_nm, ANY_VALUE(sh.shop_region) AS shop_region,
         {sty} AS sty,
         SUM(CASE WHEN {cur} THEN IF(pos.sales_type='SALE',1,-1)*pos.sales_price ELSE 0 END) AS gmv,
         SUM(CASE WHEN {cur} THEN IF(pos.sales_type='SALE',1,-1)*pos.qty ELSE 0 END) AS qty,
         SUM(CASE WHEN {lyw} THEN IF(pos.sales_type='SALE',1,-1)*pos.sales_price ELSE 0 END) AS gmv_ly,
         SUM(CASE WHEN {lyw} THEN IF(pos.sales_type='SALE',1,-1)*pos.qty ELSE 0 END) AS qty_ly
  FROM musinsa.order_group.pos_order_sales pos
  JOIN v_shop_list sl ON pos.shop_no = sl.shop_no
  JOIN musinsa.order_group.shop sh ON pos.shop_no = sh.shop_no
{gfj}
{mj}
  WHERE LOWER(pos.brand_id) IN {BRANDS}
    AND (({cur}) OR ({lyw}))
  GROUP BY CAST(pos.shop_no AS STRING), {sty}""")
    pre = ["""v_shop_list AS (SELECT DISTINCT shop_no FROM musinsa.order_group.shop
   WHERE LOWER(shop_type) IN ('offline','selectshop') OR shop_no=68)"""]
    if hero_only:
        pre.append(f"gf AS ({goods_filter_sql})")
        pre.append("""m AS (SELECT goods_no, style_no FROM (SELECT goods_no, style_no,
     ROW_NUMBER() OVER (PARTITION BY goods_no ORDER BY md_nm, team) rn
     FROM gspread.musinsastandard.mutandard_goods_meta_v2 WHERE goods_no IS NOT NULL) x WHERE rn=1)""")
    return ("WITH " + ",\n".join(pre) + "\nSELECT * FROM ("
            + " UNION ALL ".join(parts) + ") t WHERE gmv <> 0 OR gmv_ly <> 0")


def hero_goods():
    """히어로 uid 집합 = 26FW ∪ 26SS 매핑(노트북 GOODS_FILTER와 같은 모집단)."""
    uids = set()
    for f in ("hero_goods_26fw.json", "hero_goods_26ss.json"):
        p = ROOT / f
        if p.exists():
            uids |= set((json.loads(p.read_text(encoding="utf-8")).get("goods_to_hero") or {}).keys())
    return sorted(uids)


def write_tab(sheets, tab, cols, rows, label):
    body = [[label] + [""] * (len(cols) - 1), cols] + [list(r) for r in rows]
    sheets.spreadsheets().values().clear(
        spreadsheetId=SALES_SHEET_ID, range=f"'{tab}'").execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=SALES_SHEET_ID, range=f"'{tab}'!A1",
        valueInputOption="RAW", body={"values": body}).execute()
    print(f"[OK] {tab}: {len(rows):,} rows x {len(cols)} cols")


def ensure_tab(sheets, tab):
    meta = sheets.spreadsheets().get(spreadsheetId=SALES_SHEET_ID,
                                     fields="sheets.properties.title").execute()
    if tab not in {s["properties"]["title"] for s in meta["sheets"]}:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SALES_SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]}).execute()
        print(f"  탭 신규 생성: {tab}")


def main():
    dry = "--dry" in sys.argv
    today = datetime.date.today()
    pers = periods(today)
    uids = hero_goods()
    print(f"기준 {today} · 기간 {[p[0] for p in pers]} · 히어로 uid {len(uids):,}")
    gf = "SELECT * FROM (VALUES " + ",".join(f"('{u}')" for u in uids) + ") AS t(goods_no)"
    date_lbl = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    jobs = [("오프라인매장별", True, f"히어로 품번 x 매장 오프라인 매출(POS) ~{date_lbl}"),
            ("오프라인매장총계", False, f"매장 오프라인 매출 총계(무탠 전 상품, 비중 분모) ~{date_lbl}")]
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sheets = svc["sheets"]
    for tab, hero_only, label in jobs:
        cols, rows = dbx_sql(build_query(hero_only, pers, gf))
        print(f"  {tab}: {len(rows):,}행 조회")
        if dry:
            for r in rows[:3]:
                print("   ", r)
            continue
        ensure_tab(sheets, tab)
        write_tab(sheets, tab, cols, rows, label)


if __name__ == "__main__":
    sys.exit(main())
