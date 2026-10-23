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

# ★예약발매 = 비제스트 '출고예정 구분'을 예약 배송으로 바꾼 이력(2026-10-23, 사용자 "시스템상 예약발매 상태를 알 수 있을 텐데").
#   상품 마스터 `goods.dlv_due_type`(G 일반 · R 예약 · D 지연)은 **현재 값**뿐이라 입고 뒤 일반으로 돌리면 과거 예약이 사라진다
#   (10/23 현재 R 은 42개뿐). 변경 이력 memo 에는 `출고예정 구분 : 예약 배송 20260910` 처럼 예약 출고일까지 남는다 → 이걸 쓴다.
#   UID(컬러) 단위라 컬러추가도 컬러별로 정확하다. rt 는 KST(표기만 Z).
#   중계 spec 'preorder' 가 이 상수를 글자 그대로 돌린다 — 고치면 dash_audit/dbx_relay/deploy.py --apply.
PREORDER_SQL = """
WITH g AS (
  SELECT goods_no FROM musinsa.bizest.goods WHERE brand LIKE 'musinsastandard%'
)
SELECT DISTINCT CAST(h.goods_no AS STRING) AS goods_no, date_format(h.rt, 'yyyy-MM-dd HH:mm') AS set_at,
       regexp_extract(h.memo, '예약 배송 ([0-9]{8})', 1) AS ship_dt
FROM musinsa.bizest.goods_hist_management h JOIN g ON g.goods_no = h.goods_no
WHERE h.rt >= date_sub(current_date(), 240)
  AND h.upd_area LIKE '%출고예정%'
  AND h.memo LIKE '%예약 배송%'
"""
PREORDER_MIN_ROWS = 100


def fetch_preorder(sheets, log=print) -> dict[str, list[tuple[_dt.datetime, str]]]:
    """goods_no → [(예약 설정 시각, 예약 출고일 YYYYMMDD)] (오름차순)."""
    from soo.hero_ops import dbx_relay
    dbx_relay.configure(sheets, "preorder", max_age_h=30)
    try:
        rows = dbx_relay.rows(PREORDER_SQL, log)
    finally:
        dbx_relay._cfg["sheets"] = None
    if len(rows) < PREORDER_MIN_ROWS:
        raise RuntimeError(f"예약배송 이력 {len(rows)}행 < {PREORDER_MIN_ROWS} — 부분 결과 의심")
    out: dict[str, list] = {}
    for gno, at, ship in rows:
        try:
            t = _dt.datetime.strptime(str(at)[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        out.setdefault(str(gno).strip(), []).append((t, str(ship or "")))
    for v in out.values():
        v.sort()
    return out


# ★상세페이지 등록 여부(2026-10-23, 사용자 지시 "발매 D-1 전인데 등록 안 된 게 하나라도 있으면 띄워 줘")
#   = 비제스트 상품 마스터 `goods_cont`(상품설명 = 상세 본문) 가 비어 있는 상품 목록 — 여기 있으면 미등록, 없으면 등록.
#   ★처음엔 '최근 300일 등록·대기 상품'의 등록 여부를 가져왔는데, 오래전 등록된 판매중 컬러(MWEDC9D55-BK, 본문 475자·
#     이미지 9장)가 범위 밖이라 '미등록'으로 잡혔다 → 범위를 두지 않고 **빈 것만** 무탠 전체에서 모은다(휴지통 -90 제외).
#   10/23 실측: 10/28 발매 3스타일의 새 컬러 6개가 전부 등록대기(5)·본문 0자. 중계 spec 'detail'.
#   ★10/23 등록/판매 대기(5·10·15) 상품도 함께 — 아직 발매 전인 행의 '이번에 발매하는 컬러'를 가리는 데 쓴다.
#   ★통합 UID(style_no 에 컬러가 없는 상품, 예 커브드 6092186) 도 함께 — 온라인은 통합 UID 하나로 팔고 컬러별 UID 는
#     계속 등록대기·본문 0 이라(MMFPC3A15-BR/GR/OL) 컬러별로 보면 '상세 작업 전'·'발매 컬러' 둘 다 틀린다.
DETAIL_SQL = """
SELECT CAST(goods_no AS STRING) AS goods_no,
       CASE WHEN length(coalesce(goods_cont, '')) > 0 THEN 'Y' ELSE '' END AS has_detail,
       CAST(sale_stat_cl AS STRING) AS sale_stat_cl,
       CASE WHEN style_no NOT LIKE '%-%' THEN substr(style_no, 1, 9) ELSE '' END AS integrated_style
FROM musinsa.bizest.goods
WHERE brand LIKE 'musinsastandard%'
  AND (length(coalesce(goods_cont, '')) = 0 OR sale_stat_cl IN ('5', '10', '15') OR style_no NOT LIKE '%-%')
  AND coalesce(sale_stat_cl, '') <> '-90'
"""
DETAIL_MIN_ROWS = 100


def fetch_detail(sheets, log=print) -> tuple[set[str], set[str], dict[str, bool]]:
    """→ (상세 본문이 빈 goods_no, 등록/판매 대기 goods_no, {통합 UID 품번: 판매중이거나 상세 있음})."""
    from soo.hero_ops import dbx_relay
    dbx_relay.configure(sheets, "detail", max_age_h=30)
    try:
        rows = dbx_relay.rows(DETAIL_SQL, log)
    finally:
        dbx_relay._cfg["sheets"] = None
    if len(rows) < DETAIL_MIN_ROWS:
        raise RuntimeError(f"상세 미등록 목록 {len(rows)}행 < {DETAIL_MIN_ROWS} — 부분 결과 의심")
    empty = {str(r[0]).strip() for r in rows if str(r[1] or "") != "Y"}
    pending = {str(r[0]).strip() for r in rows if str(r[2] or "") in ("5", "10", "15")}
    integrated: dict[str, bool] = {}
    for r in rows:
        sty = str(r[3] or "").strip() if len(r) > 3 else ""
        if sty:
            integrated[sty] = integrated.get(sty, False) or str(r[1] or "") == "Y"
    return empty, pending, integrated


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
