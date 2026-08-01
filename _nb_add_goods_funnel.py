# -*- coding: utf-8 -*-
"""노트북에 '상품 퍼널(노출·클릭·CTR)'과 '조회자 성·연령' 원천 추가 (멱등).

배경(2026-08-01, 사용자 지시): 공식 온사이트 퍼포먼스 대시보드(QuickSight)와 기준을 맞추기로 했고,
그중 **데이터 접근이 이미 되는 것부터** 붙인다.

원천 = `team.sales.goods_funnel_daily` (goods_no x 일자 x platform x gender x age_group)
  · impression_cnt / click_cnt / gv_cnt(조회 건수) / gv_user_cnt(조회 UV) / click_user_cnt
  · 공식 대시보드의 CTR(= 클릭수/노출수)과 같은 재료. platform='무신사'만 쓴다(29CM 제외).
  · 교차검증(2026-07-20~26 커브드팬츠): 노출 1,250,216 · 클릭 41,243 · CTR 3.30% ·
    조회UV 29,550 vs 기존 pdp_uv 28,943 → **1.02배**(두 원천이 같은 것을 세고 있음 확인).

추가 탭 2개:
  ① `상품퍼널기간`  = goods x 기간(YTD/MTD/WEEK/FWTD) 노출·클릭·조회(+전년) — 생성기가 히어로/STY로 롤업
  ② `상품성연령`    = 히어로 x 시즌 x 기간 x 성별 x 연령대 (조회자 구성). v_hero_map 사용.

실행: DBX_TOKEN=... python _nb_add_goods_funnel.py [--dry]
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
HOST = "https://musinsa-data-ws.cloud.databricks.com"
WPATH = "/Users/sooyoung.moon@musinsa.com/히어로 마스터 앱_실적"
ROOT = Path(__file__).resolve().parent
TOK = os.environ.get("DBX_TOKEN") or (Path.home() / ".databricks_pat").read_text().strip()

ANCHOR = 'print("[OK] PMKT경로상세 기록 완료")\n'

CELL = '''
# COMMAND ----------

# (6) 상품 퍼널(노출·클릭·CTR) + 조회자 성·연령 — 2026-08-01 신설.
#     공식 온사이트 퍼포먼스 대시보드(QuickSight)와 같은 재료로 맞추기 위한 원천.
#     `team.sales.goods_funnel_daily` = goods x 일자 x platform x 성별 x 연령대,
#     impression_cnt(노출) · click_cnt(클릭) · gv_cnt(조회 건수) · gv_user_cnt(조회 UV).
#     ★platform='무신사'만 (29CM 제외). 브랜드 컬럼이 없어 히어로 uid 필터(v_goods_filter)로 좁힌다.
#     ★CTR = 클릭/노출 (공식 정의와 동일). 조회UV는 기존 pdp_uv와 1.02배로 일치 확인(커브드 7/20~26).
_GF_FROM = """
  FROM team.sales.goods_funnel_daily g
  JOIN v_goods_filter gf ON CAST(g.goods_no AS STRING) = CAST(gf.goods_no AS STRING)
  WHERE g.platform = '무신사'
