"""매일 09:00 트리거 — 전날 24시간 랭킹 데이터 집계 → Slack 리포트."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from soo import persona
from soo.storage import ranking_db, sheet_archive


def _aggregate(rows: list[dict]) -> dict[str, dict]:
    """상품별 집계 — 등장 시각, peak rank, peak time, hours in chart."""
    by_goods: dict[str, dict] = defaultdict(lambda: {
        "ranks": [],         # [(ts, rank), ...]
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
    """'2026-04-30T14:00:00' → '14시'."""
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


def build_report(
    db_path: Path,
    target_day: date,
    hero_uids: set[str],
    yesterday_rankings_for_compare: list[dict] | None = None,
) -> str:
    """Slack에 보낼 리포트 텍스트 생성."""
    snapshots = ranking_db.snapshots_for_day(db_path, target_day)
    rows = ranking_db.rankings_for_day(db_path, target_day)
    actions = ranking_db.actions_for_day(db_path, target_day)

    n_snapshots = len(snapshots)
    if n_snapshots == 0:
        return f"📊 *{target_day.isoformat()} 랭킹 리포트* — 캡처된 스냅샷이 없어요. (PC가 꺼져있었거나 봇 미실행)"

    aggregated = _aggregate(rows)

    # 히어로 / 비히어로 분리
    hero_aggs = sorted(
        [a for a in aggregated.values() if a["is_hero"]],
        key=lambda a: (a["peak_rank"], -a["hours_in_chart"]),
    )
    other_aggs = sorted(
        [a for a in aggregated.values() if not a["is_hero"]],
        key=lambda a: (a["peak_rank"], -a["hours_in_chart"]),
    )

    # 히어로 진입/미진입
    in_chart_hero_uids = {gn for gn, a in aggregated.items() if a["is_hero"]}
    missing_hero_uids = hero_uids - in_chart_hero_uids

    # ─── 메시지 빌드 ───
    lines = []
    lines.append(f"📊 *{target_day.isoformat()} 무탠다드 랭킹 리포트* (Top 300 기준)")
    lines.append(f"_캡처 {n_snapshots}/24 스냅샷 · 무탠 계열 누적 등장 {len(aggregated)}개 · "
                 f"히어로 {len(hero_aggs)}/{len(hero_uids)} 진입_")
    lines.append("")

    # 히어로 섹션
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

    # 일반 무탠 (히어로 외)
    lines.append(f"📈 *기타 무탠 계열 진입* (히어로 외 — 상위 10)")
    if other_aggs:
        for a in other_aggs[:10]:
            lines.append(f"  • {_hero_summary_line(a)}")
        if len(other_aggs) > 10:
            lines.append(f"  _… 외 {len(other_aggs) - 10}개_")
    else:
        lines.append("  _없음_")

    lines.append("")

    # 액션 로그
    if actions:
        lines.append("🪧 *어제 액션 로그*")
        for a in actions:
            lines.append(f"  • {a['text']}")
    else:
        lines.append("🪧 _어제 액션 로그 없음_  `m \"<액션내용>\"` 으로 기록 가능")

    return "\n".join(lines)


def run(
    db_path: Path,
    hero_uids: set[str],
    slack_bot_token: str | None,
    slack_target: str | None,
    log: logging.Logger,
    target_day: date | None = None,
    sheets_service=None,
    archive_sheet_id: str | None = None,
    purge_after_archive: bool = True,
) -> dict:
    """실행: target_day(보통 어제) 데이터 집계 → Slack 발송 → Sheet archive → 로컬 DB 정리."""
    if target_day is None:
        target_day = date.today() - timedelta(days=1)

    log.info(persona.starting_task(f"{target_day.isoformat()} 랭킹 일일 리포트", persona.RANKING_BOT))

    report = build_report(db_path, target_day, hero_uids)
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

    # 2) Google Sheet archive
    archive_result = {"long_rows": 0, "wide_rows": 0}
    if sheets_service and archive_sheet_id:
        rows = ranking_db.rankings_for_day(db_path, target_day)
        if rows:
            try:
                archive_result = sheet_archive.append_day(
                    sheets_service, archive_sheet_id, target_day, rows, log
                )
            except Exception as e:
                log.error(persona.step(f"Sheet archive 실패: {e}"))
                purge_after_archive = False  # archive 실패 시 로컬 보존
        else:
            log.info(persona.step(f"{target_day.isoformat()} 데이터 없음 — archive 스킵"))

    # 3) 로컬 DB에서 archive 한 날짜 데이터 제거 (실패 시 보존)
    if purge_after_archive and (archive_result["long_rows"] > 0):
        purged = ranking_db.purge_day(db_path, target_day)
        log.info(persona.step(
            f"로컬 DB 정리 — rankings {purged['rankings']}, snapshots {purged['snapshots']}, actions {purged['actions']}"
        ))

    log.info(persona.task_done_ok(f"{target_day.isoformat()} 리포트 + archive 완료"))
    return {
        "target_day": target_day,
        "report_len": len(report),
        "slack_sent": sent,
        "archive": archive_result,
    }
