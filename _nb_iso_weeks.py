# -*- coding: utf-8 -*-
"""주차 버킷을 ISO 주(월요일 시작)로 교정한다 (멱등).

배경(2026-08-01): 주차 집계가 `YEAR(d) + WEEKOFYEAR(d)`였는데, **12/29~31은 WEEKOFYEAR가 1**이라
그 해 'W1' 버킷에 1월 초와 12월 말이 함께 접혔다. 2026년까지는 데이터가 그 구간에 없어 안 드러났지만,
주차 이력을 2025-01-01까지 늘리는 순간 **2025 W1 = 1/1~1/5 + 12/29~12/31**(8일)로 섞여
2026 W1의 전년비가 부풀려진다. 연말이 되면 2026 W1도 같은 식으로 오염된다.

교정: **주 = 그 날짜가 속한 월요일**로 접고, 라벨 연도는 ISO 규칙(월요일+3일=목요일의 연도)을 쓴다.
  wm      = DATE_SUB(d, MOD(DAYOFWEEK(d)+5, 7))     -- 월요일
  yyyy    = YEAR(DATE_ADD(wm, 3))                   -- ISO 주 기준 연도
  week_no = WEEKOFYEAR(wm)
검증(SQL warehouse): 2025-12-29~2026-01-04 → 2026 W1 · 2024-12-30~2025-01-05 → 2025 W1 · 2026-07-27~31 → 2026 W31.

대상 = `PMKT주차`(goods x 주차) · `PMKT경로주차`(히어로 x 경로 x 주차).
실행: DBX_TOKEN=... python _nb_iso_weeks.py [--dry]
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

WM = "DATE_SUB(TO_DATE(p.dt,'yyyyMMdd'), MOD(DAYOFWEEK(TO_DATE(p.dt,'yyyyMMdd'))+5, 7)) AS wm"

# ── PMKT주차 ────────────────────────────────────────────────────────────────
A_OLD = """         TO_DATE(p.dt,'yyyyMMdd') AS d, p.pdp_uv_cnt, p.purchase_uv_cnt, p.qty, p.gmv, p.prev_path1
  {_PMKT_FROM.rstrip()}
    AND p.dt BETWEEN '20250101' AND '{date}'
)
SELECT goods_no, ANY_VALUE(style_no) AS style_no,
       YEAR(d) AS yyyy, WEEKOFYEAR(d) AS week_no, MIN(d) AS week_start, MAX(d) AS week_end,"""
A_NEW = """         TO_DATE(p.dt,'yyyyMMdd') AS d, """ + WM + """, p.pdp_uv_cnt, p.purchase_uv_cnt, p.qty, p.gmv, p.prev_path1
  {_PMKT_FROM.rstrip()}
    AND p.dt BETWEEN '20250101' AND '{date}'
)
SELECT goods_no, ANY_VALUE(style_no) AS style_no,
       YEAR(DATE_ADD(wm,3)) AS yyyy, WEEKOFYEAR(wm) AS week_no, MIN(d) AS week_start, MAX(d) AS week_end,"""

A_OLD2 = """FROM base
GROUP BY goods_no, YEAR(d), WEEKOFYEAR(d)
ORDER BY goods_no, yyyy, week_no"""
A_NEW2 = """FROM base
GROUP BY goods_no, wm
ORDER BY goods_no, yyyy, week_no"""

# ── PMKT경로주차(히어로 단위) ───────────────────────────────────────────────
B_OLD = """             TO_DATE(p.dt,'yyyyMMdd') AS d, p.pdp_uv_cnt, p.purchase_uv_cnt, p.gmv
      {_PMKT_FROM_HERO.rstrip()}
        AND p.dt BETWEEN '20250101' AND '{date}'
    )
    SELECT hero, season, path,
           YEAR(d) AS yyyy, WEEKOFYEAR(d) AS week_no, MIN(d) AS week_start, MAX(d) AS week_end,"""
B_NEW = """             TO_DATE(p.dt,'yyyyMMdd') AS d, """ + WM + """, p.pdp_uv_cnt, p.purchase_uv_cnt, p.gmv
      {_PMKT_FROM_HERO.rstrip()}
        AND p.dt BETWEEN '20250101' AND '{date}'
    )
    SELECT hero, season, path,
           YEAR(DATE_ADD(wm,3)) AS yyyy, WEEKOFYEAR(wm) AS week_no, MIN(d) AS week_start, MAX(d) AS week_end,"""

B_OLD2 = """    FROM base
    GROUP BY hero, season, path, YEAR(d), WEEKOFYEAR(d)"""
B_NEW2 = """    FROM base
    GROUP BY hero, season, path, wm"""


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
    (ROOT / "_nb_workspace_pre_isoweek.py").write_text(src, encoding="utf-8")
    out, done = src, []
    for name, old, new in (("PMKT주차 SELECT", A_OLD, A_NEW), ("PMKT주차 GROUP BY", A_OLD2, A_NEW2),
                           ("PMKT경로주차 SELECT", B_OLD, B_NEW), ("PMKT경로주차 GROUP BY", B_OLD2, B_NEW2)):
        if new in out:
            continue
        if old not in out:
            raise SystemExit(f"[중단] 앵커 못 찾음: {name}")
        out = out.replace(old, new, 1)
        done.append(name)
    print("적용:", " · ".join(done) or "없음(이미 반영)")
    (ROOT / "_nb_live_after_isoweek.py").write_text(out, encoding="utf-8")
    if not done or dry:
        return 0
    res = api("POST", "/api/2.0/workspace/import", {
        "path": WPATH, "format": "SOURCE", "language": "PYTHON", "overwrite": True,
        "content": base64.b64encode(out.encode("utf-8")).decode()})
    if "_err" in res:
        print("IMPORT 실패:", res["_err"], res.get("_body"))
        return 1
    print("노트북 반영 완료 — 다음 실행부터 ISO 주(월요일 시작)로 집계된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
