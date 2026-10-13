# -*- coding: utf-8 -*-
"""실제 발매 시점 — 상품이 시스템에서 '판매중'(sale_stat_cl=40)으로 전환된 시각.

왜 있나 (2026-10-13, 사용자 지시)
  IMC 캘린더의 발매일은 아이템마스터('무탠' B열)의 **계획**이다. 날짜가 지나면 계획이 아니라
  시스템에 남은 기록으로 "이때 발매됐다"를 보여 준다. 기준은 헷갈리지 않게 **판매중 전환 시점 하나**.

원천 = musinsa.bizest.goods_hist_management (상품별 판매상태 변경 이력, rt 는 **KST**(표기만 Z)).
  발매는 대기(0·5·9·10) → 판매중(40) 전환으로 찍힌다(26FW 히어로 384건 실측: 전부 BULK_UPD_STATUS,
  직전 상태 10/5/0). 품절(30)·중지(-10) 뒤 재판매로 40 이 되는 건 발매가 아니라서 뺀다.
  26FW 지난 발매 109스타일 대조: 계획일 당일 81 · 전날 16(키즈 등 미리 전환) · 다음날 6 · 기록 없음 3 ·
  수십 일 전 3(옛 goods_no 를 다시 쓰는 캐리오버).
  → 계획일 ±WINDOW_DAYS(7일) 안의 첫 전환만 실제 발매로 본다 — 캐리오버의 옛 전환, 발매 2~3주 전에
    판매중↔대기를 오가며 미리 켜 본 기록(빅토리아 울 MMFWCAB12 8/6 · 라이트다운 MMFDJ9A82 8/15)을 집지 않게.
    ±14일로는 이 둘이 잡혔고, 정상 발매의 어긋남은 전부 ±2일 안이었다. 창 밖·기록 없음은
    아무것도 표시하지 않는다(사용자 지시 — '판매 미확인' 표시 불필요).

조회 경로 = DBX 쿼리 중계(spec 'release', soo/hero_ops/dbx_relay.py). 생성기 CI 에는 DBX 토큰이 없다.
  ★RELEASE_SQL 은 **글자 그대로** 중계 키(sha1)다 — 고치면 dash_audit/dbx_relay/deploy.py --apply 를 다시 돌린다.
"""
from __future__ import annotations

import datetime as _dt

WINDOW_DAYS = 7

RELEASE_SQL = """
WITH g AS (
  SELECT goods_no FROM musinsa.bizest.goods WHERE brand LIKE 'musinsastandard%'
),
h AS (
  SELECT m.goods_no, m.rt, m.sale_stat_cl,
         LAG(m.sale_stat_cl) OVER (PARTITION BY m.goods_no ORDER BY m.rt, m.hist_no) AS prev_stat
  FROM musinsa.bizest.goods_hist_management m JOIN g ON g.goods_no = m.goods_no
)
SELECT CAST(goods_no AS STRING) AS goods_no, date_format(rt, 'yyyy-MM-dd HH:mm') AS on_sale_at
FROM h
WHERE sale_stat_cl = '40' AND (prev_stat IS NULL OR prev_stat IN ('0', '5', '9', '10'))
  AND rt >= date_sub(current_date(), 240)
"""

MIN_ROWS = 1000   # 실측 ~1만 행(240일) — 이보다 적으면 부분 결과로 보고 쓰지 않는다


def fetch(sheets, log=print) -> dict[str, list[_dt.datetime]]:
    """goods_no → 판매중 전환 시각 목록(오름차순). 실패하면 예외 — 호출부가 기능만 건너뛴다."""
    from soo.hero_ops import dbx_relay
    dbx_relay.configure(sheets, "release", max_age_h=30)
    try:
        rows = dbx_relay.rows(RELEASE_SQL, log)
    finally:
        dbx_relay._cfg["sheets"] = None          # 다른 spec 의 조회가 이 설정을 물려받지 않게
    if len(rows) < MIN_ROWS:
        raise RuntimeError(f"판매중 전환 로그 {len(rows)}행 < {MIN_ROWS} — 부분 결과 의심")
    out: dict[str, list[_dt.datetime]] = {}
    for gno, at in rows:
        try:
            t = _dt.datetime.strptime(str(at)[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        out.setdefault(str(gno).strip(), []).append(t)
    for v in out.values():
        v.sort()
    return out


def actual_release(uids, plan: _dt.date, log_map: dict) -> _dt.datetime | None:
    """그 스타일의 goods 들 중 계획일 ±WINDOW_DAYS 안의 가장 이른 판매중 전환. 없으면 None."""
    best = None
    for u in uids or ():
        for t in log_map.get(str(u).strip(), ()):
            if abs((t.date() - plan).days) <= WINDOW_DAYS and (best is None or t < best):
                best = t
    return best
