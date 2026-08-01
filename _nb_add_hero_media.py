# -*- coding: utf-8 -*-
"""노트북에 '히어로별 매체 유입(utm)' 원천 추가 (멱등).

배경(2026-08-02, 사용자 지시): 퍼포먼스 마케팅을 **히어로 상품 단위**로 보고 싶다.
utm_campaign은 광고코드(NEWBANE4009·FBDMTAU051…)라 상품이 안 들어 있지만,
`team.marketing.musinsa_session_goods_view_pdp_daily` 는 **goods_no × utm × 세션**이라
히어로 uid로 좁히면 "어느 매체가 이 히어로 유입을 얼마나 만들었나"가 정확히 나온다.

원천 = team.marketing.musinsa_session_goods_view_pdp_daily (2026-07-31까지 최신)
  · session_id · hash_id_mapped · utm_source/medium/campaign · goods_no · count_view_logs · count_pdp_logs
  · 실측(커브드팬츠 7/20~26): facebook_network/da 38,052세션 · naver_brandsearch/da 19,938 · app_push/cr 14,926 …

추가 탭: `히어로매체기간` = 히어로 x 시즌 x 기간 x utm_source x utm_medium (+전년 PDP)
  ★광고코드(utm_campaign)는 카디널리티가 커서 이 탭에선 빼고 매체(source/medium)까지만.

실행: DBX_TOKEN=... python _nb_add_hero_media.py [--dry]
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

ANCHOR = 'print("[OK] 상품퍼널기간 기록 완료")\n'

CELL = '''
# COMMAND ----------

# (7) 히어로 x 매체(utm) 유입 — 2026-08-02 신설. "퍼포먼스 마케팅을 히어로 단위로" 요청.
#     원천 team.marketing.musinsa_session_goods_view_pdp_daily = goods_no x utm x 세션.
#     ★utm_campaign(광고코드)은 카디널리티가 커서 제외 — 매체(source/medium)까지만 싣는다.
#     medium 코드 관례: da=디스플레이 · sa=검색광고 · sh=쇼핑 · cr=CRM · if=인플루언서 · organic=오가닉.
#     utm이 비면 온사이트/직접 유입이다(그쪽은 기존 PMKT경로 탭이 담당).
_HERO_MAP = globals().get("_HERO_MAP") or []
if _HERO_MAP:
    _media_union = " UNION ALL ".join(f"""
      SELECT '{nm}' AS period, hm.hero AS hero, hm.season AS season,
             COALESCE(NULLIF(TRIM(s.utm_source),''),'(없음)') AS src,
             COALESCE(NULLIF(TRIM(s.utm_medium),''),'(없음)') AS med,
             COUNT(DISTINCT CASE WHEN s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}' THEN s.session_id END) AS sessions,
             SUM(CASE WHEN s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}' THEN s.count_pdp_logs ELSE 0 END) AS pdp,
             COUNT(DISTINCT CASE WHEN s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}' THEN s.hash_id_mapped END) AS users,
             SUM(CASE WHEN s.partition_date BETWEEN '{sly[:4]}-{sly[4:6]}-{sly[6:]}' AND '{ely[:4]}-{ely[4:6]}-{ely[6:]}' THEN s.count_pdp_logs ELSE 0 END) AS pdp_ly
      FROM team.marketing.musinsa_session_goods_view_pdp_daily s
      JOIN v_hero_map hm ON CAST(s.goods_no AS STRING) = hm.goods_no
      WHERE (s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}'
          OR s.partition_date BETWEEN '{sly[:4]}-{sly[4:6]}-{sly[6:]}' AND '{ely[:4]}-{ely[4:6]}-{ely[6:]}')
      GROUP BY hm.hero, hm.season,
               COALESCE(NULLIF(TRIM(s.utm_source),''),'(없음)'),
               COALESCE(NULLIF(TRIM(s.utm_medium),''),'(없음)')
      HAVING SUM(CASE WHEN s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}' THEN s.count_pdp_logs ELSE 0 END) > 0
          OR SUM(CASE WHEN s.partition_date BETWEEN '{sly[:4]}-{sly[4:6]}-{sly[6:]}' AND '{ely[:4]}-{ely[4:6]}-{ely[6:]}' THEN s.count_pdp_logs ELSE 0 END) > 0""" for nm, s, e, sly, ely in _pmkt_periods)
    insert_query_result("히어로매체기간", spark.sql(_media_union),
                        label=f"히어로 x 기간 x 매체(utm source/medium) 유입 세션·PDP(+전년) ~{date}")
    print("[OK] 히어로매체기간 기록 완료")
else:
    print("[스킵] 히어로매체기간 — 히어로 맵 없음(직전 시트값 유지)")
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
    (ROOT / "_nb_workspace_pre_heromedia.py").write_text(src, encoding="utf-8")
    if "히어로매체기간" in src:
        print("이미 반영됨 — 변경 없음")
        return 0
    if ANCHOR not in src:
        print("[중단] 앵커(상품퍼널기간 print) 없음 — _nb_add_goods_funnel.py 먼저 적용할 것")
        return 1
    i = src.index(ANCHOR) + len(ANCHOR)
    out = src[:i] + CELL + src[i:]
    (ROOT / "_nb_live_after_heromedia.py").write_text(out, encoding="utf-8")
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
    print("노트북 반영 완료 — 다음 실행부터 '히어로매체기간' 탭이 생긴다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
