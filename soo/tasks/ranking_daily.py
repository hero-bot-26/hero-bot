"""매일 09:00 — 어제 24h 데이터 (Sheet의 Long 탭에서 read) → 집계 → Slack → Wide 탭 append."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from soo import persona
from soo.storage import sheet_archive


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
    """신규 진입 + 급상승 분리.

    Returns:
      new_entries: 어제 등장, 그제 미등장 → peak_rank 오름차순 정렬
      jumped: [(agg, prev_peak), ...] — 양쪽 다 등장 + peak rank가 JUMP_THRESHOLD 이상 향상
    """
    new_entries = []
    jumped: list[tuple[dict, int]] = []
    for gn, a in aggregated.items():
        if gn not in prev_aggregated:
            new_entries.append(a)
        else:
            prev_peak = prev_aggregated[gn]["peak_rank"]
            jump = prev_peak - a["peak_rank"]  # 양수 = 향상
            if jump >= JUMP_THRESHOLD:
                jumped.append((a, prev_peak))

    new_entries.sort(key=lambda a: a["peak_rank"])
    jumped.sort(key=lambda x: -(x[1] - x[0]["peak_rank"]))  # 큰 폭부터
    return new_entries, jumped


def _title(target_day: date, sheet_url: str | None, top_n: int) -> str:
    """Slack mrkdwn — sheet_url 있으면 제목 텍스트를 링크로."""
    text = f"{target_day.isoformat()} 무탠다드 랭킹 리포트"
    if sheet_url:
        return f"📊 *<{sheet_url}|{text}>* (Top {top_n} 기준)"
    return f"📊 *{text}* (Top {top_n} 기준)"


def build_report(
    rows: list[dict],
    prev_rows: list[dict],
    target_day: date,
    hero_uids: set[str],
    sheet_url: str | None = None,
    top_n: int = 100,
) -> str:
    # 과도기 호환: top_n이 줄어든 직후엔 기존 데이터에 rank > top_n 행이 섞여있음.
    # 리포트는 현재 top_n 기준으로만 보여줘야 일관성 유지.
    rows = [r for r in rows if r.get("rank", 999) <= top_n]
    prev_rows = [r for r in prev_rows if r.get("rank", 999) <= top_n]

    n_snapshots = sheet_archive.count_snapshots(rows)
    if n_snapshots == 0:
        return f"{_title(target_day, sheet_url, top_n)} — 캡처된 스냅샷이 없어요. (봇 미실행 또는 데이터 누락)"

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
    lines.append(_title(target_day, sheet_url, top_n))
    lines.append(f"_캡처 {n_snapshots}/24 회 · 무탠 계열 누적 등장 {len(aggregated)}개 · "
                 f"히어로 {len(hero_aggs)}/{len(hero_uids)} 진입_")
    lines.append("")

    lines.append("🎯 *히어로 (사전 지정 상품)*")
    if hero_aggs:
        for a in hero_aggs[:20]:
            lines.append(f"  • {_hero_summary_line(a)}")
        if len(hero_aggs) > 20:
            lines.append(f"  _… 외 {len(hero_aggs) - 20}개_")
    else:
        lines.append("  _없음_")

    if missing_hero_uids:
        lines.append(f"  ⚠️ 미진입 히어로 {len(missing_hero_uids)}개 (전체 {len(hero_uids)}개 중)")

    lines.append("")
    lines.append(f"📈 *기타 무탠 계열 진입* (히어로 외 — 상위 10)")
    if other_aggs:
        for a in other_aggs[:10]:
            lines.append(f"  • {_hero_summary_line(a)}")
        if len(other_aggs) > 10:
            lines.append(f"  _… 외 {len(other_aggs) - 10}개_")
    else:
        lines.append("  _없음_")

    # 신규 진입 + 급상승
    lines.append("")
    lines.append(f"🚀 *전일 대비 급상승 / 신규 진입* (peak rank {JUMP_THRESHOLD}위 이상 향상)")
    if not prev_rows:
        lines.append("  _전일 데이터 부족 — 비교 불가_")
    elif not new_entries and not jumped:
        lines.append("  _급상승/신규 진입 없음_")
    else:
        for a in new_entries[:10]:
            lines.append(f"  • 🆕 최고 랭킹 #{a['peak_rank']:>3}  {a['product_name'][:42]:<42}  (신규 · {_format_time(a['peak_ts'])} 피크)")
        if len(new_entries) > 10:
            lines.append(f"  _… 신규 외 {len(new_entries) - 10}개_")
        for a, prev_peak in jumped[:10]:
            jump_amt = prev_peak - a["peak_rank"]
            lines.append(f"  • 📈 최고 랭킹 #{a['peak_rank']:>3}  {a['product_name'][:42]:<42}  (전일 #{prev_peak} → +{jump_amt}↑)")
        if len(jumped) > 10:
            lines.append(f"  _… 급상승 외 {len(jumped) - 10}개_")

    return "\n".join(lines)


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
) -> dict:
    if target_day is None:
        target_day = date.today() - timedelta(days=1)

    log.info(persona.starting_task(f"{target_day.isoformat()} 랭킹 일일 리포트", persona.RANKING_BOT))

    # 1) Sheet에서 어제 + 그제 데이터 read (그제는 신규/급상승 비교용)
    rows = sheet_archive.read_day_long(sheets_service, sheet_id, target_day)
    log.info(persona.step(f"Long 탭에서 read (어제) — {len(rows)}행, "
                          f"{sheet_archive.count_snapshots(rows)}개 시각"))
    prev_day = target_day - timedelta(days=1)
    prev_rows = sheet_archive.read_day_long(sheets_service, sheet_id, prev_day)
    log.info(persona.step(f"Long 탭에서 read (그제 {prev_day.isoformat()}) — {len(prev_rows)}행"))

    # 2) 리포트 생성 + Slack 발송
    report = build_report(rows, prev_rows, target_day, hero_uids, sheet_url=sheet_url, top_n=top_n)
    log.info(persona.step(f"리포트 생성 — {len(report)}자"))
    for line in report.split("\n"):
        log.info(line)

    sent = False
    if slack_bot_token and slack_target:
        sent = persona.send_slack(
            report,
            bot_token=slack_bot_token,
            target=slack_target,
            persona=persona.RANKING_BOT,
            log=log,
        )
        log.info(persona.step(f"Slack 발송 — {'성공' if sent else '실패'}"))

    # 3) Wide 탭에 일일 요약 append
    wide_appended = sheet_archive.append_day_wide(
        sheets_service=sheets_service,
        sheet_id=sheet_id,
        target_day=target_day,
        rows=rows,
        log=log,
    )

    log.info(persona.task_done_ok(f"{target_day.isoformat()} 리포트 + Wide 정리 완료"))
    return {
        "target_day": target_day,
        "rows_read": len(rows),
        "report_len": len(report),
        "slack_sent": sent,
        "wide_appended": wide_appended,
    }
