# -*- coding: utf-8 -*-
"""라이브 노트북(`히어로 마스터 앱_실적`)에 '주차 스냅샷 + 유입경로 상세' 원천을 추가한다 (멱등).

배경(2026-08-01, 사용자 요청):
  ① 유입 경로를 대분류(prev_path1)만 보여줘서 '메인-세일/발매'로 흩어진 기획전 유입을 볼 수 없었다
     → 중분류(prev_path2)까지 내려가는 상세 원천이 필요.
  ② IMC 성과가 '최근 완료주 vs 직전주'만 보여줘서 과거 주차 회고가 불가.
     → 주차별 성과 + 주차별 경로 구성을, **26년 1월부터**(YoY 비교는 25년 1월부터) 실을 원천이 필요.

바꾸는 것 4가지:
  P1  `_히어로UID` 탭을 읽을 때 uid만 뽑던 것 → (uid, hero, season)까지 보관(_HERO_MAP).
  P2  `v_hero_map` 임시뷰 신설(+캐시). 경로 집계를 goods가 아니라 **히어로 단위**로 내리기 위함
      — goods x path x 83주는 40만행이 넘어 시트에 못 싣는다(히어로 단위면 2~3만행).
  P3  `PMKT주차` 원천 기간 20260101 → **20250101**(주차 YoY용).
  P4  `PMKT경로주차`를 히어로 단위 x 2025-01-01~ 로 교체(기존=goods 단위 최근 12주).
  P5  `PMKT경로상세` 탭 신설 — 히어로 x 시즌 x 기간 x 대분류 x **중분류** + 전년.

★가드: `_HERO_MAP`이 비면(탭 읽기 실패) P4/P5 셀은 시트를 **건드리지 않고 스킵**한다(조용한 0 방지).

실행: DBX_TOKEN=... python _nb_add_week_history.py [--dry]
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


def api(method, path, body=None):
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode() if body else None,
                                 method=method,
                                 headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_body": e.read().decode()[:500]}


# ── P1: _히어로UID 탭 → uid + hero/season ────────────────────────────────────
P1_OLD = """      _uids = sorted({r[0].strip() for r in _uid_rows if r and r[0].strip().isdigit()}, key=int)
  except Exception as _euid:
      _uids = []
"""
P1_NEW = """      _uids = sorted({r[0].strip() for r in _uid_rows if r and r[0].strip().isdigit()}, key=int)
      # ★히어로 맵(uid→hero/season) — 유입경로를 goods가 아닌 '히어로' 단위로 집계하려고 함께 읽는다
      #   (goods x 경로 x 83주 = 40만행 초과라 시트에 못 싣는다. 히어로 단위면 2~3만행).
      #   같은 uid가 두 시즌에 걸리면(캐리오버) 두 행 다 유지 = 시즌 레인 각각에 계상.
      _HERO_MAP = sorted({(r[0].strip(), r[1].strip(), r[2].strip()) for r in _uid_rows
                          if len(r) >= 3 and r[0].strip().isdigit() and r[1].strip() and r[2].strip()})
  except Exception as _euid:
      _uids, _HERO_MAP = [], []
"""

# ── P2: v_hero_map 뷰 ────────────────────────────────────────────────────────
P2_ANCHOR = """  spark.sql(f"CREATE OR REPLACE TEMP VIEW v_goods_filter AS SELECT DISTINCT goods_no FROM (VALUES {GOODS_FILTER}) AS t(goods_no)")
"""
P2_ADD = """  # ★히어로 맵 뷰(2026-08-01) — 경로 주차/상세를 히어로 단위로 내리는 조인 키.
  _HM_VALUES = ",".join("('%s','%s','%s')" % (u, h.replace("'", "''"), s) for u, h, s in (_HERO_MAP or []))
  if _HM_VALUES:
      spark.sql(f"CREATE OR REPLACE TEMP VIEW v_hero_map AS SELECT * FROM (VALUES {_HM_VALUES}) AS t(goods_no, hero, season)")
      spark.sql("CACHE TABLE v_hero_map")
      print("v_hero_map = %d행(uid x 시즌)" % len(_HERO_MAP))
  else:
      print("[주의] 히어로 맵이 비었다 — PMKT경로주차/경로상세 셀은 시트를 건드리지 않고 스킵된다")
