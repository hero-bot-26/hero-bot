"""매일 09:00 — 어제 24h 데이터 (Sheet의 Long 탭에서 read) → 뷰별 집계 → Slack → Wide 탭 append.

3개 뷰(전체/남자/여자) 각각 별도 Slack 메시지로 발송.
각 뷰의 Top 10 진입 스크린샷도 해당 뷰 리포트 뒤에 image_block으로 첨부.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from soo import persona, VIEWS
from soo.storage import drive_uploader, sheet_archive, screenshots_tab


def _aggregate(rows: list[dict]) -> dict[str, dict]:
    by_goods: dict[str, dict] = defaultdict(lambda: {
        "ranks": [],
        "brand": "",
        "product_name": "",
        "is_hero": False,
    })
    for r in rows:
        g = by_goods[r["goods_no"]]
        g["ranks"].append((r["ts"], r["rank"]))
        g["brand"] = r["brand"]
        g["product_name"] = r["product_name"]
        g["is_hero"] = g["is_hero"] or r["is_hero"]

    result = {}
    for gn, g in by_goods.items():
        ranks_only = [rk for _, rk in g["ranks"]]
        peak_idx = ranks_only.index(min(ranks_only))
        peak_ts, peak_rank = g["ranks"][peak_idx]
        result[gn] = {
            "brand": g["brand"],
            "product_name": g["product_name"],
            "is_hero": g["is_hero"],
            "hours_in_chart": len(g["ranks"]),
            "peak_rank": peak_rank,
            "peak_ts": peak_ts,
            "ranks": g["ranks"],
        }
    return result


def _format_time(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.minute == 0:
            return f"{dt.hour}시"
        return f"{dt.hour}:{dt.minute:02d}"
    except Exception:
        return iso_ts


def _hero_summary_line(agg: dict) -> str:
    return (
        f"최고 랭킹 #{agg['peak_rank']:>3}  "
        f"{agg['product_name'][:42]:<42}  "
        f"({_format_time(agg['peak_ts'])} 피크)"
    )


JUMP_THRESHOLD = 50  # 전일 대비 peak rank 향상 폭이 이 이상이면 "급상승"


def _new_and_jumped(
    aggregated: dict[str, dict],
    prev_aggregated: dict[str, dict],
) -> tuple[list[dict], list[tuple[dict, int]]]:
    """신규 진입 + 급상승 분리."""
    new_entries = []
    jumped: list[tuple[dict, int]] = []
    for gn, a in aggregated.items():
        if gn not in prev_aggregated:
            new_entries.append(a)
        else:
            prev_peak = prev_aggregated[gn]["peak_rank"]
            jump = prev_peak - a["peak_rank"]
            if jump >= JUMP_THRESHOLD:
                jumped.append((a, prev_peak))

    new_entries.sort(key=lambda a: a["peak_rank"])
    jumped.sort(key=lambda x: -(x[1] - x[0]["peak_rank"]))
    return new_entries, jumped


def _title(target_day: date, view: str, sheet_url: str | None, top_n: int) -> str:
    """Slack mrkdwn — sheet_url 있으면 제목 텍스트를 링크로. view 라벨 prefix."""
    text = f"[{view}] {target_day.isoformat()} 무탠다드 랭킹 리포트"
    if sheet_url:
        return f"📊 *<{sheet_url}|{text}>* (Top {top_n} 기준)"
    return f"📊 *{text}* (Top {top_n} 기준)"


def build_report(
    rows: list[dict],
    prev_rows: list[dict],
    target_day: date,
    view: str,
    hero_uids: set[str],
    sheet_url: str | None = None,
    top_n: int = 100,
    screenshots_count: int = 0,
) -> str:
    rows = [r for r in rows if r.get("rank", 999) <= top_n]
    prev_rows = [r for r in prev_rows if r.get("rank", 999) <= top_n]

    n_snapshots = sheet_archive.count_snapshots(rows)
    if n_snapshots == 0:
        msg = (
            f"{_title(target_day, view, sheet_url, top_n)} — "
            f"Long 탭에 [{view}] 시간별 적재 행이 0건이에요. "
            f"(hourly 봇이 [{view}] 뷰를 못 적재 — 워크플로우/스키마 마이그 누락 의심)"
        )
        if screenshots_count > 0:
            msg += f"\n_단, Drive 스크린샷은 [{view}] 뷰로 {screenshots_count}장 캡처돼 있음._"
        return msg

    aggregated = _aggregate(rows)
    prev_aggregated = _aggregate(prev_rows) if prev_rows else {}
    new_entries, jumped = _new_and_jumped(aggregated, prev_aggregated)

    hero_aggs = sorted(
        [a for a in aggregated.values() if a["is_hero"]],
        key=lambda a: (a["peak_rank"], -a["hours_in_chart"]),
    )
    other_aggs = sorted(
        [a for a in aggregated.values() if not a["is_hero"]],
        key=lambda a: (a["peak_rank"], -a["hours_in_chart"]),
    )

    in_chart_hero_uids = {gn for gn, a in aggregated.items() if a["is_hero"]}
    missing_hero_uids = hero_uids - in_chart_hero_uids

    lines = []
    lines.append(_title(target_day, view, sheet_url, top_n))
    lines.append(f"_캡처 {n_snapshots}/24 회 · 무탠 계열 누적 등장 {len(aggregated)}개 · "
                 f"히어로 {len(hero_aggs)}/{len(hero_uids)} 진입_")
    lines.append("")

    lines.append("🎯 *히어로 (사전 지정 상품)*")
    if hero_aggs:
        for a in hero_aggs[:5]:
            lines.append(f"  • {_hero_summary_line(a)}")
        if len(hero_aggs) > 5:
            lines.append(f"  _… 외 {len(hero_aggs) - 5}개 (전체 목록은 Wide 탭)_")
    else:
        lines.append("  _없음_")

    if missing_hero_uids:
        lines.append(f"  ⚠️ 미진입 히어로 {len(missing_hero_uids)}개 (전체 {len(hero_uids)}개 중)")

    lines.append("")
    lines.append(f"📈 *기타 무탠 계열 진입* (히어로 외 — 상위 5)")
    if other_aggs:
        for a in other_aggs[:5]:
            lines.append(f"  • {_hero_summary_line(a)}")
        if len(other_aggs) > 5:
            lines.append(f"  _… 외 {len(other_aggs) - 5}개 (전체 목록은 Wide 탭)_")
    else:
        lines.append("  _없음_")

    lines.append("")
    lines.append(f"🚀 *전일 대비 급상승 / 신규 진입* (peak rank {JUMP_THRESHOLD}위 이상 향상 · 합쳐 상위 5)")
    if not prev_rows:
        lines.append("  _전일 데이터 부족 — 비교 불가_")
    elif not new_entries and not jumped:
        lines.append("  _급상승/신규 진입 없음_")
    else:
        shown = 0
        for a in new_entries[:5]:
            lines.append(f"  • 🆕 최고 랭킹 #{a['peak_rank']:>3}  {a['product_name'][:42]:<42}  (신규 · {_format_time(a['peak_ts'])} 피크)")
            shown += 1
        for a, prev_peak in jumped[: max(0, 5 - shown)]:
            jump_amt = prev_peak - a["peak_rank"]
            lines.append(f"  • 📈 최고 랭킹 #{a['peak_rank']:>3}  {a['product_name'][:42]:<42}  (전일 #{prev_peak} → +{jump_amt}↑)")
            shown += 1
        remaining = (len(new_entries) + len(jumped)) - shown
        if remaining > 0:
            lines.append(f"  _… 외 {remaining}개 (전체 목록은 Wide 탭)_")

    return "\n".join(lines)


def _send_screenshots(
    *,
    sheets_service: Any,
    drive_service: Any,
    sheet_id: str,
    target_day: date,
    view: str,
    rows: list[dict],
    slack_bot_token: str,
    slack_target: str,
    thread_ts: str | None,
    log: logging.Logger,
) -> int:
    """(뷰별) 어제 스크린샷 PNG들을 Drive에서 다운받아 슬랙에 직접 업로드.

    무신사 Workspace가 Drive anyone-with-link 외부 공개를 차단하므로 image_block(URL) 방식
    대신 files_upload_v2로 채널에 PNG 파일을 직접 올림. 슬랙이 자체 호스팅하여 미리보기 정상.

    thread_ts: 해당 view 리포트 메시지의 ts. 헤더와 모든 PNG를 그 thread reply로 묶음 —
               files_upload_v2가 슬랙 백엔드에서 한 박자 늦게 게시되면서
               다음 view 리포트 뒤로 밀려 뒤섞이는 문제 방지.
    """
    records = screenshots_tab.read_day_records(sheets_service, sheet_id, target_day, view=view)
    if not records:
        return 0

    name_lookup: dict[str, str] = {}
    for r in rows:
        gn = r["goods_no"]
        if gn not in name_lookup:
            name_lookup[gn] = r.get("product_name", "")

    items_with_file = [(gn, rec) for gn, rec in records.items() if rec.get("file_id")]
    if not items_with_file:
        return 0
    items_with_file.sort(key=lambda kv: kv[1]["peak_rank"])

    # 헤더 메시지 1개 (view 리포트 thread 안에)
    persona.send_slack(
        f"📸 *[{view}] Top 10 진입 스크린샷* — {len(items_with_file)}건",
        bot_token=slack_bot_token,
        target=slack_target,
        persona=persona.RANKING_BOT,
        thread_ts=thread_ts,
        log=log,
    )

    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        log.error(persona.task_failed("slack_sdk 미설치 — files_upload_v2 발송 불가"))
        return 0

    client = WebClient(token=slack_bot_token)

    # 채널 ID로 정규화 — files_upload_v2는 D/C/G/Z 시작 ID 필요. 사용자 ID(U…)면 IM 열어 변환.
    upload_channel = slack_target
    if slack_target and slack_target.startswith("U"):
        try:
            im = client.conversations_open(users=slack_target)
            upload_channel = im["channel"]["id"]
        except SlackApiError as e:
            log.error(persona.task_failed(f"DM 채널 열기 실패 — {e.response.get('error') if e.response else e}"))
            return 0

    sent_count = 0
    for gn, rec in items_with_file:
        name = name_lookup.get(gn, gn)
        caption = f"*#{rec['peak_rank']:>2}*  {name[:60]}"
        try:
            png = drive_uploader.download_png(drive_service, rec["file_id"])
        except Exception as e:
            log.error(persona.task_failed(f"Drive 다운로드 실패 [{view}] ({gn}): {e}"))
            continue
        try:
            upload_kwargs: dict = dict(
                channel=upload_channel,
                file=png,
                filename=f"ranking_{view}_{target_day.isoformat()}_rank{rec['peak_rank']:02d}_{gn}.png",
                title=f"[{view}] #{rec['peak_rank']} {name[:60]}",
                initial_comment=caption,
            )
            if thread_ts:
                upload_kwargs["thread_ts"] = thread_ts
            client.files_upload_v2(**upload_kwargs)
            sent_count += 1
        except SlackApiError as e:
            err = e.response.get("error") if e.response else str(e)
            log.error(persona.task_failed(f"Slack 업로드 실패 [{view}] ({gn}): {err}"))
        except Exception as e:
            log.error(persona.task_failed(f"Slack 업로드 예외 [{view}] ({gn}): {type(e).__name__}: {e}"))
    return sent_count


def _run_view(
    *,
    sheets_service: Any,
    drive_service: Any,
    sheet_id: str,
    view: str,
    target_day: date,
    prev_day: date,
    hero_uids: set[str],
    slack_bot_token: str | None,
    slack_target: str | None,
    log: logging.Logger,
    sheet_url: str | None,
    top_n: int,
    force: bool,
) -> dict:
    # 멱등성: 이미 (date, view) wide-append 된 경우 force 아니면 skip
    already_wide = sheet_archive.has_day_wide(sheets_service, sheet_id, target_day, view=view)
    if not force and already_wide:
        log.info(persona.step(f"[{view}] Wide 탭에 이미 적재됨 — skip (재발송: --force)"))
        return {"view": view, "skipped": True, "rows_read": 0, "slack_sent": False, "wide_appended": 0}

    rows = sheet_archive.read_day_long(sheets_service, sheet_id, target_day, view=view)
    log.info(persona.step(
        f"[{view}] Long 탭 read (어제) — {len(rows)}행, {sheet_archive.count_snapshots(rows)}개 시각"
    ))
    prev_rows = sheet_archive.read_day_long(sheets_service, sheet_id, prev_day, view=view)
    log.info(persona.step(f"[{view}] Long 탭 read (그제 {prev_day.isoformat()}) — {len(prev_rows)}행"))

    # Long 0건 케이스에서 "그래도 Drive PNG는 있다"를 보여주기 위해 미리 카운트
    screenshots_records = screenshots_tab.read_day_records(sheets_service, sheet_id, target_day, view=view)
    screenshots_count = len(screenshots_records)

    report = build_report(
        rows, prev_rows, target_day, view, hero_uids,
        sheet_url=sheet_url, top_n=top_n, screenshots_count=screenshots_count,
    )
    log.info(persona.step(f"[{view}] 리포트 생성 — {len(report)}자"))
    for line in report.split("\n"):
        log.info(line)

    report_ts: str | None = None
    if slack_bot_token and slack_target:
        report_ts = persona.send_slack(
            report,
            bot_token=slack_bot_token,
            target=slack_target,
            persona=persona.RANKING_BOT,
            log=log,
        )
        log.info(persona.step(f"[{view}] Slack 발송 — {'성공' if report_ts else '실패'}"))

    # Wide 적재는 스크린샷 업로드 전에 — 스크린샷이 10분 cap 안에서 못 끝나
    # GH Actions 가 SIGKILL 해도 다음 cron 이 has_day_wide() 로 이 뷰를 skip 하고
    # 다음 뷰로 넘어가게 한다 (스크린샷 일부 누락은 감수, Slack 중복 발송 방지가 우선).
    # force=True 재실행 시 이미 wide 행이 있으면 중복 적재 방지하고 슬랙만 다시 보낸다.
    if already_wide:
        log.info(persona.step(f"[{view}] Wide 탭 이미 적재됨 — append skip (slack만 재발송)"))
        wide_appended = 0
    else:
        wide_appended = sheet_archive.append_day_wide(
            sheets_service=sheets_service,
            sheet_id=sheet_id,
            target_day=target_day,
            view=view,
            rows=rows,
            log=log,
        )

    if report_ts:
        screenshots_sent = _send_screenshots(
            sheets_service=sheets_service,
            drive_service=drive_service,
            sheet_id=sheet_id,
            target_day=target_day,
            view=view,
            rows=rows,
            slack_bot_token=slack_bot_token,
            slack_target=slack_target,
            thread_ts=report_ts,
            log=log,
        )
        log.info(persona.step(f"[{view}] 스크린샷 슬랙 업로드 — {screenshots_sent}건"))

    return {
        "view": view,
        "skipped": False,
        "rows_read": len(rows),
        "report_len": len(report),
        "slack_sent": bool(report_ts),
        "wide_appended": wide_appended,
    }


def run(
    sheets_service: Any,
    sheet_id: str,
    hero_uids: set[str],
    slack_bot_token: str | None,
    slack_target: str | None,
    log: logging.Logger,
    target_day: date | None = None,
    sheet_url: str | None = None,
    top_n: int = 100,
    force: bool = False,
    drive_service: Any = None,
) -> dict:
    if target_day is None:
        target_day = date.today() - timedelta(days=1)
    prev_day = target_day - timedelta(days=1)

    log.info(persona.starting_task(f"{target_day.isoformat()} 랭킹 일일 리포트 (3뷰)", persona.RANKING_BOT))

    # 모든 뷰가 이미 Wide에 적재됐고 force=False면 안내 메시지 발송 전에 일찍 return —
    # GH Actions cron 안전망(09:00/09:15/09:30/10:00)이 4번 발화할 때 첫 번째 외 3번은
    # 뷰 처리는 _run_view 내부 has_day_wide()로 skip되지만 안내 메시지만 외롭게 발송돼
    # 노이즈가 되던 버그 (2026-05-18 21:52 사고).
    if not force:
        all_done = all(
            sheet_archive.has_day_wide(sheets_service, sheet_id, target_day, view=view)
            for _gf, view in VIEWS
        )
        if all_done:
            log.info(persona.step("모든 뷰가 이미 적재됨 — 안내/리포트 전체 skip (재발송: --force)"))
            return {
                "target_day": target_day,
                "per_view": [
                    {"view": v, "skipped": True, "rows_read": 0,
                     "slack_sent": False, "wide_appended": 0}
                    for _gf, v in VIEWS
                ],
            }

    # 가독성 안내 — 각 view 리포트의 스크린샷은 해당 메시지의 thread(댓글)에 들어간다.
    # 매일 발송되지만 한 줄짜리라 노이즈 부담은 작음.
    if slack_bot_token and slack_target:
        persona.send_slack(
            "📎 _가독성을 높이기 위해, 각 top10 랭킹 사진은 각 리포트의 댓글(thread)에서 확인 가능합니다._",
            bot_token=slack_bot_token,
            target=slack_target,
            persona=persona.RANKING_BOT,
            log=log,
        )

    per_view: list[dict] = []
    for _gf, view in VIEWS:
        try:
            result = _run_view(
                sheets_service=sheets_service,
                drive_service=drive_service,
                sheet_id=sheet_id,
                view=view,
                target_day=target_day,
                prev_day=prev_day,
                hero_uids=hero_uids,
                slack_bot_token=slack_bot_token,
                slack_target=slack_target,
                log=log,
                sheet_url=sheet_url,
                top_n=top_n,
                force=force,
            )
        except Exception as e:
            import traceback as _tb
            log.error(persona.task_failed(f"[{view}] 처리 중 오류: {e}"))
            log.debug(_tb.format_exc())
            result = {"view": view, "skipped": False, "error": str(e),
                      "rows_read": 0, "slack_sent": False, "wide_appended": 0}
        per_view.append(result)

    log.info(persona.task_done_ok(f"{target_day.isoformat()} 3뷰 리포트 + Wide 정리 완료"))
    return {
        "target_day": target_day,
        "per_view": per_view,
    }
