# -*- coding: utf-8 -*-
"""DBX 쿼리 중계 — 읽는 쪽(로컬·CI 자동화).

왜 있나 (2026-10-12)
  데이터플랫폼팀 공지: 노트북의 로컬 파이썬·CLI 로 SQL Warehouse 에 붙는 경로는 순차적으로 막는다.
  정기 배치는 Databricks Job 으로 옮기라는 답을 받았다(장성우님, #tech-문의-데이터).
  → DBX 잡(`[자동] DBX 쿼리 중계`, 노트북 dbx_relay_nb.py)이 **각 자동화의 코드를 그대로 불러**
    같은 SQL 을 돌리고, 결과를 중간 시트에 쓴다. 자동화는 SQL 을 보내기 직전에 여기서 **같은 SQL 의
    결과**를 찾는다(키 = 공백 정규화한 SQL 의 sha1). SQL 이 한 글자라도 다르면 못 찾는다 —
    날짜 창·품번 목록이 어긋난 결과를 조용히 쓰는 일이 원리상 없다.

모드 (환경변수 DBX_RELAY_MODE)
  prefer (기본) — 중계에 있으면 쓰고, 없으면 지금처럼 직접 조회(경고 로그). 로컬 접근이 막히기 전까지.
  only          — 중계에 없으면 실패. 로컬 접근이 막힌 뒤 이걸로 바꾼다.
  off           — 중계를 안 본다(비교·디버깅용).

★이 파일은 hero_bot/soo/hero_ops/dbx_relay.py 에 같은 내용으로 복사돼 있다(CI 는 이 폴더를 못 본다).
  고치면 둘 다 고친다.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import time

STAGING_ID = "1uYgHc_LkNHTCGBRxK3_Sws44dk_RlDRd17gKySqvz9g"   # [자동] DBX 쿼리 중계 (내 드라이브, SA writer)
NULL = "␀"                                               # SQL NULL 표식(빈 문자열과 구분)
STYLE_TAB = "_asn_styles"                                     # ASN 잡이 읽는 히어로 품번 목록(CI 가 매번 갱신)

_cfg = {"sheets": None, "spec": None, "max_age_h": 6.0, "idx": None, "idx_at": 0.0}


class Miss(Exception):
    """중계 결과가 없다/낡았다 — 호출부가 직접 조회로 넘어갈지 정한다."""


def mode() -> str:
    return (os.environ.get("DBX_RELAY_MODE") or "prefer").strip().lower()


def key(sql: str) -> str:
    return hashlib.sha1(" ".join(sql.split()).encode("utf-8")).hexdigest()[:16]


def configure(sheets, spec: str, max_age_h: float) -> None:
    """sheets = googleapiclient sheets 서비스 또는 spreadsheets() 리소스."""
    _cfg.update(sheets=sheets.spreadsheets() if hasattr(sheets, "spreadsheets") else sheets,
                spec=spec, max_age_h=max_age_h, idx=None)


def _retry(fn, n=5):
    last = None
    for a in range(n):
        try:
            return fn()
        except Exception as e:      # 사내망 503·10054 · Sheets 429
            last = e
            time.sleep(10 * (a + 1))
    raise last


def _get(rng: str) -> list[list]:
    ss = _cfg["sheets"]
    return _retry(lambda: ss.values().get(spreadsheetId=STAGING_ID, range=rng,
                                          valueRenderOption="UNFORMATTED_VALUE").execute()).get("values", [])


def _index() -> dict:
    if _cfg["idx"] is None or time.time() - _cfg["idx_at"] > 300:
        rows = _get(f"'_index_{_cfg['spec']}'!A2:I")
        _cfg["idx"] = {r[0]: r for r in rows if r and r[0]}
        _cfg["idx_at"] = time.time()
    return _cfg["idx"]


def _age_h(loaded_at: str) -> float:
    t = _dt.datetime.fromisoformat(str(loaded_at))            # KST naive
    now = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=9)
    return (now.replace(tzinfo=None) - t).total_seconds() / 3600


def _read(row) -> tuple[list[dict], list[list]]:
    # index 열: key, tab, spec, alias, rows, ncols, loaded_at_kst, run_id, sql_head
    tab, n, nc = row[1], int(row[4]), int(row[5])
    vals = _get(f"'{tab}'!A1:{_col(max(nc, 1))}{n + 1}")
    cols = json.loads(vals[0][0])
    out = []
    for r in vals[1:]:
        r = [("" if v is None else str(v)) for v in r] + [""] * (nc - len(r))
        out.append([None if v == NULL else v for v in r[:nc]])
    if len(out) != n:
        raise Miss(f"중계 탭 {tab} 행수 {len(out)} ≠ 기록 {n} (부분 기입?)")
    return cols, out


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def statement(sql: str, log=print) -> dict:
    """Statement API 의 SUCCEEDED 응답과 같은 모양을 돌려준다(호출부 후처리를 그대로 쓰게)."""
    if mode() == "off":
        raise Miss("DBX_RELAY_MODE=off")
    if _cfg["sheets"] is None:
        raise Miss("relay.configure() 안 됨")
    k = key(sql)
    try:
        row = _index().get(k)
    except Exception as e:
        raise Miss(f"중계 인덱스 읽기 실패: {type(e).__name__}: {str(e)[:120]}")
    if not row:
        raise Miss(f"중계에 이 SQL 결과가 없다(key {k}, spec {_cfg['spec']})")
    age = _age_h(row[6])
    if age > _cfg["max_age_h"]:
        raise Miss(f"중계 결과가 낡았다 — {age:.1f}h 전({row[6]}) > 허용 {_cfg['max_age_h']}h")
    cols, rows = _read(row)
    log(f"[relay] 중계 사용 · {len(rows):,}행 · {row[6]} 적재 · key {k}")
    return {"statement_id": "relay:" + k, "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": cols}, "total_row_count": len(rows)},
            "result": {"data_array": rows}}


def rows(sql: str, log=print) -> list[list]:
    return statement(sql, log)["result"]["data_array"]


def by_alias(alias: str, max_age_h: float | None = None) -> tuple[list[dict], list[list]]:
    """SQL 이 아니라 별칭으로 찾는다(예: goods_master — 다른 자동화가 이름 사전으로 빌려 쓴다)."""
    hits = [r for r in _index().values() if len(r) > 3 and r[3] == alias]
    if not hits:
        raise Miss(f"별칭 {alias} 없음")
    row = max(hits, key=lambda r: str(r[6]))
    if max_age_h is not None and _age_h(row[6]) > max_age_h:
        raise Miss(f"{alias} 낡음({row[6]})")
    return _read(row)


def fallback(e: Exception, what: str, log=print) -> None:
    """중계 실패 시 직접 조회로 넘어가도 되는지. only 모드면 여기서 실패로 올린다."""
    if mode() == "only":
        raise RuntimeError(f"[relay] {what}: 중계 결과 없음 + DBX_RELAY_MODE=only → 직접 조회 금지. {e}")
    log(f"[relay] ⚠ {what}: {e} → 직접 조회(로컬 SQL Warehouse 접근 — 제한 예정)")


def publish_styles(sheets, styles: list[str], log=print) -> None:
    """ASN DBX 잡이 쓸 품번 목록을 갱신한다(바뀐 경우에만 씀)."""
    ss = sheets.spreadsheets() if hasattr(sheets, "spreadsheets") else sheets
    want = sorted(set(styles))
    try:
        cur = [r[0] for r in _retry(lambda: ss.values().get(
            spreadsheetId=STAGING_ID, range=f"'{STYLE_TAB}'!A2:A").execute()).get("values", []) if r]
    except Exception:
        cur = None
    if cur == want:
        return
    if cur is None:
        _retry(lambda: ss.batchUpdate(spreadsheetId=STAGING_ID, body={"requests": [
            {"addSheet": {"properties": {"title": STYLE_TAB}}}]}).execute())
    _retry(lambda: ss.values().clear(spreadsheetId=STAGING_ID, range=f"'{STYLE_TAB}'!A:A").execute())
    _retry(lambda: ss.values().update(spreadsheetId=STAGING_ID, range=f"'{STYLE_TAB}'!A1", valueInputOption="RAW",
                                      body={"values": [["style"]] + [[s] for s in want]}).execute())
    log(f"[relay] ASN 품번 목록 갱신 {len(cur or [])} → {len(want)}")