"""

# ── P3: PMKT주차 기간 확장(주차 YoY) ────────────────────────────────────────
P3_OLD = """    AND p.dt BETWEEN '20260101' AND '{date}'
)
SELECT goods_no, ANY_VALUE(style_no) AS style_no,"""
P3_NEW = """    AND p.dt BETWEEN '20250101' AND '{date}'
)
SELECT goods_no, ANY_VALUE(style_no) AS style_no,"""

P3_LABEL_OLD = 'label=f"히어로 goods x 주차 PMKT 퍼널(direct) 2026~{date}"'
P3_LABEL_NEW = 'label=f"히어로 goods x 주차 PMKT 퍼널(direct) 2025~{date}"'

# ── P4/P5: 경로 주차(히어로 단위·2025~) + 경로 상세(중분류) ──────────────────
P45_OLD_START = "# (4) 유입경로 x 주차 — 경로별 '전주비(WoW)' 원천."
P45_OLD_END = 'print("[OK] PMKT경로주차 기록 완료")\n'

P45_NEW = '''# (4) 유입경로 x 주차 — ★히어로 단위 x 2025-01-01~ (2026-08-01 개편).
#     예전엔 goods 단위 최근 12주라 '전주비' 말고는 쓸 데가 없었다. 성과 탭 '주차 스냅샷'(과거 주차 회고)을
#     26년 1월부터 보려면 주차 이력이 통째로 필요한데, goods x 경로 x 83주는 40만행이 넘어 시트에 못 싣는다.
#     → 조인을 v_hero_map으로 바꿔 **히어로 x 시즌 x 경로 x 주차**(2~3만행)로 내린다. 전주비도 그대로 나온다.
#     주차 정의는 PMKT주차와 동일(YEAR/WEEKOFYEAR + week_start/week_end) → 생성기가 같은 일평균 정규화를 재사용.
_HERO_MAP = globals().get("_HERO_MAP") or []      # 이 셀만 따로 돌릴 때 NameError 방지
_PMKT_FROM_HERO = """
  FROM team.sales.pdp_path_daily_summary_v p
  JOIN v_hero_map hm ON CAST(p.goods_no AS STRING) = hm.goods_no
  WHERE p.path_type = 'direct'
    AND LOWER(p.brand) IN ('musinsastandard','musinsastandardwoman','musinsastandardkids','musinsastandardhome')
    AND LOWER(p.com_id) NOT IN ('musinsa','musinsa_event')
