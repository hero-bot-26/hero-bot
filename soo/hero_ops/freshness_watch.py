# -*- coding: utf-8 -*-
"""원천이 '조용히 멈추는 것'을 잡는 감시 (2026-08-01 신설).

배경: PLM → Databricks 적재가 **7/22 이후 멈췄는데** 그 아래 파이프(테이블→시트→앱)는 매일 정상
동작해서, 앱은 열흘째 같은 값을 새 값처럼 보여주고 있었다. 파일 수정시각도 매일 갱신되므로
'언제 마지막으로 **내용**이 바뀌었는지'를 우리가 따로 기록해야 알 수 있다.

방식: 원천에서 만든 지문(fingerprint)을 앱 시트의 `_소스신선도` 탭에 적어두고, 실행할 때마다
비교한다. 지문이 그대로면 '마지막 변경일'을 유지 → 며칠째 안 바뀌었는지 계산된다.
임계일을 넘기면 호출부가 헬스 경고로 올리고, CI가 슬랙 DM을 보낸다.

탭 포맷: 1행 라벨 · 2행 헤더(소스키/지문/마지막변경일/마지막확인일/비고) · 3행~ 데이터.
"""
from __future__ import annotations

import datetime as dt
import hashlib

TAB = "_소스신선도"
HEADER = ["소스키", "지문", "마지막변경일", "마지막확인일", "비고"]


def fingerprint(values) -> str:
    """행 목록/문자열 → 짧은 지문. 값이 하나라도 바뀌면 달라진다."""
    h = hashlib.sha1()
    if isinstance(values, str):
        h.update(values.encode("utf-8"))
    else:
        for row in values:
            h.update(("\x1f".join("" if c is None else str(c) for c in row) + "\x1e").encode("utf-8"))
    return h.hexdigest()[:16]


def _read(sheets, sheet_id):
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{TAB}'!A1:E200").execute().get("values", [])
    except Exception:
        return None                      # 탭 없음 → 아래에서 만든다
    return vals


def _ensure_tab(sheets, sheet_id):
    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id,
                                     fields="sheets.properties(title)").execute()
    titles = {s["properties"]["title"] for s in meta["sheets"]}
    if TAB in titles:
        return
    sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [
        {"addSheet": {"properties": {"title": TAB, "gridProperties": {"rowCount": 200, "columnCount": 5}}}}]}).execute()


def track(sheets, sheet_id, key, fp, today=None, stale_days=10, note=""):
    """지문 비교 → {'days': 며칠째 그대로, 'last_changed': 'YYYY-MM-DD', 'stale': bool, 'changed': bool}.

    실패하면 판단 보류(stale=False) — 감시가 본 작업을 막지 않는다.
    """
    today = today or dt.date.today()
    out = {"days": 0, "last_changed": today.isoformat(), "stale": False, "changed": True}
    try:
        vals = _read(sheets, sheet_id)
        if vals is None:
            _ensure_tab(sheets, sheet_id)
            vals = []
        rows = vals[2:] if len(vals) > 2 else []
        idx = {str(r[0]).strip(): i for i, r in enumerate(rows) if r}
        prev = rows[idx[key]] if key in idx else None
        prev_fp = str(prev[1]).strip() if prev and len(prev) > 1 else ""
        prev_changed = str(prev[2]).strip() if prev and len(prev) > 2 else ""
        changed = (fp != prev_fp)
        last_changed = today.isoformat() if changed or not prev_changed else prev_changed
        try:
            out["days"] = (today - dt.date.fromisoformat(last_changed)).days
        except ValueError:
            out["days"] = 0
        out.update(changed=changed, last_changed=last_changed,
                   stale=(out["days"] >= stale_days))
        row = [key, fp, last_changed, today.isoformat(), note]
        if key in idx:
            rng = f"'{TAB}'!A{idx[key] + 3}:E{idx[key] + 3}"
        else:
            rng = f"'{TAB}'!A{len(rows) + 3}:E{len(rows) + 3}"
            if not vals:
                sheets.spreadsheets().values().update(
                    spreadsheetId=sheet_id, range=f"'{TAB}'!A1:E2", valueInputOption="RAW",
                    body={"values": [["원천 신선도 감시 — 지문이 바뀐 날짜를 기록한다(생성기 자동)"], HEADER]}).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=rng, valueInputOption="RAW", body={"values": [row]}).execute()
    except Exception as e:                # 감시 실패는 조용히 통과(본 작업 우선)
        print(f"[freshness] '{key}' 기록 실패 — 판단 보류: {type(e).__name__}: {e}")
    return out
