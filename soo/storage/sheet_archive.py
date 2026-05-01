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
def append_realtime(
    sheets_service: Any,
    sheet_id: str,
    ts: datetime,
    items: list[tuple],
    log: logging.Logger,
) -> int:
    """items: [(goods_no, rank, brand, product_name, is_hero), ...]"""
    _ensure_tab(sheets_service, sheet_id, LONG_TAB, LONG_HEADER)

    rows = []
    for goods_no, rank, brand, product_name, is_hero in items:
        rows.append([
            ts.date().isoformat(),
            ts.strftime("%H:%M"),
            goods_no,
            rank,
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
        valueInputOption="USER_ENTERED",
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
    ).execute()
    raw = resp.get("values", [])

    day_str = target_day.isoformat()
    out: list[dict] = []
    for row in raw:
        if len(row) < 7:
            row = row + [""] * (7 - len(row))
        date_s, time_s, goods_no, rank_s, brand, product_name, hero_s = row[:7]
        if date_s != day_str:
            continue
        try:
            rank = int(rank_s)
        except (TypeError, ValueError):
            continue
        try:
            ts_iso = f"{date_s}T{time_s}:00"
        except Exception:
            continue
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
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": wide_data},
    ).execute()
    log.info(persona.step(f"Wide 탭 append: {len(wide_data)}행"))
    return len(wide_data)
