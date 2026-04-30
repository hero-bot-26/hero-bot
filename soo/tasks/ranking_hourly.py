"""매시간 정각 트리거 — 무신사 랭킹 Top 300에서 무탠 계열 추출 → SQLite 저장."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from soo import persona
from soo.scrapers.musinsa_ranking import fetch_top, filter_by_brand
from soo.storage import ranking_db


def run(
    db_path: Path,
    brand_keywords: list[str],
    hero_uids: set[str],
    log: logging.Logger,
    top_n: int = 300,
    section_id: int = 199,
    sub_pan: str | None = "product",
) -> dict:
    """1회 캡처 + 저장. 결과 요약 dict 반환."""
    captured_at = datetime.now()
    # ts는 30분 슬롯으로 정규화 (분 = 0 또는 30)
    minute_slot = 0 if captured_at.minute < 30 else 30
    ts = captured_at.replace(minute=minute_slot, second=0, microsecond=0)

    log.info(persona.starting_task(f"랭킹 캡처 {ts.strftime('%Y-%m-%d %H:%M')}", persona.RANKING_BOT))
    log.info(persona.step(f"무신사 Top {top_n} (section {section_id}) 가져오는 중..."))

    all_items = fetch_top(n=top_n, section_id=section_id, sub_pan=sub_pan)
    log.info(persona.step(f"전체 {len(all_items)}개 fetch"))

    matched = filter_by_brand(all_items, brand_keywords)
    log.info(persona.step(f"브랜드 매칭: {len(matched)}개 (키워드: {', '.join(brand_keywords)})"))

    rows = []
    hero_hits = []
    for it in matched:
        is_hero = it.goods_no in hero_uids
        if is_hero:
            hero_hits.append(it)
        rows.append((it.goods_no, it.rank, it.brand, it.product_name, is_hero))

    ranking_db.save_snapshot(
        db_path=db_path,
        ts=ts,
        captured_at=captured_at,
        fetched_total=len(all_items),
        items=rows,
    )

    if hero_hits:
        log.info(persona.step(f"히어로 진입: {len(hero_hits)}개"))
        for h in hero_hits[:5]:
            log.info(persona.step(f"  · #{h.rank} {h.product_name[:40]}"))
        if len(hero_hits) > 5:
            log.info(persona.step(f"  · ... 외 {len(hero_hits) - 5}개"))
    else:
        log.info(persona.step("히어로 진입 0개"))

    log.info(persona.task_done_ok(
        f"{ts.strftime('%H:%M')} 스냅샷 저장 — 무탠 {len(matched)}개 / 히어로 {len(hero_hits)}개"
    ))

    return {
        "ts": ts,
        "matched": len(matched),
        "hero_hits": len(hero_hits),
        "fetched": len(all_items),
    }
