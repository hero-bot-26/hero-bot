"""랭킹 1위 즉시 알림 — 시간 무관, 무탠 계열이 1위에 오르면 그 순간 슬랙 알림.

기존 hourly(적재) / daily(리포트)와 별개로 도는 가벼운 감시 루프.
- 무신사 랭킹 API에서 뷰별 상위 몇 개만 조회 (Top 100 전체 fetch 안 함)
- rank == 1 이 무탠 계열이면 → 랭킹 페이지 스냅샷 캡처 → 채널에 @channel + 스냅샷 발송
- 중복 방지: Rank1Alerts 탭 (날짜, 뷰, goods_no) — 하루 1회
- 같은 트리거에서 여러 뷰가 동시에 1위면 메시지는 1개로 묶고 뷰 라벨을 나열

Sheet의 Long/Wide/Screenshots 탭은 건드리지 않는다 (hourly/daily 멱등성 무간섭).
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from soo import persona, VIEWS
from soo.scrapers.musinsa_ranking import fetch_top, filter_by_brand


KST = ZoneInfo("Asia/Seoul")

# 1위만 보면 되지만, 광고 슬롯/응답 흔들림 대비로 상위 몇 개를 받아 rank==1을 고른다.
PROBE_TOP_N = 5

_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
_VIEW_ORDER = {label: i for i, (_gf, label) in enumerate(VIEWS)}


def _kst_time_label(dt: datetime) -> str:
    """'오후 2시 20분' — 슬랙에서 사람이 읽는 시각 표기."""
    ampm = "오전" if dt.hour < 12 else "오후"
    h12 = dt.hour % 12 or 12
    return f"{ampm} {h12}시 {dt.minute:02d}분"


def _kst_date_label(dt: datetime) -> str:
    return f"{dt.month}월 {dt.day}일({_WEEKDAY_KO[dt.weekday()]})"


def probe(
    brand_keywords: list[str],
    section_id: int = 199,
    sub_pan: str | None = "product",
    log: logging.Logger | None = None,
) -> list[dict]:
    """뷰별 1위를 조회해서 무탠 계열인 것만 반환.

    반환: [{"gf", "view", "item"(RankItem)}, ...]
    Google 인증 없이 도는 가벼운 경로 — 워크플로의 사전 체크(probe) 스텝에서도 쓴다.
    """
    hits: list[dict] = []
    for gf, view in VIEWS:
        try:
            items = fetch_top(n=PROBE_TOP_N, section_id=section_id, sub_pan=sub_pan, gf=gf)
        except Exception as e:
            if log:
                log.error(persona.task_failed(f"[{view}] 랭킹 조회 실패: {e}"))
            continue

        top1 = next((it for it in items if it.rank == 1), None)
        if top1 is None:
            if log:
                log.info(persona.step(f"[{view}] 1위 항목을 못 찾음 (응답 이상) — skip"))
            continue

        is_musinsa_standard = bool(filter_by_brand([top1], brand_keywords))
        if log:
            mark = "🥇 무탠!" if is_musinsa_standard else "—"
            log.info(persona.step(
                f"[{view}] 현재 1위: {top1.brand} / {top1.product_name[:36]} {mark}"
            ))
        if is_musinsa_standard:
            hits.append({"gf": gf, "view": view, "item": top1})
    return hits


def build_message(
    *,
    views: list[str],
    item: Any,
    is_hero: bool,
    hero_line: str,
    detected_at: datetime,
    poll_minutes: int,
    mention_channel: bool,
) -> str:
    view_label = "·".join(sorted(views, key=lambda v: _VIEW_ORDER.get(v, 99)))
    # 슬랙에서 @channel 로 렌더되는 특수 멘션 토큰. 문자열 "@channel"은 알림이 안 감.
    head = "<!channel> " if mention_channel else ""

    lines = []
    lines.append(
        f"{head}🥇 *[{view_label}] 무신사 랭킹 1위 등극!*"
        + (f"   🎯 _히어로 · {hero_line}_" if is_hero else "")
    )
    lines.append("")
    name = item.product_name or item.goods_no
    lines.append(f"*<{item.url}|{name[:70]}>*")
    lines.append("")
    lines.append(
        f"• 1위 확인 — *{_kst_time_label(detected_at)}* "
        f"({_kst_date_label(detected_at)} KST)"
    )
    lines.append(
        f"• _{poll_minutes}분 주기로 감시 중이라, 실제 1위 진입은 이보다 "
        f"최대 {poll_minutes}분 이른 시점일 수 있어요._"
    )
    return "\n".join(lines)


def _capture_and_archive(
    *,
    drive_service: Any,
    folder_id: str,
    section_id: int,
    gf: str,
    view: str,
    crop_to_rank: int | None,
    detected_at: datetime,
    log: logging.Logger,
) -> tuple[bytes | None, str, str]:
    """랭킹 페이지 스냅샷 캡처 → (옵션) Drive 아카이브. (png, url, file_id) 반환.

    캡처/업로드 실패는 알림 자체를 막지 않는다 (텍스트만이라도 나가는 게 낫다).
    """
    from soo.scrapers import musinsa_screenshot

    try:
        png = musinsa_screenshot.screenshot_ranking_full_page(
            section_id=section_id, crop_to_rank=crop_to_rank, gf=gf,
        )
        log.info(persona.step(f"스냅샷 캡처 완료 [{view}] — {len(png) // 1024}KB"))
    except Exception as e:
        log.error(persona.task_failed(f"스냅샷 캡처 실패 [{view}]: {e}"))
        log.debug(traceback.format_exc())
        return None, "", ""

    if not (drive_service and folder_id):
        return png, "", ""

    try:
        from soo.storage import drive_uploader
        day_folder_id = drive_uploader.ensure_subfolder(
            drive_service, folder_id, detected_at.date().isoformat()
        )
        view_folder_id = drive_uploader.ensure_subfolder(drive_service, day_folder_id, view)
        filename = f"rank1_{view}_{detected_at.strftime('%Y%m%d_%H%M%S')}.png"
        url, file_id = drive_uploader.upload_png(drive_service, view_folder_id, filename, png)
        log.info(persona.step(f"Drive 아카이브 [{view}] — {filename}"))
        return png, url, file_id
    except Exception as e:
        log.error(persona.task_failed(f"Drive 업로드 실패(무시) [{view}]: {e}"))
        return png, "", ""


def _upload_png_to_slack(
    *,
    png: bytes,
    filename: str,
    title: str,
    slack_bot_token: str,
    slack_target: str,
    log: logging.Logger,
) -> bool:
    """스냅샷 PNG를 채널 본문(스레드 아님)에 직접 업로드.

    Drive anyone-with-link가 막혀 있어 image_block(URL) 미리보기가 불가 →
    daily와 같은 files_upload_v2 경로. 1위 알림은 사진이 핵심이라 스레드가 아니라
    채널 타임라인에 바로 올린다.
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        log.error(persona.task_failed("slack_sdk 미설치 — 스냅샷 업로드 불가"))
        return False

    client = WebClient(token=slack_bot_token)
    upload_channel = slack_target
    if slack_target and slack_target.startswith("U"):
        try:
            im = client.conversations_open(users=slack_target)
            upload_channel = im["channel"]["id"]
        except SlackApiError as e:
            log.error(persona.task_failed(
                f"DM 채널 열기 실패 — {e.response.get('error') if e.response else e}"
            ))
            return False

    try:
        # initial_comment 없음 — 바로 위 알림 메시지가 이미 설명이라 캡션은 중복.
        client.files_upload_v2(
            channel=upload_channel,
            file=png,
            filename=filename,
            title=title,
        )
        return True
    except SlackApiError as e:
        err = e.response.get("error") if e.response else str(e)
        log.error(persona.task_failed(f"스냅샷 슬랙 업로드 실패 — {err}"))
        return False
    except Exception as e:
        log.error(persona.task_failed(f"스냅샷 슬랙 업로드 예외 — {type(e).__name__}: {e}"))
        return False


