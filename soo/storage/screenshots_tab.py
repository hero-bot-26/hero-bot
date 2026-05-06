"""Screenshots 탭 — (날짜, goods_no) 별 best peak rank + 스크린샷 URL 추적.

플로우:
- hourly가 rank ≤ threshold 진입 검출 시 read_day_records로 기존 기록 확인
- 새 rank가 더 좋으면(낮으면) upsert_record로 갱신 (URL 교체)
- daily가 read_day_records로 어제 분 가져와 Slack image_block 구성

스키마: 날짜 / goods_no / peak_rank / screenshot_url / file_id / captured_at
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from soo import persona


SCREENSHOTS_TAB = "Screenshots"
SCREENSHOTS_HEADER = ["날짜", "goods_no", "peak_rank", "screenshot_url", "file_id", "captured_at"]


def _ensure_tab(sheets_service: Any, sheet_id: str) -> None:
    meta = sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets.properties"
    ).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}
    if SCREENSHOTS_TAB in existing:
        return
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": SCREENSHOTS_TAB}}}]},
    ).execute()
    sheets_service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{SCREENSHOTS_TAB}'!A1",
        valueInputOption="RAW",
        body={"values": [SCREENSHOTS_HEADER]},
    ).execute()


def _read_all(sheets_service: Any, sheet_id: str) -> list[list]:
    try:
        resp = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{SCREENSHOTS_TAB}'!A2:F",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ).execute()
        return resp.get("values", [])
    except Exception:
        return []


def read_day_records(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
) -> dict[str, dict]:
    """target_day의 (goods_no → record) dict. record: peak_rank, screenshot_url, file_id, row_idx (1-based)."""
    _ensure_tab(sheets_service, sheet_id)
    rows = _read_all(sheets_service, sheet_id)
    day_str = target_day.isoformat()
    day_alt_strict = f"{target_day.year}. {target_day.month}. {target_day.day}."
    out: dict[str, dict] = {}
    for i, row in enumerate(rows, start=2):  # 헤더 다음 줄부터 row 2
        if len(row) < 6:
            row = list(row) + [""] * (6 - len(row))
        date_s, goods_no, peak_rank_s, url, file_id, captured_at = row[:6]
        date_str = str(date_s).strip()
        if date_str not in (day_str, day_alt_strict):
            continue
        try:
            peak_rank = int(peak_rank_s)
        except (TypeError, ValueError):
            continue
        gn = str(goods_no)
        out[gn] = {
            "peak_rank": peak_rank,
            "screenshot_url": str(url) if url else "",
            "file_id": str(file_id) if file_id else "",
            "captured_at": str(captured_at) if captured_at else "",
            "row_idx": i,
        }
    return out


def upsert_record(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    goods_no: str,
    peak_rank: int,
    screenshot_url: str,
    file_id: str,
    captured_at: datetime,
    log: logging.Logger | None = None,
) -> None:
    """Screenshots 탭에 (날짜, goods_no) row upsert.

    같은 (날짜, goods_no)가 이미 있으면 update, 없으면 append.
    호출 측에서 'peak_rank가 기존보다 좋다'를 검증한 뒤 호출해야 함.
    """
    _ensure_tab(sheets_service, sheet_id)
    existing = read_day_records(sheets_service, sheet_id, target_day)
    new_row = [
        target_day.isoformat(),
        str(goods_no),
        int(peak_rank),
        screenshot_url,
        file_id,
        captured_at.isoformat(timespec="seconds"),
    ]

    rec = existing.get(str(goods_no))
    if rec:
        row_idx = rec["row_idx"]
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{SCREENSHOTS_TAB}'!A{row_idx}:F{row_idx}",
            valueInputOption="RAW",
            body={"values": [new_row]},
        ).execute()
        if log:
            log.info(persona.step(
                f"Screenshots 갱신 — {goods_no} peak #{peak_rank} (row {row_idx})"
            ))
    else:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"'{SCREENSHOTS_TAB}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [new_row]},
        ).execute()
        if log:
            log.info(persona.step(
                f"Screenshots 추가 — {goods_no} peak #{peak_rank}"
            ))