"""
if _HERO_MAP:
    pmkt_path_week_query = f"""
    WITH base AS (
      SELECT hm.hero AS hero, hm.season AS season,
             COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타') AS path,
             TO_DATE(p.dt,'yyyyMMdd') AS d, p.pdp_uv_cnt, p.purchase_uv_cnt, p.gmv
      {_PMKT_FROM_HERO.rstrip()}
        AND p.dt BETWEEN '20250101' AND '{date}'
    )
    SELECT hero, season, path,
           YEAR(d) AS yyyy, WEEKOFYEAR(d) AS week_no, MIN(d) AS week_start, MAX(d) AS week_end,
           SUM(pdp_uv_cnt) AS pdp_uv, SUM(purchase_uv_cnt) AS buy_uv, SUM(gmv) AS gmv
    FROM base
    GROUP BY hero, season, path, YEAR(d), WEEKOFYEAR(d)
    ORDER BY hero, season, path, yyyy, week_no
    """
    insert_query_result("PMKT경로주차", spark.sql(pmkt_path_week_query),
                        label=f"히어로 x 시즌 x 경로(prev_path1) x 주차 유입·전환·거래액(direct) 2025~{date}")
    print("[OK] PMKT경로주차 기록 완료")
else:
    print("[스킵] PMKT경로주차 — 히어로 맵 없음(직전 시트값 유지)")

# COMMAND ----------

# (5) 유입경로 상세 — ★중분류(prev_path2)까지. 히어로 x 시즌 x 기간 x 대분류 x 중분류 + 전년(YoY).
#     배경: 대분류만 보면 온라인팀 기획전 유입이 '메인-세일/발매'·'기타'로 흩어져 보이지 않는다
#     (캠페인/기획전 라벨은 월 100UV 수준). 중분류를 펼쳐야 '세일/발매/추천/랭킹'이 구분된다.
#     기간·필터는 PMKT기간과 동일(_pmkt_periods) → 누계(YTD)는 1/1~, 전년은 25년 1/1~ 동일 창.
if _HERO_MAP:
    _path_dtl_union = " UNION ALL ".join(f"""
      SELECT '{nm}' AS period, hm.hero AS hero, hm.season AS season,
             COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타') AS path,
             COALESCE(NULLIF(TRIM(p.prev_path2),''),'기타') AS path2,
             SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.pdp_uv_cnt ELSE 0 END) AS pdp_uv,
             SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.purchase_uv_cnt ELSE 0 END) AS buy_uv,
             SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.gmv ELSE 0 END) AS gmv,
             SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.pdp_uv_cnt ELSE 0 END) AS pdp_uv_ly,
             SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.purchase_uv_cnt ELSE 0 END) AS buy_uv_ly
      {_PMKT_FROM_HERO.rstrip()}
        AND (p.dt BETWEEN '{s}' AND '{e}' OR p.dt BETWEEN '{sly}' AND '{ely}')
      GROUP BY hm.hero, hm.season, COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타'),
               COALESCE(NULLIF(TRIM(p.prev_path2),''),'기타')""" for nm, s, e, sly, ely in _pmkt_periods)
    insert_query_result("PMKT경로상세", spark.sql(_path_dtl_union),
                        label=f"히어로 x 기간 x 경로 대분류/중분류(prev_path2) 유입·전환·YoY(direct) ~{date}")
    print("[OK] PMKT경로상세 기록 완료")
else:
    print("[스킵] PMKT경로상세 — 히어로 맵 없음(직전 시트값 유지)")
'''


def main():
    dry = "--dry" in sys.argv
    r = api("GET", f"/api/2.0/workspace/export?path={urllib.parse.quote(WPATH)}&format=SOURCE")
    if "_err" in r:
        print("EXPORT 실패:", r["_err"], r.get("_body"))
        return 1
    src = base64.b64decode(r["content"]).decode("utf-8")
    (ROOT / "_nb_workspace_pre_weekhist.py").write_text(src, encoding="utf-8")
    print(f"백업 저장: _nb_workspace_pre_weekhist.py ({len(src.splitlines())}줄)")

    out, done, skip = src, [], []

    def patch(name, old, new, must=True):
        nonlocal out
        if new in out:
            skip.append(name)
            return
        if old not in out:
            if must:
                raise SystemExit(f"[중단] 앵커 못 찾음: {name}")
            skip.append(name + "(앵커없음)")
            return
        out = out.replace(old, new, 1)
        done.append(name)

    patch("P1 _히어로UID→hero/season", P1_OLD, P1_NEW)
    if "v_hero_map" not in out:
        out = out.replace(P2_ANCHOR, P2_ANCHOR + P2_ADD, 1)
        done.append("P2 v_hero_map 뷰")
    else:
        skip.append("P2 v_hero_map 뷰")
    patch("P3 PMKT주차 2025~", P3_OLD, P3_NEW)
    patch("P3b PMKT주차 라벨", P3_LABEL_OLD, P3_LABEL_NEW)

    # P4/P5 — 기존 (4)셀 통째 교체
    if "PMKT경로상세" in out:
        skip.append("P4/P5 경로주차·경로상세")
    else:
        i = out.index(P45_OLD_START)
        j = out.index(P45_OLD_END, i) + len(P45_OLD_END)
        out = out[:i] + P45_NEW + out[j:]
        done.append("P4/P5 경로주차(히어로·2025~)·경로상세(중분류)")

    print("적용:", " · ".join(done) or "없음")
    print("스킵(이미 적용):", " · ".join(skip) or "없음")
    (ROOT / "_nb_live_after_weekhist.py").write_text(out, encoding="utf-8")
    if not done:
        print("변경 없음 — import 생략")
        return 0
    if dry:
        print("--dry: 워크스페이스 반영 안 함 (_nb_live_after_weekhist.py 확인)")
        return 0
    res = api("POST", "/api/2.0/workspace/import", {
        "path": WPATH, "format": "SOURCE", "language": "PYTHON", "overwrite": True,
        "content": base64.b64encode(out.encode("utf-8")).decode()})
    if "_err" in res:
        print("IMPORT 실패:", res["_err"], res.get("_body"))
        return 1
    print("노트북 반영 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
