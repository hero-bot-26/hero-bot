# -*- coding: utf-8 -*-
"""노트북에 'PMKT경로주차' 탭 추가 — goods x 유입경로(prev_path1) x ISO주차.
   IMC 성과 '유입 경로 구성'의 전주비(WoW)를 실제로 산출하기 위한 원천.
   현재는 경로별 데이터가 기간(YTD/MTD/WEEK)+전년만 있어 전주비 모드에서 '–'로 뜬다.
   PMKT주차와 동일한 주차 정의(YEAR/WEEKOFYEAR, week_start/week_end)를 써서 생성기가 같은
   일평균 정규화 로직을 그대로 재사용하게 한다.
   사용: python _nb_add_pathweek.py   (입력 _nb_live_updated.py → 같은 파일에 덮어씀)
"""
import io
from pathlib import Path

P = Path("_nb_live_updated.py")
s = io.open(P, encoding="utf-8").read()

ANCHOR = '''insert_query_result("PMKT경로기간", spark.sql(_path_union), label=f"히어로 goods x 경로(prev_path1) x 기간 유입·전환·YoY(direct) ~{date}")
print("[OK] PMKT경로기간 기록 완료")'''

ADD = '''insert_query_result("PMKT경로기간", spark.sql(_path_union), label=f"히어로 goods x 경로(prev_path1) x 기간 유입·전환·YoY(direct) ~{date}")
print("[OK] PMKT경로기간 기록 완료")

# COMMAND ----------

# (4) 유입경로 x 주차 — 경로별 '전주비(WoW)' 원천. 경로 데이터가 기간+전년뿐이라 성과뷰 전주비가 '–'로 떴다.
#     주차 정의는 PMKT주차와 동일(YEAR/WEEKOFYEAR + week_start/week_end) → 생성기가 같은 일평균 정규화를 재사용.
#     최근 12주만(경로 축이 붙어 행이 크게 늘어남 — 전주비엔 최근 2주면 충분).
_path_week_from = (datetime.strptime(date, "%Y%m%d") - timedelta(weeks=12)).strftime("%Y%m%d")
pmkt_path_week_query = f"""
WITH base AS (
  SELECT CAST(p.goods_no AS STRING) AS goods_no,
         COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타') AS path,
         TO_DATE(p.dt,'yyyyMMdd') AS d, p.pdp_uv_cnt, p.purchase_uv_cnt
  {_PMKT_FROM.rstrip()}
    AND p.dt BETWEEN '{_path_week_from}' AND '{date}'
)
SELECT goods_no, path,
       YEAR(d) AS yyyy, WEEKOFYEAR(d) AS week_no, MIN(d) AS week_start, MAX(d) AS week_end,
       SUM(pdp_uv_cnt) AS pdp_uv, SUM(purchase_uv_cnt) AS buy_uv
FROM base
GROUP BY goods_no, path, YEAR(d), WEEKOFYEAR(d)
ORDER BY goods_no, path, yyyy, week_no
"""
insert_query_result("PMKT경로주차", spark.sql(pmkt_path_week_query), label=f"히어로 goods x 경로 x 주차 유입·전환(direct) 최근12주~{date}")
print("[OK] PMKT경로주차 기록 완료")'''

assert s.count(ANCHOR) == 1, f"앵커 {s.count(ANCHOR)}건 (1이어야 함)"
io.open(P, "w", encoding="utf-8").write(s.replace(ANCHOR, ADD))
print("PMKT경로주차 셀 추가 완료 →", P)
