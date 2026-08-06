"""Rank1Alerts 탭 — "랭킹 1위 즉시 알림"의 중복 방지 원장.

키 = (날짜, 뷰, goods_no). 한 번 알린 (뷰, 상품)은 그날 다시 알리지 않는다.
→ 1위에 올랐다 내려갔다를 반복해도 채널에 같은 알림이 도배되지 않음.
→ [남자] 1위로 먼저 알린 상품이 나중에 [전체] 1위에 오르면 그건 새 키라 다시 알림(의도됨).

스키마: 날짜 / 뷰 / goods_no / 브랜드 / 상품명 / 히어로여부 / 감지시각 / 슬랙ts /
       screenshot_url / file_id

⚠️ 발송 *전에* 행을 먼저 쓴다(ranking_daily의 Wide-우선 적재와 같은 이유).
   발송 후 기록하면 기록이 쿼터/네트워크로 실패할 때 다음 트리거(10분 뒤)가
   같은 알림을 또 보낸다. 먼저 쓰면 "행 존재 ⟺ 발송을 시도함"이 성립.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from soo import persona, DEFAULT_VIEW_LABEL
from soo.storage.sheet_archive import _execute


RANK1_TAB = "Rank1Alerts"
RANK1_HEADER = [
    "날짜", "뷰", "goods_no", "브랜드", "상품명", "히어로여부",
    "감지시각", "슬랙ts", "screenshot_url", "file_id",
]
_NCOL = len(RANK1_HEADER)


def _ensure_tab(sheets_service: Any, sheet_id: str, log: logging.Logger | None = None) -> None:
    meta = _execute(sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets.properties"
    ))
    existing = {s["properties"]["title"] for s in meta["sheets"]}
    if RANK1_TAB not in existing:
        _execute(sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": RANK1_TAB}}}]},
        ))
        _execute(sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{RANK1_TAB}'!A1",
            valueInputOption="RAW",
            body={"values": [RANK1_HEADER]},
        ))
        if log:
            log.info(persona.step(f"[{RANK1_TAB}] 탭 신규 생성"))
        return

    resp = _execute(sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{RANK1_TAB}'!A1:1"
    ))
    if not resp.get("values"):
        _execute(sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{RANK1_TAB}'!A1",
            valueInputOption="RAW",
            body={"values": [RANK1_HEADER]},
        ))


def read_day_alerts(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    log: logging.Logger | None = None,
) -> dict[tuple[str, str], dict]:
    """(target_day)에 이미 발송된 알림 → {(뷰, goods_no): record}.

    빈 뷰 셀은 DEFAULT_VIEW_LABEL("전체")로 해석. 날짜 셀은 시트 로케일 서식
    ("2026. 8. 6.") 으로 렌더될 수 있어 Long/Wide와 같은 방식으로 3형태를 모두 인정.
    """
    _ensure_tab(sheets_service, sheet_id, log=log)
    try:
        resp = _execute(sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{RANK1_TAB}'!A2:J",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ))
    except Exception:
        return {}

    day_str = target_day.isoformat()
    day_alt_strict = f"{target_day.year}. {target_day.month}. {target_day.day}."
    day_alt_compact = f"{target_day.year}.{target_day.month}.{target_day.day}."

    out: dict[tuple[str, str], dict] = {}
    for row in resp.get("values", []):
        if len(row) < _NCOL:
            row = list(row) + [""] * (_NCOL - len(row))
        (date_s, view_s, goods_no, brand, name, hero_s,
         detected_at, slack_ts, url, file_id) = row[:_NCOL]
        if str(date_s).strip() not in (day_str, day_alt_strict, day_alt_compact):
            continue
        view = (str(view_s).strip() or DEFAULT_VIEW_LABEL)
        out[(view, str(goods_no))] = {
            "brand": str(brand),
            "product_name": str(name),
            "is_hero": str(hero_s).strip() == "히어로",
            "detected_at": str(detected_at),
            "slack_ts": str(slack_ts),
            "screenshot_url": str(url),
            "file_id": str(file_id),
        }
    return out


def append_alert(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    views: list[str],
    goods_no: str,
    brand: str,
    product_name: str,
    is_hero: bool,
    detected_at: datetime,
    slack_ts: str = "",
    screenshot_url: str = "",
    file_id: str = "",
    log: logging.Logger | None = None,
) -> int:
    """한 알림에 묶인 뷰들(views)을 각각 한 행으로 append. 반환=쓴 행 수.

    한 상품이 같은 시각에 [전체]·[남자] 동시 1위여도 슬랙 메시지는 1개지만
    원장은 뷰별 1행 — 다음 트리거의 dedup 키가 (뷰, goods_no)이기 때문.
    """
    if not views:
        return 0
    _ensure_tab(sheets_service, sheet_id, log=log)
    rows = [[
        target_day.isoformat(),
        view,
        str(goods_no),
        brand,
        product_name,
        "히어로" if is_hero else "",
        detected_at.isoformat(timespec="seconds"),
        slack_ts,
        screenshot_url,
        file_id,
    ] for view in views]

    _execute(sheets_service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{RANK1_TAB}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ))
    if log:
        log.info(persona.step(
            f"[{RANK1_TAB}] 기록 {len(rows)}행 — {goods_no} ({'·'.join(views)})"
        ))
    return len(rows)


def delete_alert(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    views: list[str],
    goods_no: str,
    log: logging.Logger | None = None,
) -> int:
    """발송이 실패했을 때 미리 써 둔 dedup 행을 되돌린다 (10분 뒤 재시도 가능하게).

    "발송 전 기록"은 중복을 막지만, 슬랙이 죽으면 알림이 하루 통째로 유실된다.
    발송 실패가 확인된 경우에만 이 함수로 마커를 지워 다음 트리거가 다시 시도하게 한다.
    """
    try:
        meta = _execute(sheets_service.spreadsheets().get(
            spreadsheetId=sheet_id, fields="sheets(properties(title,sheetId))"
        ))
        gid = next(
            (s["properties"]["sheetId"] for s in meta.get("sheets", [])
             if s["properties"]["title"] == RANK1_TAB),
            None,
        )
        if gid is None:
            return 0
        resp = _execute(sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{RANK1_TAB}'!A2:C",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ))
    except Exception:
        return 0

    day_str = target_day.isoformat()
    day_alt_strict = f"{target_day.year}. {target_day.month}. {target_day.day}."
    day_alt_compact = f"{target_day.year}.{target_day.month}.{target_day.day}."
    want = set(views)

    kill: list[int] = []
    for i, row in enumerate(resp.get("values", []), start=2):
        if len(row) < 3:
            continue
        if str(row[0]).strip() not in (day_str, day_alt_strict, day_alt_compact):
            continue
        view = (str(row[1]).strip() or DEFAULT_VIEW_LABEL)
        if view in want and str(row[2]) == str(goods_no):
            kill.append(i)

    if not kill:
        return 0
    # 아래에서부터 지워야 인덱스가 밀리지 않는다.
    requests = [{"deleteDimension": {"range": {
        "sheetId": gid, "dimension": "ROWS", "startIndex": i - 1, "endIndex": i,
    }}} for i in sorted(kill, reverse=True)]
    try:
        _execute(sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ))
    except Exception as e:
        if log:
            log.warning(persona.step(f"[{RANK1_TAB}] 롤백 실패 — {e}"))
        return 0
    if log:
        log.info(persona.step(f"[{RANK1_TAB}] 발송 실패 롤백 {len(kill)}행 — {goods_no}"))
    return len(kill)


def update_alert_meta(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    views: list[str],
    goods_no: str,
    slack_ts: str = "",
    screenshot_url: str = "",
    file_id: str = "",
    log: logging.Logger | None = None,
) -> None:
    """발송 후 슬랙ts/스크린샷 URL을 해당 행들에 채운다 (실패해도 치명적이지 않음).

    행 자체는 발송 전에 append 되므로 여기서는 H:J 컬럼만 덧쓴다.
    """
    try:
        resp = _execute(sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{RANK1_TAB}'!A2:C",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ))
    except Exception:
        return

    day_str = target_day.isoformat()
    day_alt_strict = f"{target_day.year}. {target_day.month}. {target_day.day}."
    day_alt_compact = f"{target_day.year}.{target_day.month}.{target_day.day}."
    want = set(views)

    data = []
    for i, row in enumerate(resp.get("values", []), start=2):
        if len(row) < 3:
            continue
        if str(row[0]).strip() not in (day_str, day_alt_strict, day_alt_compact):
            continue
        view = (str(row[1]).strip() or DEFAULT_VIEW_LABEL)
        if view not in want or str(row[2]) != str(goods_no):
            continue
        data.append({
            "range": f"'{RANK1_TAB}'!H{i}:J{i}",
            "values": [[slack_ts, screenshot_url, file_id]],
        })

    if not data:
        return
    try:
        _execute(sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ))
    except Exception as e:
        if log:
            log.warning(persona.step(f"[{RANK1_TAB}] 메타 갱신 실패(무시) — {e}"))
