"""Google Sheet에 랭킹 데이터 적재 + 조회.

탭 구조:
- Long 탭: 시각×상품 단위 raw row (Hourly가 매시간 append, Daily가 read)
  날짜 / 시간 / goods_no / 랭킹 순위 / 브랜드 / 상품명 / 히어로여부
- Wide 탭: 일자×상품 단위, 시간을 컬럼으로 (Daily가 09:00에 일일 요약 append)
  날짜 / goods_no / 브랜드 / 상품명 / 히어로여부 / 00:00 / 00:30 / ... / 23:30

탭이 없으면 헤더와 함께 자동 생성.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from soo import persona


LONG_TAB = "Long"
WIDE_TAB = "Wide"

LONG_HEADER = ["날짜", "시간", "goods_no", "랭킹 순위", "브랜드", "상품명", "히어로여부"]

# Wide tab — 매시간 슬롯 24개 (00시~23시)
TIME_SLOTS = [f"{h:02d}:00" for h in range(24)]
WIDE_HEADER = ["날짜", "goods_no", "브랜드", "상품명", "히어로여부"] + TIME_SLOTS


def _hero_label(is_hero: bool) -> str:
    return "히어로" if is_hero else ""


def _ensure_tab(sheets_service: Any, sheet_id: str, tab: str, header: list[str]) -> None:
    """탭이 없으면 만들고 헤더 작성. 있으면 헤더가 비어있을 때만 헤더 채움."""
    meta = sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets.properties"
    ).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}

    if tab not in existing:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).execute()
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ).execute()
        return

    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A1:1"
    ).execute()
    if not resp.get("values"):
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ).execute()


# ───────────────────────────────────────────────────────────
# Hourly: 매시간 raw row 를 Long 탭에 append
# ───────────────────────────────────────────────────────────
def has_hour_data(
    sheets_service: Any,
    sheet_id: str,
    ts: datetime,
) -> bool:
    """Long 탭에 ts 시각(YYYY-MM-DD, HH:00) 슬롯의 행이 이미 있으면 True.

    같은 시간 내 중복 trigger를 멱등 처리하기 위해 사용. 마지막 ~200행만 검사 —
    concurrency 그룹이 활성이면 직전 적재가 마지막 영역에 몰려있다.
    """
    target_date = ts.date().isoformat()
    target_time = ts.strftime("%H:%M")
    try:
        meta = sheets_service.spreadsheets().get(
            spreadsheetId=sheet_id,
            fields="sheets(properties(title,gridProperties(rowCount)))",
        ).execute()
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
    start = max(2, row_count - 200)
    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"'{LONG_TAB}'!A{start}:B{row_count}",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    rows = resp.get("values", [])
    for row in rows:
        if len(row) >= 2 and str(row[0]) == target_date and str(row[1]) == target_time:
            return True
    return False


def append_realtime(
    sheets_service: Any,
    sheet_id: str,
    ts: datetime,
    items: list[tuple],
    log: logging.Logger,
) -> int:
    """items: [(goods_no, rank, brand, product_name, is_hero), ...]

    valueInputOption="RAW" — 날짜/시간/숫자 자동 파싱 막아서 ISO 문자열 그대로 보존.
    USER_ENTERED 쓰면 Sheets가 날짜 타입으로 변환 → daily가 read할 때 로케일 포맷
    (예: "2026. 5. 1.")으로 돌아와서 ISO 문자열 비교에 실패함.
    """
    _ensure_tab(sheets_service, sheet_id, LONG_TAB, LONG_HEADER)

    rows = []
    for goods_no, rank, brand, product_name, is_hero in items:
        rows.append([
            ts.date().isoformat(),
            ts.strftime("%H:%M"),
            str(goods_no),
            int(rank),
            brand,
            product_name,
            _hero_label(bool(is_hero)),
        ])

    if not rows:
        log.info(persona.step("Long 탭 append: 0행 (매칭된 상품 없음)"))
        return 0

    sheets_service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{LONG_TAB}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    log.info(persona.step(f"Long 탭 append: {len(rows)}행"))
    return len(rows)


# ───────────────────────────────────────────────────────────
# Daily: Long 탭에서 특정 날짜 데이터 read → ranking_db rows 형식으로
# ───────────────────────────────────────────────────────────
def read_day_long(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
) -> list[dict]:
    """Long 탭에서 target_day 의 모든 row를 읽어 dict 리스트로 반환.

    반환 row 구조 (기존 ranking_db.rankings_for_day 와 호환):
      {"ts": "YYYY-MM-DDTHH:MM:00", "goods_no": str, "rank": int,
       "brand": str, "product_name": str, "is_hero": bool}
    """
    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"'{LONG_TAB}'!A2:G",  # 헤더 제외
        # UNFORMATTED + FORMATTED_STRING — 날짜/시간 셀이 USER_ENTERED로
        # 파싱돼있어도 사람이 읽을 수 있는 문자열로 받고, 숫자(goods_no, rank)는
        # 그대로 받는다. 기존 데이터 호환을 위해 ISO/로케일 양쪽 매칭.
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    raw = resp.get("values", [])

    day_str = target_day.isoformat()
    # 호환: "2026. 5. 1." / "2026.5.1." 같은 한국 로케일 포맷도 매칭
    day_alt_strict = f"{target_day.year}. {target_day.month}. {target_day.day}."
    day_alt_compact = f"{target_day.year}.{target_day.month}.{target_day.day}."
    out: list[dict] = []
    for row in raw:
        if len(row) < 7:
            row = row + [""] * (7 - len(row))
        date_s, time_s, goods_no, rank_s, brand, product_name, hero_s = row[:7]
        date_str = str(date_s).strip()
        if date_str not in (day_str, day_alt_strict, day_alt_compact):
            continue
        try:
            rank = int(rank_s)
        except (TypeError, ValueError):
            continue
        # 시간 정규화: "08:00" 또는 "오전 8:00:00" 등 → "HH:00"
        time_str = str(time_s).strip()
        if ":" in time_str:
            # "8:00:00", "오전 8:00:00", "08:00" 등에서 시간만 추출
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
# Daily: Wide 탭에 일일 요약 append
# ───────────────────────────────────────────────────────────
def _build_wide_rows(rows: list[dict], target_day: date) -> list[list]:
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
        row = [day_str, gn, info["brand"], info["product_name"], _hero_label(info["is_hero"])]
        for slot in TIME_SLOTS:
            r = info["ranks"].get(slot)
            row.append(r if r is not None else "")
        out.append(row)
    return out


def append_day_wide(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    rows: list[dict],
    log: logging.Logger,
) -> int:
    if not rows:
        log.info(persona.step("Wide 탭 append: 0행 (데이터 없음)"))
        return 0

    _ensure_tab(sheets_service, sheet_id, WIDE_TAB, WIDE_HEADER)

    wide_data = _build_wide_rows(rows, target_day)
    if not wide_data:
        return 0

    sheets_service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{WIDE_TAB}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": wide_data},
    ).execute()
    log.info(persona.step(f"Wide 탭 append: {len(wide_data)}행"))
    return len(wide_data)


# ───────────────────────────────────────────────────────────
# Daily 멱등성: Wide 탭에 같은 target_day 행이 이미 있는지 체크
# ───────────────────────────────────────────────────────────
def has_day_wide(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
) -> bool:
    """Wide 탭 A열(날짜)에 target_day 가 이미 있으면 True."""
    try:
        resp = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{WIDE_TAB}'!A2:A",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ).execute()
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
        if v in (day_str, day_alt_strict, day_alt_compact):
            return True
    return False
