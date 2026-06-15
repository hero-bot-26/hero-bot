"""Google Sheet에 랭킹 데이터 적재 + 조회.

탭 구조 (뷰 컬럼 추가 후):
- Long 탭: 시각×뷰×상품 단위 raw row (Hourly가 매시간 뷰별로 append, Daily가 read)
  날짜 / 시간 / 뷰 / goods_no / 랭킹 순위 / 브랜드 / 상품명 / 히어로여부
- Wide 탭: 일자×뷰×상품 단위, 시간을 컬럼으로 (Daily가 09:00에 뷰별 일일 요약 append)
  날짜 / 뷰 / goods_no / 브랜드 / 상품명 / 히어로여부 / 00:00 / ... / 23:00

뷰 = "전체"/"남자"/"여자" (무신사 랭킹 페이지 우측 성별 필터).
기존 7컬럼 Long / 5+24컬럼 Wide 스키마(뷰 없음)는 자동 마이그레이션 — 컬럼 insert + 헤더 갱신.
기존 행의 빈 뷰 셀은 read 시 "전체"로 해석.

탭이 없으면 헤더와 함께 자동 생성.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

from googleapiclient.errors import HttpError

from soo import persona, DEFAULT_VIEW_LABEL


def _execute(request: Any, num_retries: int = 5) -> Any:
    """Sheets API 호출(.execute())을 429/5xx에서 지수 백오프로 재시도.

    무신사 일일 리포트는 3개 뷰를 순차 처리하며 뷰당 여러 번 read/write 한다.
    마지막 뷰([여자])에서 분당 쿼터(60 req/min·user)에 걸려 Wide 적재가 429로
    실패하던 사고(2026-06) 대응. 1초→2→4→…(최대 30초) 백오프로 다음 분 쿼터 창까지
    버틴다. 모든 재시도 소진 시 원래 HttpError를 그대로 raise.
    """
    delay = 1.0
    for attempt in range(num_retries + 1):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status is not None and int(status) in (429, 500, 502, 503) and attempt < num_retries:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            raise


LONG_TAB = "Long"
WIDE_TAB = "Wide"

LONG_HEADER = ["날짜", "시간", "뷰", "goods_no", "랭킹 순위", "브랜드", "상품명", "히어로여부"]
LONG_HEADER_OLD = ["날짜", "시간", "goods_no", "랭킹 순위", "브랜드", "상품명", "히어로여부"]
_LONG_VIEW_COL_IDX = 2  # 0-based, "뷰" 컬럼이 들어갈 위치 (= C열)

# Wide tab — 매시간 슬롯 24개 (00시~23시)
TIME_SLOTS = [f"{h:02d}:00" for h in range(24)]
WIDE_HEADER = ["날짜", "뷰", "goods_no", "브랜드", "상품명", "히어로여부"] + TIME_SLOTS
WIDE_HEADER_OLD = ["날짜", "goods_no", "브랜드", "상품명", "히어로여부"] + TIME_SLOTS
_WIDE_VIEW_COL_IDX = 1  # 0-based, "뷰" 컬럼이 들어갈 위치 (= B열)


def _hero_label(is_hero: bool) -> str:
    return "히어로" if is_hero else ""


def _col_letter(idx_zero_based: int) -> str:
    """0=A, 1=B, ..."""
    return chr(ord("A") + idx_zero_based)


def _migrate_view_column(
    sheets_service: Any,
    sheet_id: str,
    tab: str,
    view_col_idx: int,
    expected_old_header: list[str],
    expected_new_header: list[str],
    log: logging.Logger | None = None,
) -> bool:
    """탭이 옛 헤더(뷰 컬럼 없음)면 view_col_idx 위치에 컬럼 insert + "뷰" 헤더 작성.

    이미 새 헤더면 no-op. 탭이 없으면 호출 측(_ensure_tab)에서 처리. True=마이그 발생.
    """
    meta = _execute(sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets(properties(title,sheetId))"
    ))
    sheet_props = next(
        (s["properties"] for s in meta.get("sheets", []) if s["properties"]["title"] == tab),
        None,
    )
    if not sheet_props:
        return False  # 탭 자체가 없음 — _ensure_tab이 새 헤더로 만들 것

    resp = _execute(sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A1:1"
    ))
    current = (resp.get("values") or [[]])[0]

    if current == expected_new_header:
        return False
    if not current:
        # 헤더가 비어 있으면 그냥 새 헤더로 작성
        _execute(sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [expected_new_header]},
        ))
        return False

    if current == expected_old_header:
        _execute(sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_props["sheetId"],
                        "dimension": "COLUMNS",
                        "startIndex": view_col_idx,
                        "endIndex": view_col_idx + 1,
                    },
                    "inheritFromBefore": False,
                },
            }]},
        ))
        _execute(sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!{_col_letter(view_col_idx)}1",
            valueInputOption="RAW",
            body={"values": [["뷰"]]},
        ))
        if log:
            log.info(persona.step(f"[{tab}] 뷰 컬럼 {_col_letter(view_col_idx)}열에 자동 insert"))
        return True

    # 알 수 없는 헤더 — 손대지 않고 경고만
    if log:
        log.warning(persona.step(
            f"[{tab}] 헤더가 기대값과 다름. 자동 마이그 skip. 현재={current}"
        ))
    return False


def _ensure_tab(
    sheets_service: Any,
    sheet_id: str,
    tab: str,
    header: list[str],
    old_header: list[str] | None = None,
    view_col_idx: int | None = None,
    log: logging.Logger | None = None,
) -> None:
    """탭이 없으면 만들고 헤더 작성. 있으면:
       - 헤더가 비어있으면 새 헤더 채움
       - 헤더가 옛 스키마(view 없음)이면 마이그 (insert column + 헤더 갱신)
    """
    meta = _execute(sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets.properties"
    ))
    existing = {s["properties"]["title"] for s in meta["sheets"]}

    if tab not in existing:
        _execute(sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ))
        _execute(sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ))
        return

    if old_header is not None and view_col_idx is not None:
        _migrate_view_column(
            sheets_service, sheet_id, tab,
            view_col_idx=view_col_idx,
            expected_old_header=old_header,
            expected_new_header=header,
            log=log,
        )
        return

    resp = _execute(sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A1:1"
    ))
    if not resp.get("values"):
        _execute(sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ))


# ───────────────────────────────────────────────────────────
# Hourly: 매시간 raw row 를 Long 탭에 append
# ───────────────────────────────────────────────────────────
def has_hour_data(
    sheets_service: Any,
    sheet_id: str,
    ts: datetime,
    view: str,
) -> bool:
    """Long 탭에 (ts 시각, view) 슬롯의 행이 이미 있으면 True.

    뷰별 멱등 — 동일 시간에 전체는 적재됐지만 남자는 아직이면 남자만 진행.
    마지막 ~600행만 검사 (3개 뷰 × Top 100 × 2시간 정도 여유).
    """
    target_date = ts.date().isoformat()
    target_time = ts.strftime("%H:%M")
    try:
        meta = _execute(sheets_service.spreadsheets().get(
            spreadsheetId=sheet_id,
            fields="sheets(properties(title,gridProperties(rowCount)))",
        ))
    except Exception:
        return False
    long_props = next(
        (s["properties"] for s in meta.get("sheets", [])
         if s["properties"]["title"] == LONG_TAB),
        None,
    )
    if not long_props:
        return False
    row_count = long_props.get("gridProperties", {}).get("rowCount", 0)
    if row_count < 2:
        return False
    start = max(2, row_count - 600)
    resp = _execute(sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"'{LONG_TAB}'!A{start}:C{row_count}",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ))
    rows = resp.get("values", [])
    for row in rows:
        if len(row) < 2:
            continue
        if str(row[0]) != target_date or str(row[1]) != target_time:
            continue
        row_view = (str(row[2]).strip() if len(row) >= 3 else "") or DEFAULT_VIEW_LABEL
        if row_view == view:
            return True
    return False


def append_realtime(
    sheets_service: Any,
    sheet_id: str,
    ts: datetime,
    view: str,
    items: list[tuple],
    log: logging.Logger,
) -> int:
    """items: [(goods_no, rank, brand, product_name, is_hero), ...]

    valueInputOption="RAW" — 날짜/시간/숫자 자동 파싱 막아서 ISO 문자열 그대로 보존.
    """
    _ensure_tab(
        sheets_service, sheet_id, LONG_TAB, LONG_HEADER,
        old_header=LONG_HEADER_OLD, view_col_idx=_LONG_VIEW_COL_IDX, log=log,
    )

    rows = []
    for goods_no, rank, brand, product_name, is_hero in items:
        rows.append([
            ts.date().isoformat(),
            ts.strftime("%H:%M"),
            view,
            str(goods_no),
            int(rank),
            brand,
            product_name,
            _hero_label(bool(is_hero)),
        ])

    if not rows:
        log.info(persona.step(f"Long 탭 append [{view}]: 0행 (매칭된 상품 없음)"))
        return 0

    _execute(sheets_service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{LONG_TAB}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ))
    log.info(persona.step(f"Long 탭 append [{view}]: {len(rows)}행"))
    return len(rows)


# ───────────────────────────────────────────────────────────
# Daily: Long 탭에서 특정 날짜 × 뷰 데이터 read → ranking_db rows 형식으로
# ───────────────────────────────────────────────────────────
def read_day_long(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    view: str | None = None,
) -> list[dict]:
    """Long 탭에서 target_day 의 모든 row를 읽어 dict 리스트로 반환.

    view 지정 시 해당 뷰 행만. None이면 전체 뷰 합쳐서 반환 (뷰 필드는 row에 포함).
    빈 뷰 셀은 DEFAULT_VIEW_LABEL("전체")로 해석 — 기존 데이터 호환.

    반환 row 구조:
      {"ts": "YYYY-MM-DDTHH:MM:00", "view": str, "goods_no": str, "rank": int,
       "brand": str, "product_name": str, "is_hero": bool}
    """
    resp = _execute(sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"'{LONG_TAB}'!A2:H",  # 헤더 제외, 8컬럼
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ))
    raw = resp.get("values", [])

    day_str = target_day.isoformat()
    day_alt_strict = f"{target_day.year}. {target_day.month}. {target_day.day}."
    day_alt_compact = f"{target_day.year}.{target_day.month}.{target_day.day}."
    out: list[dict] = []
    for row in raw:
        if len(row) < 8:
            row = row + [""] * (8 - len(row))
        date_s, time_s, view_s, goods_no, rank_s, brand, product_name, hero_s = row[:8]
        date_str = str(date_s).strip()
        if date_str not in (day_str, day_alt_strict, day_alt_compact):
            continue
        row_view = (str(view_s).strip() or DEFAULT_VIEW_LABEL)
        if view is not None and row_view != view:
            continue
        try:
            rank = int(rank_s)
        except (TypeError, ValueError):
            continue
        # 시간 정규화: "08:00" 또는 "오전 8:00:00" 등 → "HH:00"
        time_str = str(time_s).strip()
        if ":" in time_str:
            parts = time_str.replace("오전", "").replace("오후", "").strip().split(":")
            try:
                hour = int(parts[0])
                if "오후" in str(time_s) and hour < 12:
                    hour += 12
                time_str = f"{hour:02d}:00"
            except (ValueError, IndexError):
                continue
        ts_iso = f"{day_str}T{time_str}:00"
        out.append({
            "ts": ts_iso,
            "view": row_view,
            "goods_no": str(goods_no),
            "rank": rank,
            "brand": brand,
            "product_name": product_name,
            "is_hero": hero_s == "히어로",
        })
    return out


def count_snapshots(rows: list[dict]) -> int:
    """rows에서 unique 시각 수 반환 (캡처 횟수 추정)."""
    return len({r["ts"] for r in rows})


# ───────────────────────────────────────────────────────────
# Daily: Wide 탭에 일일 요약 append (뷰별 행)
# ───────────────────────────────────────────────────────────
def _build_wide_rows(rows: list[dict], target_day: date, view: str) -> list[list]:
    by_goods: dict[str, dict] = {}
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        slot = dt.strftime("%H:%M")
        gn = r["goods_no"]
        if gn not in by_goods:
            by_goods[gn] = {
                "brand": r["brand"],
                "product_name": r["product_name"],
                "is_hero": False,
                "ranks": {},
            }
        by_goods[gn]["is_hero"] = by_goods[gn]["is_hero"] or bool(r["is_hero"])
        by_goods[gn]["ranks"][slot] = r["rank"]

    items = list(by_goods.items())
    items.sort(key=lambda kv: (
        not kv[1]["is_hero"],
        min(kv[1]["ranks"].values()) if kv[1]["ranks"] else 999,
    ))

    out = []
    day_str = target_day.isoformat()
    for gn, info in items:
        row = [day_str, view, gn, info["brand"], info["product_name"], _hero_label(info["is_hero"])]
        for slot in TIME_SLOTS:
            r = info["ranks"].get(slot)
            row.append(r if r is not None else "")
        out.append(row)
    return out


def append_day_wide(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    view: str,
    rows: list[dict],
    log: logging.Logger,
) -> int:
    if not rows:
        log.info(persona.step(f"Wide 탭 append [{view}]: 0행 (데이터 없음)"))
        return 0

    _ensure_tab(
        sheets_service, sheet_id, WIDE_TAB, WIDE_HEADER,
        old_header=WIDE_HEADER_OLD, view_col_idx=_WIDE_VIEW_COL_IDX, log=log,
    )

    wide_data = _build_wide_rows(rows, target_day, view)
    if not wide_data:
        return 0

    _execute(sheets_service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{WIDE_TAB}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": wide_data},
    ))
    log.info(persona.step(f"Wide 탭 append [{view}]: {len(wide_data)}행"))
    return len(wide_data)


# ───────────────────────────────────────────────────────────
# Daily 멱등성: Wide 탭에 (target_day, view) 행이 이미 있는지 체크
# ───────────────────────────────────────────────────────────
def has_day_wide(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    view: str,
) -> bool:
    """Wide 탭에 (날짜=target_day, 뷰=view) 행이 이미 있으면 True.

    빈 뷰 셀은 DEFAULT_VIEW_LABEL("전체")로 간주 — 기존 데이터 호환.
    """
    try:
        resp = _execute(sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{WIDE_TAB}'!A2:B",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ))
    except Exception:
        return False
    rows = resp.get("values", [])
    day_str = target_day.isoformat()
    day_alt_strict = f"{target_day.year}. {target_day.month}. {target_day.day}."
    day_alt_compact = f"{target_day.year}.{target_day.month}.{target_day.day}."
    for row in rows:
        if not row:
            continue
        v = str(row[0]).strip()
        if v not in (day_str, day_alt_strict, day_alt_compact):
            continue
        row_view = (str(row[1]).strip() if len(row) >= 2 else "") or DEFAULT_VIEW_LABEL
        if row_view == view:
            return True
    return False
