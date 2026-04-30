"""매일 09:00 — Sheet의 Long 탭에서 어제 데이터 read → 집계 → Slack → Wide 탭 append.

사용:
  python run_ranking_daily.py
  python run_ranking_daily.py --as-of 2026-04-30
  python run_ranking_daily.py --dry-run        # Slack 발송 없이 콘솔만
"""

from __future__ import annotations

import argparse
import io
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

import yaml

from soo import persona
from soo.auth import build_services, get_credentials
from soo.hero_list import load_hero_list
from soo.secrets import load_secrets
from soo.tasks import ranking_daily


ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
SECRETS_PATH = ROOT / "secrets.yaml"
CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"
LOG_DIR = ROOT / "logs"


def _utf8():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


def main() -> int:
    _utf8()
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Slack 발송 없이 콘솔만")
    p.add_argument("--as-of", type=str, default=None, help="대상 날짜 YYYY-MM-DD (기본 어제)")
    args = p.parse_args()

    target_day = date.fromisoformat(args.as_of) if args.as_of else date.today() - timedelta(days=1)

    log = persona.setup_logger(LOG_DIR, dry_run=args.dry_run)
    cfg_full = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = cfg_full.get("ranking", {})
    archive_sheet_id = cfg["archive_sheet_id"]
    archive_sheet_url = cfg.get("archive_sheet_url")

    try:
        creds = get_credentials(CREDENTIALS_PATH, TOKEN_PATH)
        svc = build_services(creds)
        sheets_svc = svc["sheets"]
        heroes = load_hero_list(sheets_svc, cfg["hero_sheet_id"])
        hero_uids = set(heroes.keys())
        log.info(persona.step(f"히어로 리스트 로드 — {len(hero_uids)}개"))
    except Exception as e:
        log.error(persona.task_failed(f"Google 인증/히어로 로드 실패: {e}"))
        log.debug(traceback.format_exc())
        return 1

    secrets = load_secrets(SECRETS_PATH)
    slack_token = None if args.dry_run else secrets.get("slack_bot_token")
    slack_target = None if args.dry_run else secrets.get("slack_target")

    try:
        ranking_daily.run(
            sheets_service=sheets_svc,
            sheet_id=archive_sheet_id,
            hero_uids=hero_uids,
            slack_bot_token=slack_token,
            slack_target=slack_target,
            log=log,
            target_day=target_day,
            sheet_url=archive_sheet_url,
        )
    except Exception as e:
        log.error(persona.task_failed(str(e)))
        log.debug(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
