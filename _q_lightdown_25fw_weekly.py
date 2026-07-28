# -*- coding: utf-8 -*-
"""25FW 시티레저 라이트다운 — 2025-08-04(8월 1주) 시작 주간 온/오프 판매 시계열.
   마스터앱에 없는 조회(작년 시즌·전용 uid 세트)라 SQL warehouse로 직접 뽑는다.
   채널 정의는 노트북(히어로 마스터 앱_실적)과 동일: 온라인=orders_merged, 오프라인=pos_order_sales(offline/selectshop).
   사용: python _q_lightdown_25fw_weekly.py
"""
import json
import pathlib
import time

import requests

PAT = (pathlib.Path.home() / ".databricks_pat").read_text(encoding="utf-8").strip()
HOST = "https://musinsa-data-ws.cloud.databricks.com"
WH = "c0ee970a9c3ed562"

# 사용자 제공 ★주력 3 STY (2026-07-27 시트 기준) — Main 후디드 + Sub1 시어 + Sub2 우먼즈.
#   ★첫 실행 때 MMDDJAZ01 9개만 받아 우먼즈·시어가 통째로 빠졌었다. 24 uid가 정본.
STY_UIDS = {
    "MMDDJAZ01": ("Main · 후디드", [4356794, 4356796, 5148062, 5148063, 5148059, 5148061,
                                   5148065, 5148060, 4356795, 4356798, 5148068, 5148064, 5148066]),
    "MMEDJ9A04": ("Sub1 · 시어", [5215535, 5215534, 5215532, 5215538, 5215533, 5215536, 5215537]),
    "MWEDJ9B57": ("Sub2 · 우먼즈", [5162178, 5162177, 5162181, 5162180]),
}
UIDS = [u for _lab, us in STY_UIDS.values() for u in us]
START, END = "20250804", "20260202"          # 8월 1주차부터 시즌 끝(다음해 2월 초)까지

_UL = ",".join(str(u) for u in UIDS)

META_Q = f"""
SELECT goods_no, ANY_VALUE(style_no) style_no, ANY_VALUE(goods_nm) goods_nm,
       ANY_VALUE(release_season_type) release_season, ANY_VALUE(season) sell_season
FROM gspread.musinsastandard.mutandard_goods_meta_v2
WHERE goods_no IN ({_UL})
GROUP BY goods_no ORDER BY goods_no
"""

WEEK_Q = f"""
WITH cal AS (
  SELECT DISTINCT dt, TO_DATE(dt,'yyyyMMdd') AS d
  FROM datamart.datamart.calendar WHERE dt BETWEEN '{START}' AND '{END}'
),
online AS (
  SELECT 'Online' AS channel, c.d, om.goods_no,
         om.sell_sub_clm_amt AS gmv, om.sell_sub_clm_qty AS qty
  FROM datamart.datamart.orders_merged om
  JOIN cal c ON om.ord_state_date = c.dt
  WHERE om.state_order = TRUE
    AND om.goods_no IN ({_UL})
    AND om.com_id NOT IN ('musinsa','musinsa_event')
),
shops AS (
  SELECT DISTINCT shop_no FROM musinsa.order_group.shop
  WHERE LOWER(shop_type) IN ('offline','selectshop')
),
offline AS (
  SELECT 'Offline' AS channel, c.d, pos.goods_no,
         IF(pos.sales_type='SALE',1,-1) * pos.sales_price AS gmv,
         IF(pos.sales_type='SALE',1,-1) * pos.qty AS qty
  FROM musinsa.order_group.pos_order_sales pos
  JOIN cal c ON DATE(pos.sales_date) = c.d
  JOIN shops s ON pos.shop_no = s.shop_no
  WHERE pos.goods_no IN ({_UL})
),
u AS (SELECT * FROM online UNION ALL SELECT * FROM offline)
SELECT channel,
       DATE_ADD(d, -((DATEDIFF(d, DATE'2025-08-04')) % 7)) AS week_start,
       SUM(gmv) AS gmv, SUM(qty) AS qty
FROM u
WHERE d >= DATE'2025-08-04'
GROUP BY channel, DATE_ADD(d, -((DATEDIFF(d, DATE'2025-08-04')) % 7))
ORDER BY week_start, channel
"""

# 스타일(품번)별 주간 — 어떤 스타일이 끌었는지 분해용
WEEK_STY_Q = WEEK_Q.replace(
    "SELECT channel,\n       DATE_ADD",
    "SELECT channel, goods_no,\n       DATE_ADD").replace(
    "GROUP BY channel, DATE_ADD", "GROUP BY channel, goods_no, DATE_ADD").replace(
    "ORDER BY week_start, channel", "ORDER BY week_start, channel, goods_no")


def runsql(q, timeout=900):
    r = requests.post(f"{HOST}/api/2.0/sql/statements",
                      headers={"Authorization": f"Bearer {PAT}"},
                      json={"warehouse_id": WH, "statement": q, "wait_timeout": "30s",
                            "disposition": "INLINE", "format": "JSON_ARRAY"}, timeout=60)
    d = r.json()
    sid, st, t0 = d.get("statement_id"), d.get("status", {}).get("state"), time.time()
    while st in ("PENDING", "RUNNING") and time.time() - t0 < timeout:
        time.sleep(4)
        d = requests.get(f"{HOST}/api/2.0/sql/statements/{sid}",
                         headers={"Authorization": f"Bearer {PAT}"}, timeout=60).json()
        st = d.get("status", {}).get("state")
    if st != "SUCCEEDED":
        raise RuntimeError(f"SQL {st}: {json.dumps(d.get('status', {}), ensure_ascii=False)[:500]}")
    cols = [c["name"] for c in d["manifest"]["schema"]["columns"]]
    return cols, (d.get("result", {}).get("data_array") or [])


if __name__ == "__main__":
    out = {}
    try:
        c, rows = runsql(META_Q)
        out["meta"] = [dict(zip(c, r)) for r in rows]
        print(f"[메타] {len(rows)}건")
        for r in rows:
            print("   ", " | ".join(str(x) for x in r))
    except Exception as e:
        print(f"[메타] 실패(무시하고 진행): {type(e).__name__}: {e}")
        out["meta"] = []

    c, rows = runsql(WEEK_Q)
    out["weekly"] = [dict(zip(c, r)) for r in rows]
    print(f"\n[주간] {len(rows)}행")
    for r in rows:
        print("   ", " | ".join(str(x) for x in r))

    c, rows = runsql(WEEK_STY_Q)
    out["weekly_by_goods"] = [dict(zip(c, r)) for r in rows]
    print(f"\n[주간x품번] {len(rows)}행")

    out["sty_uids"] = {k: {"label": v[0], "uids": v[1]} for k, v in STY_UIDS.items()}
    pathlib.Path("_lightdown_25fw_weekly.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n저장: _lightdown_25fw_weekly.json")
