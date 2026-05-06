"""매시간 정각 트리거 — 무신사 랭킹 Top N → 무탠 매칭 → Sheet의 Long 탭에 직접 append.

추가: rank ≤ screenshot_threshold 진입 + 그날 best 갱신 시 무신사 페이지 스크린샷
→ Drive 업로드 → Screenshots 탭에 URL 기록 → daily 리포트가 image_block으로 첨부.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from soo import persona
from soo.scrapers.musinsa_ranking import fetch_top, filter_by_brand
from soo.storage import sheet_archive


KST = ZoneInfo("Asia/Seoul")


def _maybe_capture_screenshot(
    *,
    sheets_service: Any,
    drive_service: Any,
    sheet_id: str,
    matched: list,
    captured_at: datetime,
    threshold: int,
    folder_id: str,
    section_id: int,
    log: logging.Logger,
) -> int:
    """rank ≤ threshold 진입 + 그날 best 갱신된 항목 있으면 1회 캡처/업로드/upsert.

    같은 hourly 실행에서 여러 상품이 동시 갱신되면 한 번만 캡처해서 모두에게 같은 URL 부여.
    Returns: upsert된 (=새 best 갱신) 항목 수.
    """
    if not folder_id:
        return 0  # 스크린샷 비활성화

    from soo.storage import screenshots_tab, drive_uploader
    from soo.scrapers import musinsa_screenshot

    target_day = captured_at.date()
    candidates = [it for it in matched if it.rank <= threshold]
    if not candidates:
        return 0

    existing = screenshots_tab.read_day_records(sheets_service, sheet_id, target_day)
    needs_update = []
    for it in candidates:
        rec = existing.get(it.goods_no)
        if rec is None or it.rank < rec["peak_rank"]:
            needs_update.append(it)

    if not needs_update:
        log.info(persona.step(
            f"스크린샷 — Top {threshold} 후보 {len(candidates)}개 모두 best 갱신 X (skip)"
        ))
        return 0

    log.info(persona.step(
        f"스크린샷 캡처 시작 — best 갱신 {len(needs_update)}개 "
        f"({', '.join(f'#{it.rank}' for it in needs_update[:5])})"
    ))

    try:
        png = musinsa_screenshot.screenshot_ranking_full_page(section_id=section_id)
    except Exception as e:
        log.error(persona.task_failed(f"스크린샷 캡처 실패: {e}"))
        log.debug(traceback.format_exc())
        return 0

    try:
        day_folder_id = drive_uploader.ensure_subfolder(drive_service, folder_id, target_day.isoformat())
        filename = f"ranking_{captured_at.strftime('%Y%m%d_%H%M%S')}.png"
        url, file_id = drive_uploader.upload_png(drive_service, day_folder_id, filename, png)
    except Exception as e:
        log.error(persona.task_failed(f"Drive 업로드 실패: {e}"))
        log.debug(traceback.format_exc())
        return 0

    log.info(persona.step(f"Drive 업로드 완료 — {filename} ({len(png) // 1024}KB)"))

    for it in needs_update:
        try:
            screenshots_tab.upsert_record(
                sheets_service=sheets_service,
                sheet_id=sheet_id,
                target_day=target_day,
                goods_no=it.goods_no,
                peak_rank=it.rank,
                screenshot_url=url,
                file_id=file_id,
                captured_at=captured_at,
                log=log,
            )
        except Exception as e:
            log.error(persona.task_failed(f"Screenshots upsert 실패 ({it.goods_no}): {e}"))

    return len(needs_update)


def run(
    sheets_service: Any,
    sheet_id: str,
    brand_keywords: list[str],
    hero_uids: set[str],
    log: logging.Logger,
    top_n: int = 100,
    section_id: int = 199,
    sub_pan: str | None = "product",
    drive_service: Any = None,
    screenshot_threshold: int = 10,
    screenshot_folder_id: str = "",
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

    # 스크린샷: Top {threshold} 진입 + 그날 best 갱신 시
    screenshot_updated = 0
    if drive_service and screenshot_folder_id:
        screenshot_updated = _maybe_capture_screenshot(
            sheets_service=sheets_service,
            drive_service=drive_service,
            sheet_id=sheet_id,
            matched=matched,
            captured_at=captured_at,
            threshold=screenshot_threshold,
            folder_id=screenshot_folder_id,
            section_id=section_id,
            log=log,
        )

    log.info(persona.task_done_ok(
        f"{ts.strftime('%H:%M')} 캡처 — Sheet에 {appended}행 (무탠 {len(matched)} / 히어로 {len(hero_hits)} / "
        f"스크린샷 갱신 {screenshot_updated})"
    ))

    return {
        "ts": ts,
        "matched": len(matched),
        "hero_hits": len(hero_hits),
        "fetched": len(all_items),
        "appended": appended,
        "screenshot_updated": screenshot_updated,
    }
