"""매시간 정각 트리거 — 무신사 랭킹 Top 300 → 무탠 매칭 → Sheet의 Long 탭에 직접 append."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from soo import persona
from soo.scrapers.musinsa_ranking import fetch_top, filter_by_brand
from soo.storage import sheet_archive


KST = ZoneInfo("Asia/Seoul")


def run(
    sheets_service: Any,
    sheet_id: str,
    brand_keywords: list[str],
    hero_uids: set[str],
    log: logging.Logger,
    top_n: int = 300,
    section_id: int = 199,
    sub_pan: str | None = "product",
) -> dict:
    captured_at = datetime.now(KST)
    # 매시간 1회 — 모든 trigger를 KST :00 슬롯으로 정규화 (분 무관)
    ts = captured_at.replace(minute=0, second=0, microsecond=0)

    log.info(persona.starting_task(f"랭킹 캡처 {ts.strftime('%Y-%m-%d %H:%M KST')}", persona.RANKING_BOT))

    # 멱등성: 같은 시간 슬롯에 이미 적재됐으면 skip (동일 시간 내 중복 trigger 방지)
    if sheet_archive.has_hour_data(sheets_service, sheet_id, ts):
        log.info(persona.step(f"{ts.strftime('%H:%M')} 슬롯 이미 적재됨 — skip"))
        return {"ts": ts, "matched": 0, "hero_hits": 0, "fetched": 0, "appended": 0, "skipped": True}

    log.info(persona.step(f"무신사 Top {top_n} (section {section_id}) 가져오는 중..."))

    all_items = fetch_top(n=top_n, section_id=section_id, sub_pan=sub_pan)
    log.info(persona.step(f"전체 {len(all_items)}개 fetch"))

    matched = filter_by_brand(all_items, brand_keywords)
    log.info(persona.step(f"브랜드 매칭: {len(matched)}개 (키워드: {', '.join(brand_keywords)})"))

    items_for_sheet: list[tuple] = []
    hero_hits = []
    for it in matched:
        is_hero = it.goods_no in hero_uids
        if is_hero:
            hero_hits.append(it)
        items_for_sheet.append((it.goods_no, it.rank, it.brand, it.product_name, is_hero))

    appended = sheet_archive.append_realtime(
        sheets_service=sheets_service,
        sheet_id=sheet_id,
        ts=ts,
        items=items_for_sheet,
        log=log,
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
        f"{ts.strftime('%H:%M')} 캡처 — Sheet에 {appended}행 append (무탠 {len(matched)} / 히어로 {len(hero_hits)})"
    ))

    return {
        "ts": ts,
        "matched": len(matched),
        "hero_hits": len(hero_hits),
        "fetched": len(all_items),
        "appended": appended,
    }
