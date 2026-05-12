"""Screenshots 탭 — (날짜, 뷰, goods_no) 별 best peak rank + 스크린샷 URL 추적.

플로우:
- hourly가 (뷰별로) rank ≤ threshold 진입 검출 시 read_day_records로 기존 기록 확인
- 새 rank가 더 좋으면(낮으면) upsert_record로 갱신 (URL 교체)
- daily가 read_day_records로 어제 분 가져와 Slack image_block 구성

스키마: 날짜 / 뷰 / goods_no / peak_rank / screenshot_url / file_id / captured_at
기존 6컬럼 스키마(뷰 없음)는 자동 마이그 — B열에 컬럼 insert.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from soo import persona, DEFAULT_VIEW_LABEL


SCREENSHOTS_TAB = "Screenshots"
SCREENSHOTS_HEADER = ["날짜", "뷰", "goods_no", "peak_rank", "screenshot_url", "file_id", "captured_at"]
SCREENSHOTS_HEADER_OLD = ["날짜", "goods_no", "peak_rank", "screenshot_url", "file_id", "captured_at"]
_VIEW_COL_IDX = 1  # 0-based, B열


def _col_letter(idx_zero_based: int) -> str:
    return chr(ord("A") + idx_zero_based)


def _migrate_view_column(sheets_service: Any, sheet_id: str, log: logging.Logger | None = None) -> bool:
    """탭이 옛 6컬럼 스키마면 B열에 "뷰" 컬럼 insert. 이미 새 헤더면 no-op."""
    meta = sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets(properties(title,sheetId))"
    ).execute()
    sheet_props = next(
        (s["properties"] for s in meta.get("sheets", []) if s["properties"]["title"] == SCREENSHOTS_TAB),
        None,
    )
    if not sheet_props:
        return False

    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{SCREENSHOTS_TAB}'!A1:1"
    ).execute()
    current = (resp.get("values") or [[]])[0]

    if current == SCREENSHOTS_HEADER:
        return False
    if not current:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{SCREENSHOTS_TAB}'!A1",
            valueInputOption="RAW",
            body={"values": [SCREENSHOTS_HEADER]},
        ).execute()
        return False

    if current == SCREENSHOTS_HEADER_OLD:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_props["sheetId"],
                        "dimension": "COLUMNS",
                        "startIndex": _VIEW_COL_IDX,
                        "endIndex": _VIEW_COL_IDX + 1,
                    },
                    "inheritFromBefore": False,
                },
            }]},
        ).execute()
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{SCREENSHOTS_TAB}'!{_col_letter(_VIEW_COL_IDX)}1",
            valueInputOption="RAW",
            body={"values": [["뷰"]]},
        ).execute()
        if log:
            log.info(persona.step(f"[{SCREENSHOTS_TAB}] 뷰 컬럼 B열에 자동 insert"))
        return True

    if log:
        log.warning(persona.step(
            f"[{SCREENSHOTS_TAB}] 헤더가 기대값과 다름. 자동 마이그 skip. 현재={current}"
        ))
    return False


def _ensure_tab(sheets_service: Any, sheet_id: str, log: logging.Logger | None = None) -> None:
    meta = sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets.properties"
    ).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}
    if SCREENSHOTS_TAB not in existing:
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
        return
    _migrate_view_column(sheets_service, sheet_id, log=log)


def _read_all(sheets_service: Any, sheet_id: str) -> list[list]:
    try:
        resp = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{SCREENSHOTS_TAB}'!A2:G",
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
    view: str = DEFAULT_VIEW_LABEL,
) -> dict[str, dict]:
    """(target_day, view)의 (goods_no → record) dict. record: peak_rank, screenshot_url, file_id, row_idx.

    빈 뷰 셀은 DEFAULT_VIEW_LABEL("전체")로 해석.
    """
    _ensure_tab(sheets_service, sheet_id)
    rows = _read_all(sheets_service, sheet_id)
    day_str = target_day.isoformat()
    day_alt_strict = f"{target_day.year}. {target_day.month}. {target_day.day}."
    out: dict[str, dict] = {}
    for i, row in enumerate(rows, start=2):
        if len(row) < 7:
            row = list(row) + [""] * (7 - len(row))
        date_s, view_s, goods_no, peak_rank_s, url, file_id, captured_at = row[:7]
        date_str = str(date_s).strip()
        if date_str not in (day_str, day_alt_strict):
            continue
        row_view = (str(view_s).strip() or DEFAULT_VIEW_LABEL)
        if row_view != view:
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
    view: str,
    goods_no: str,
    peak_rank: int,
    screenshot_url: str,
    file_id: str,
    captured_at: datetime,
    log: logging.Logger | None = None,
) -> None:
    """Screenshots 탭에 (날짜, 뷰, goods_no) row upsert.

    같은 키가 이미 있으면 update, 없으면 append.
    호출 측에서 'peak_rank가 기존보다 좋다'를 검증한 뒤 호출.
    """
    _ensure_tab(sheets_service, sheet_id, log=log)
    existing = read_day_records(sheets_service, sheet_id, target_day, view=view)
    new_row = [
        target_day.isoformat(),
        view,
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
            range=f"'{SCREENSHOTS_TAB}'!A{row_idx}:G{row_idx}",
            valueInputOption="RAW",
            body={"values": [new_row]},
        ).execute()
        if log:
            log.info(persona.step(
                f"Screenshots 갱신 [{view}] — {goods_no} peak #{peak_rank} (row {row_idx})"
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
                f"Screenshots 추가 [{view}] — {goods_no} peak #{peak_rank}"
            ))