"""
_gf_union = " UNION ALL ".join(f"""
  SELECT '{nm}' AS period, CAST(g.goods_no AS STRING) AS goods_no,
         SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.impression_cnt ELSE 0 END) AS imp,
         SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.click_cnt ELSE 0 END) AS clk,
         SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.gv_cnt ELSE 0 END) AS gv,
         SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.gv_user_cnt ELSE 0 END) AS gv_uv,
         SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.impression_cnt ELSE 0 END) AS imp_ly,
         SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.click_cnt ELSE 0 END) AS clk_ly,
         SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.gv_cnt ELSE 0 END) AS gv_ly,
         SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.gv_user_cnt ELSE 0 END) AS gv_uv_ly
  {_GF_FROM.rstrip()}
    AND (g.dt BETWEEN '{s}' AND '{e}' OR g.dt BETWEEN '{sly}' AND '{ely}')
  GROUP BY CAST(g.goods_no AS STRING)""" for nm, s, e, sly, ely in _pmkt_periods)
insert_query_result("상품퍼널기간", spark.sql(_gf_union),
                    label=f"히어로 goods x 기간 노출·클릭·조회(+전년) · goods_funnel_daily(무신사) ~{date}")
print("[OK] 상품퍼널기간 기록 완료")

_HERO_MAP = globals().get("_HERO_MAP") or []
if _HERO_MAP:
    _demo_union = " UNION ALL ".join(f"""
      SELECT '{nm}' AS period, hm.hero AS hero, hm.season AS season,
             CASE WHEN g.gender IN ('남성','여성') THEN g.gender ELSE '미상' END AS gender,
             CASE WHEN g.age_group RLIKE '^[0-9]' THEN g.age_group ELSE '미상' END AS age_group,
             SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.impression_cnt ELSE 0 END) AS imp,
             SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.click_cnt ELSE 0 END) AS clk,
             SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.gv_user_cnt ELSE 0 END) AS gv_uv,
             SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.gv_user_cnt ELSE 0 END) AS gv_uv_ly
      FROM team.sales.goods_funnel_daily g
      JOIN v_hero_map hm ON CAST(g.goods_no AS STRING) = hm.goods_no
      WHERE g.platform = '무신사'
        AND (g.dt BETWEEN '{s}' AND '{e}' OR g.dt BETWEEN '{sly}' AND '{ely}')
      GROUP BY hm.hero, hm.season,
               CASE WHEN g.gender IN ('남성','여성') THEN g.gender ELSE '미상' END,
               CASE WHEN g.age_group RLIKE '^[0-9]' THEN g.age_group ELSE '미상' END""" for nm, s, e, sly, ely in _pmkt_periods)
    insert_query_result("상품성연령", spark.sql(_demo_union),
                        label=f"히어로 x 기간 x 성별 x 연령대 조회 구성(무신사) ~{date}")
    print("[OK] 상품성연령 기록 완료")
else:
    print("[스킵] 상품성연령 — 히어로 맵 없음(직전 시트값 유지)")
'''


def api(method, path, body=None):
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode() if body else None,
                                 method=method,
                                 headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_body": e.read().decode()[:500]}


def main():
    dry = "--dry" in sys.argv
    r = api("GET", f"/api/2.0/workspace/export?path={urllib.parse.quote(WPATH)}&format=SOURCE")
    if "_err" in r:
        print("EXPORT 실패:", r["_err"], r.get("_body"))
        return 1
    src = base64.b64decode(r["content"]).decode("utf-8")
    (ROOT / "_nb_workspace_pre_goodsfunnel.py").write_text(src, encoding="utf-8")
    if "상품퍼널기간" in src:
        print("이미 반영됨 — 변경 없음")
        return 0
    if ANCHOR not in src:
        print("[중단] 앵커(PMKT경로상세 print)를 못 찾음 — _nb_add_week_history.py 먼저 적용할 것")
        return 1
    i = src.index(ANCHOR) + len(ANCHOR)
    out = src[:i] + CELL + src[i:]
    (ROOT / "_nb_live_after_goodsfunnel.py").write_text(out, encoding="utf-8")
    print(f"셀 추가 준비 완료 (+{len(CELL.splitlines())}줄)")
    if dry:
        print("--dry: 워크스페이스 반영 안 함")
        return 0
    res = api("POST", "/api/2.0/workspace/import", {
        "path": WPATH, "format": "SOURCE", "language": "PYTHON", "overwrite": True,
        "content": base64.b64encode(out.encode("utf-8")).decode()})
    if "_err" in res:
        print("IMPORT 실패:", res["_err"], res.get("_body"))
        return 1
    print("노트북 반영 완료 — 다음 실행부터 상품퍼널기간·상품성연령 탭이 생긴다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
