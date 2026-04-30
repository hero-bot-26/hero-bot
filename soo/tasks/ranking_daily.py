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
        return f"{dt.hour}시"
    except Exception:
        return iso_ts


def _hero_summary_line(agg: dict) -> str:
    return (
        f"#{agg['peak_rank']:>3}  "
        f"{agg['product_name'][:38]:<38}  "
        f"{agg['hours_in_chart']:>2}시간 등장  "
        f"({_format_time(agg['peak_ts'])} 피크)"
    )


def build_report(rows: list[dict], target_day: date, hero_uids: set[str]) -> str:
    n_snapshots = sheet_archive.count_snapshots(rows)
    if n_snapshots == 0:
        return f"📊 *{target_day.isoformat()} 랭킹 리포트* — 캡처된 스냅샷이 없어요. (봇 미실행 또는 데이터 누락)"

    aggregated = _aggregate(rows)

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
    lines.append(f"📊 *{target_day.isoformat()} 무탠다드 랭킹 리포트* (Top 300 기준)")
    lines.append(f"_캡처 {n_snapshots}/24 스냅샷 · 무탠 계열 누적 등장 {len(aggregated)}개 · "
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

    return "\n".join(lines)


def run(
    sheets_service: Any,
    sheet_id: str,
    hero_uids: set[str],
    slack_bot_token: str | None,
    slack_target: str | None,
    log: logging.Logger,
    target_day: date | None = None,
) -> dict:
    if target_day is None:
        target_day = date.today() - timedelta(days=1)

    log.info(persona.starting_task(f"{target_day.isoformat()} 랭킹 일일 리포트", persona.RANKING_BOT))

    # 1) Sheet에서 어제 데이터 read
    rows = sheet_archive.read_day_long(sheets_service, sheet_id, target_day)
    log.info(persona.step(f"Long 탭에서 read — {len(rows)}행, "
                          f"{sheet_archive.count_snapshots(rows)}개 시각"))

    # 2) 리포트 생성 + Slack 발송
    report = build_report(rows, target_day, hero_uids)
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
