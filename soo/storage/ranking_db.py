"""랭킹 스냅샷 SQLite 저장소.

- snapshots: 매시간 캡처 메타 (timestamp, fetched count, hero matched count)
- rankings: 각 스냅샷별 무탠 계열 상품 랭킹 (PRIMARY KEY ts + goods_no)
- actions: 사용자 자발적 액션 로그 (옵션 — `m \"어제 카플친 발송\"` 으로 기록)
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts          TEXT PRIMARY KEY,        -- ISO 8601, 예: 2026-04-30T14:00:00
    captured_at TEXT NOT NULL,           -- 실제 캡처 시각 (정각 ts와 같거나 약간 차이)
    fetched_total INTEGER NOT NULL,      -- 페이지에서 fetch한 전체 (보통 300)
    musinsa_brand_count INTEGER NOT NULL,-- 무탠 계열만 필터한 수
    hero_in_chart INTEGER NOT NULL       -- hero UID 중 진입 수
);

CREATE TABLE IF NOT EXISTS rankings (
    ts          TEXT NOT NULL,
    goods_no    TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    brand       TEXT NOT NULL,
    product_name TEXT NOT NULL,
    is_hero     INTEGER NOT NULL DEFAULT 0,  -- 0/1
    PRIMARY KEY (ts, goods_no)
);

CREATE INDEX IF NOT EXISTS idx_rankings_goods ON rankings(goods_no);
CREATE INDEX IF NOT EXISTS idx_rankings_hero  ON rankings(is_hero);

CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at   TEXT NOT NULL,           -- 입력 시각
    target_date TEXT NOT NULL,           -- 어느 날짜 액션인지 (YYYY-MM-DD)
    text        TEXT NOT NULL
);
"""


@dataclass
class Snapshot:
    ts: str
    captured_at: str
    fetched_total: int
    musinsa_brand_count: int
    hero_in_chart: int


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as con:
        con.executescript(SCHEMA)


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    init_db(path)
    con = sqlite3.connect(str(path))
    try:
        yield con
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────
# 쓰기
# ─────────────────────────────────────────────────────────────────

def save_snapshot(
    db_path: Path,
    ts: datetime,
    captured_at: datetime,
    fetched_total: int,
    items: Iterable[tuple],  # (goods_no, rank, brand, product_name, is_hero)
) -> None:
    """스냅샷 1개 저장 — snapshots + rankings 모두."""
    items_list = list(items)
    musinsa_count = len(items_list)
    hero_count = sum(1 for x in items_list if x[4])

    ts_str = ts.isoformat(timespec="seconds")
    cap_str = captured_at.isoformat(timespec="seconds")

    with connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?)",
            (ts_str, cap_str, fetched_total, musinsa_count, hero_count),
        )
        con.execute("DELETE FROM rankings WHERE ts = ?", (ts_str,))
        con.executemany(
            "INSERT INTO rankings (ts, goods_no, rank, brand, product_name, is_hero) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(ts_str, gn, r, b, name, int(bool(h))) for (gn, r, b, name, h) in items_list],
        )
        con.commit()


def log_action(db_path: Path, target_date: date, text: str) -> None:
    with connect(db_path) as con:
        con.execute(
            "INSERT INTO actions (logged_at, target_date, text) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), target_date.isoformat(), text),
        )
        con.commit()


# ─────────────────────────────────────────────────────────────────
# 읽기 (일일 리포트용)
# ─────────────────────────────────────────────────────────────────

def snapshots_for_day(db_path: Path, day: date) -> list[Snapshot]:
    start = datetime.combine(day, time.min).isoformat(timespec="seconds")
    end = datetime.combine(day + timedelta(days=1), time.min).isoformat(timespec="seconds")
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT ts, captured_at, fetched_total, musinsa_brand_count, hero_in_chart "
            "FROM snapshots WHERE ts >= ? AND ts < ? ORDER BY ts",
            (start, end),
        ).fetchall()
    return [Snapshot(*r) for r in rows]


def rankings_for_day(db_path: Path, day: date) -> list[dict]:
    start = datetime.combine(day, time.min).isoformat(timespec="seconds")
    end = datetime.combine(day + timedelta(days=1), time.min).isoformat(timespec="seconds")
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT ts, goods_no, rank, brand, product_name, is_hero "
            "FROM rankings WHERE ts >= ? AND ts < ? ORDER BY ts, rank",
            (start, end),
        ).fetchall()
    return [
        {
            "ts": ts, "goods_no": gn, "rank": r, "brand": b,
            "product_name": name, "is_hero": bool(h),
        }
        for (ts, gn, r, b, name, h) in rows
    ]


def latest_rankings(db_path: Path, goods_no: str, limit: int = 24) -> list[tuple[str, int]]:
    """특정 상품의 최근 N개 (ts, rank) 시계열."""
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT ts, rank FROM rankings WHERE goods_no = ? ORDER BY ts DESC LIMIT ?",
            (goods_no, limit),
        ).fetchall()
    return list(reversed(rows))


def actions_for_day(db_path: Path, day: date) -> list[dict]:
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT logged_at, text FROM actions WHERE target_date = ? ORDER BY logged_at",
            (day.isoformat(),),
        ).fetchall()
    return [{"logged_at": la, "text": t} for la, t in rows]


def purge_day(db_path: Path, day: date) -> dict:
    """특정 날짜의 snapshots + rankings 삭제. archive 후 호출."""
    start = datetime.combine(day, time.min).isoformat(timespec="seconds")
    end = datetime.combine(day + timedelta(days=1), time.min).isoformat(timespec="seconds")
    with connect(db_path) as con:
        cur = con.execute("DELETE FROM rankings WHERE ts >= ? AND ts < ?", (start, end))
        n_rank = cur.rowcount
        cur = con.execute("DELETE FROM snapshots WHERE ts >= ? AND ts < ?", (start, end))
        n_snap = cur.rowcount
        # actions 도 같이 정리 (선택 — 액션 로그도 archive 한 후라면)
        cur = con.execute("DELETE FROM actions WHERE target_date = ?", (day.isoformat(),))
        n_act = cur.rowcount
        con.commit()
    return {"rankings": n_rank, "snapshots": n_snap, "actions": n_act}
