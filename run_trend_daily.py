"""트렌드 레이더 — 매일 Top500 캡처 → 어제 대비 급상승/신규 → (Slack 발행).

히어로 랭킹봇과 별개. Google 인증 불필요(스크래퍼+로컬 JSON만). Slack 발행 시에만 토큰 사용.

사용:
  python run_trend_daily.py --dry-run        # 콘솔 미리보기만
  python run_trend_daily.py --as-of 2026-07-14
  python run_trend_daily.py                   # 실제 Slack 발행 (채널 설정 시)
"""

from __future__ import annotations

import argparse
import io
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from soo import VIEWS, persona
from soo.persona import Persona
from soo.secrets import load_secrets
from soo.storage import trend_snapshot
from soo.tasks import trend_radar, trend_keywords, trend_publish

TREND_BOT = Persona(
    name="무신사 트렌드 레이더",
    tagline="전사용 시장 트렌드 발행봇",
    slack_username="무신사 트렌드 레이더",
    slack_icon_emoji=":satellite_antenna:",
)

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
SECRETS_PATH = ROOT / "secrets.yaml"
LOG_DIR = ROOT / "logs"


def _utf8():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


def _preview(view: str, new_entries, risers, log) -> None:
    log.info(persona.step(f"┌─ [{view}] 급상승 {len(risers)} · 신규 {len(new_entries)}"))
    for m in risers[:5]:
        log.info(persona.step(f"│  🔥 {m.prev_rank}위→{m.rank}위 ▲{m.jump}  {m.name[:40]}  ({m.brand})"))
    for m in new_entries[:5]:
        log.info(persona.step(f"│  🆕 신규→{m.rank}위  {m.name[:40]}  ({m.brand})"))


def main() -> int:
    _utf8()
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Slack 발송 없이 콘솔만")
    p.add_argument("--as-of", type=str, default=None, help="대상 날짜 YYYY-MM-DD (기본 오늘 KST)")
    p.add_argument("--no-save", action="store_true", help="스냅샷 저장 생략(테스트용)")
    args = p.parse_args()

    target_day = date.fromisoformat(args.as_of) if args.as_of else datetime.now(KST).date()
    log = persona.setup_logger(LOG_DIR, dry_run=args.dry_run)

    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")).get("trend", {})
    snap_root = ROOT / cfg.get("snapshot_dir", "data/trend_snap")
    top_n = int(cfg.get("top_n", 500))

    try:
        views = trend_radar.capture(
            top_n=top_n,
            section_id=int(cfg.get("section_id", 199)),
            sub_pan=cfg.get("sub_pan", "product"),
            log=log,
        )
    except Exception as e:
        log.error(persona.task_failed(f"캡처 실패: {e}"))
        log.debug(traceback.format_exc())
        return 1

    if not args.no_save:
        trend_radar.save_snapshot(
            root=snap_root, target_day=target_day, views=views,
            captured_at=datetime.now(KST).isoformat(timespec="seconds"),
            keep_days=int(cfg.get("keep_days", 14)), log=log,
        )

    # 베이스라인: 어제(DoD). 없으면 cold start.
    yday = trend_snapshot.load(snap_root, target_day - timedelta(days=1))
    if yday is None:
        log.info(persona.task_done_skip(
            f"어제({(target_day - timedelta(days=1)).isoformat()}) 스냅샷 없음 — 오늘치만 적재. "
            f"급상승/뜨는 키워드는 내일부터 산출."
        ))
        return 0

    jump = int(cfg.get("jump_threshold", 30))
    focus = int(cfg.get("top_focus", 300))
    kd = trend_keywords.KeywordDict.load(ROOT / "data" / "trend_keywords.json")

    per_view: dict[str, tuple] = {}
    kw_by_view: dict[str, list] = {}
    for _, view in VIEWS:
        base = trend_snapshot.rank_map(yday, view)
        new_entries, risers = trend_radar.compute_movers(
            views[view], base, jump_threshold=jump, top_focus=focus,
        )
        kws = trend_keywords.rising_keywords(risers + new_entries, kd)
        per_view[view] = (new_entries, risers, kws)
        kw_by_view[view] = kws
        _preview(view, new_entries, risers, log)

    # 발행 메시지 = '전체' 뷰 중심. 서술은 3뷰 키워드 종합.
    new_e, risers, kws = per_view["전체"]
    narrative = trend_publish.build_narrative(kw_by_view)
    thumbs = (kws[0].examples if kws else []) + risers[:4]
    fallback, blocks = trend_publish.build_blocks(
        target_day, narrative, risers, new_e, kws, thumbs,
        sheet_url="https://www.musinsa.com/main/musinsa/ranking",
    )

    channel = cfg.get("slack_channel", "")
    if args.dry_run or not channel:
        log.info(persona.step("── 발행 미리보기 ──"))
        log.info(persona.step(narrative))
        reason = "dry-run" if args.dry_run else "trend.slack_channel 미설정"
        log.info(persona.task_done_skip(f"Slack 발송 생략 ({reason}) — 블록 {len(blocks)}개 준비됨"))
        return 0

    secrets = load_secrets(SECRETS_PATH)
    ts = persona.send_slack(
        fallback, bot_token=secrets.get("slack_bot_token"), target=channel,
        persona=TREND_BOT, log=log, blocks=blocks,
    )
    if ts:
        log.info(persona.task_done_ok(f"트렌드 레이더 발행 완료 → {channel}"))
        return 0
    log.error(persona.task_failed("Slack 발송 실패"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
