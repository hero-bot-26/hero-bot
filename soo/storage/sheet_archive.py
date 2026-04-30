"""일일 랭킹 데이터를 Google Sheet에 archive.

두 개 탭에 cumulative append:
- Long 탭: 시각×상품 단위 (피벗/필터 분석용)
  날짜 / 시간 / goods_no / 랭킹 순위 / 브랜드 / 상품명 / 히어로여부
- Wide 탭: 일자×상품 단위, 시간을 컬럼으로 (한 눈에 시간대별 진입 패턴)
  날짜 / goods_no / 브랜드 / 상품명 / 히어로여부 / 00:00 / 00:30 / ... / 23:30

탭이 없으면 헤더와 함께 자동 생성.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from soo import persona


LONG_TAB = "Long"
WIDE_TAB = "Wide"

LONG_HEADER = ["날짜", "시간", "goods_no", "랭킹 순위", "브랜드", "상품명", "히어로여부"]

# Wide tab — 30분 슬롯 48개
TIME_SLOTS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
WIDE_HEADER = ["날짜", "goods_no", "브랜드", "상품명", "히어로여부"] + TIME_SLOTS


def _hero_label(is_hero: bool) -> str:
    return "히어로" if is_hero else ""


def _ensure_tab(sheets_service: Any, sheet_id: str, tab: str, header: list[str]) -> None:
    """탭이 없으면 만들고 헤더 작성. 있으면 헤더가 비어있을 때만 헤더 채움."""
    meta = sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets.properties"
    ).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    if tab not in existing:
        # 새 탭 생성
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).execute()
        # 헤더 쓰기
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ).execute()
        return

    # 기존 탭 — 1행 비어있으면 헤더 추가
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


def _build_long_rows(rows: list[dict]) -> list[list]:
    """rankings rows → Long format 2D list.

    rows: [{"ts": "...", "goods_no", "rank", "brand", "product_name", "is_hero"}]
    """
    out = []
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        out.append([
            dt.date().isoformat(),                # 날짜
            dt.strftime("%H:%M"),                 # 시간
            r["goods_no"],
            r["rank"],
            r["brand"],
            r["product_name"],
            _hero_label(bool(r["is_hero"])),
        ])
    return out


def _build_wide_rows(rows: list[dict], target_day: date) -> list[list]:
    """rankings rows → Wide format. 한 행 = (날짜 × goods_no), 컬럼 = 시간 슬롯."""
    # goods_no별로 모으기
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

    # 정렬: 히어로 먼저, 그 다음 최저 peak rank 순
    items = list(by_goods.items())
    items.sort(key=lambda kv: (
        not kv[1]["is_hero"],  # 히어로 먼저
        min(kv[1]["ranks"].values()) if kv[1]["ranks"] else 999,
    ))

    out = []
    day_str = target_day.isoformat()
    for gn, info in items:
        row = [
            day_str,
            gn,
            info["brand"],
            info["product_name"],
            _hero_label(info["is_hero"]),
        ]
        for slot in TIME_SLOTS:
            r = info["ranks"].get(slot)
            row.append(r if r is not None else "")
        out.append(row)
    return out


def append_day(
    sheets_service: Any,
    sheet_id: str,
    target_day: date,
    rows: list[dict],
    log: logging.Logger,
) -> dict:
    """target_day의 모든 rankings rows를 Long + Wide 탭에 append.

    rows: ranking_db.rankings_for_day() 결과.
    Returns: {"long_rows": int, "wide_rows": int}
    """
    if not rows:
        log.info(persona.step("archive 할 데이터 없음 — 스킵"))
        return {"long_rows": 0, "wide_rows": 0}

    # 탭 보장
    _ensure_tab(sheets_service, sheet_id, LONG_TAB, LONG_HEADER)
    _ensure_tab(sheets_service, sheet_id, WIDE_TAB, WIDE_HEADER)

    # Long append
    long_data = _build_long_rows(rows)
    if long_data:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"'{LONG_TAB}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": long_data},
        ).execute()
        log.info(persona.step(f"Long 탭 append: {len(long_data)}행"))

    # Wide append
    wide_data = _build_wide_rows(rows, target_day)
    if wide_data:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"'{WIDE_TAB}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": wide_data},
        ).execute()
        log.info(persona.step(f"Wide 탭 append: {len(wide_data)}행"))

    return {"long_rows": len(long_data), "wide_rows": len(wide_data)}