def run(
    sheets_service: Any,
    sheet_id: str,
    brand_keywords: list[str],
    heroes: dict,
    slack_bot_token: str | None,
    slack_target: str | None,
    log: logging.Logger,
    section_id: int = 199,
    sub_pan: str | None = "product",
    drive_service: Any = None,
    screenshot_folder_id: str = "",
    crop_to_rank: int | None = 6,
    poll_minutes: int = 10,
    mention_policy: str = "hero",
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    detected_at = datetime.now(KST)
    target_day = detected_at.date()

    log.info(persona.starting_task(
        f"랭킹 1위 감시 {detected_at.strftime('%Y-%m-%d %H:%M KST')}", persona.RANKING_BOT
    ))

    hits = probe(brand_keywords, section_id=section_id, sub_pan=sub_pan, log=log)
    if not hits:
        log.info(persona.task_done_ok("무탠 계열 1위 없음 — 알림 없음"))
        return {"hits": 0, "alerts": 0, "detected_at": detected_at}

    # 이미 오늘 알린 (뷰, goods_no) 제외
    if force or dry_run:
        already: dict = {}
        if force:
            log.info(persona.step("--force — 중복 체크 생략"))
    else:
        from soo.storage import rank1_log
        already = rank1_log.read_day_alerts(sheets_service, sheet_id, target_day, log=log)

    fresh = [h for h in hits if (h["view"], h["item"].goods_no) not in already]
    if not fresh:
        log.info(persona.task_done_ok(
            f"1위 {len(hits)}건 모두 오늘 이미 알림 발송됨 — skip"
        ))
        return {"hits": len(hits), "alerts": 0, "detected_at": detected_at}

    # 같은 상품이 여러 뷰에서 동시 1위 → 메시지 1개로 묶기
    grouped: dict[str, dict] = {}
    for h in fresh:
        gn = h["item"].goods_no
        g = grouped.setdefault(gn, {"item": h["item"], "views": [], "gfs": {}})
        g["views"].append(h["view"])
        g["gfs"][h["view"]] = h["gf"]

    alerts_sent = 0
    for gn, g in grouped.items():
        item = g["item"]
        views = sorted(g["views"], key=lambda v: _VIEW_ORDER.get(v, 99))
        primary_view = views[0]
        primary_gf = g["gfs"][primary_view]
        hero_entry = heroes.get(gn)
        is_hero = hero_entry is not None
        hero_line = getattr(hero_entry, "line", "") if hero_entry else ""

        # 멘션은 정책에 따라 — 히어로 외 건(양말 7팩 등)까지 @channel 하면 이틀에 한 번 전원 호출.
        mention = mention_policy == "always" or (mention_policy == "hero" and is_hero)
        message = build_message(
            views=views,
            item=item,
            is_hero=is_hero,
            hero_line=hero_line,
            detected_at=detected_at,
            poll_minutes=poll_minutes,
            mention_channel=mention,
        )
        log.info(persona.step(f"알림 대상 — #{item.rank} {item.product_name[:40]} [{'·'.join(views)}]"))
        for line in message.split("\n"):
            log.info(line)

        if dry_run:
            alerts_sent += 1
            continue

        # ── 중복 방지 우선: 발송 *전에* 원장부터 쓴다 (ranking_daily의 Wide-우선과 같은 이유).
        #    쓰기가 실패하면 예외로 이 상품은 건너뛰고, 다음 트리거(10분 뒤)가 1회만 재시도.
        from soo.storage import rank1_log
        rank1_log.append_alert(
            sheets_service=sheets_service,
            sheet_id=sheet_id,
            target_day=target_day,
            views=views,
            goods_no=gn,
            brand=item.brand,
            product_name=item.product_name,
            is_hero=is_hero,
            detected_at=detected_at,
            log=log,
        )

        png, drive_url, file_id = _capture_and_archive(
            drive_service=drive_service,
            folder_id=screenshot_folder_id,
            section_id=section_id,
            gf=primary_gf,
            view=primary_view,
            crop_to_rank=crop_to_rank,
            detected_at=detected_at,
            log=log,
        )

        slack_ts = ""
        if slack_bot_token and slack_target:
            slack_ts = persona.send_slack(
                message,
                bot_token=slack_bot_token,
                target=slack_target,
                persona=persona.RANKING_BOT,
                log=log,
            ) or ""
            log.info(persona.step(f"Slack 발송 — {'성공' if slack_ts else '실패'}"))

            if not slack_ts:
                # 발송 실패 — dedup 마커를 되돌려 다음 트리거(10분 뒤)가 재시도하게 한다.
                rank1_log.delete_alert(
                    sheets_service=sheets_service, sheet_id=sheet_id,
                    target_day=target_day, views=views, goods_no=gn, log=log,
                )
                continue

            if png:
                ok = _upload_png_to_slack(
                    png=png,
                    filename=f"rank1_{primary_view}_{detected_at.strftime('%Y%m%d_%H%M')}_{gn}.png",
                    title=f"[{primary_view}] 랭킹 1위 — {item.product_name[:60]}",
                    slack_bot_token=slack_bot_token,
                    slack_target=slack_target,
                    log=log,
                )
                log.info(persona.step(f"스냅샷 업로드 — {'성공' if ok else '실패'}"))
        else:
            log.warning(persona.step("Slack token/target 없음 — 발송 생략 (원장만 기록)"))

        rank1_log.update_alert_meta(
            sheets_service=sheets_service,
            sheet_id=sheet_id,
            target_day=target_day,
            views=views,
            goods_no=gn,
            slack_ts=slack_ts,
            screenshot_url=drive_url,
            file_id=file_id,
            log=log,
        )
        alerts_sent += 1

    log.info(persona.task_done_ok(f"1위 감지 {len(hits)}건 · 신규 알림 {alerts_sent}건"))
    return {"hits": len(hits), "alerts": alerts_sent, "detected_at": detected_at}
