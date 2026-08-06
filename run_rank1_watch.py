"""10분마다 — 무탠 계열이 무신사 랭킹 1위에 오르면 즉시 슬랙 알림 (@channel + 스냅샷).

사용:
  python run_rank1_watch.py               # 감시 1회 실행 (알림 조건 충족 시 발송)
  python run_rank1_watch.py --probe       # 1위만 조회하고 종료 (Google/Slack 미사용)
  python run_rank1_watch.py --dry-run     # 시트 기록·발송 없이 메시지만 콘솔 출력
  python run_rank1_watch.py --force       # 오늘 이미 알린 건도 다시 발송 (테스트용)

--probe 는 GitHub Actions에서 "1위가 무탠일 때만" 무거운 스텝(Playwright 설치)을
돌리기 위한 사전 체크. GITHUB_OUTPUT 이 있으면 hit=true/false 를 써 준다.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import traceback
from pathlib import Path

import yaml

from soo import persona
from soo.auth import build_services, get_credentials
from soo.hero_list import load_hero_list
from soo.secrets import load_secrets
from soo.tasks import rank1_alert


ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
SECRETS_PATH = ROOT / "secrets.yaml"
CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"
LOG_DIR = ROOT / "logs"


def _utf8():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


def _write_gh_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def main() -> int:
    _utf8()
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true", help="1위 조회만 하고 종료 (Google/Slack 미사용)")
    p.add_argument("--dry-run", action="store_true", help="시트 기록·슬랙 발송 없이 콘솔만")
    p.add_argument("--force", action="store_true", help="오늘 이미 알린 건도 재발송")
    args = p.parse_args()

    log = persona.setup_logger(LOG_DIR, dry_run=args.dry_run)
    cfg_full = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = cfg_full.get("ranking", {})
    if not cfg:
        log.error(persona.task_failed("config.yaml에 'ranking' 섹션이 없어요"))
        return 1
    acfg = cfg_full.get("rank1_alert", {}) or {}

    if not acfg.get("enabled", True):
        log.info(persona.task_done_skip("rank1_alert.enabled=false — 1위 감시 꺼짐"))
        _write_gh_output("hit", "false")
        return 0

    brand_keywords = cfg.get("brand_keywords") or [
        "무신사 스탠다드", "무신사 스탠다드 우먼", "무신사 스탠다드 키즈",
    ]
    section_id = int(cfg.get("section_id", 199))
    sub_pan = cfg.get("sub_pan", "product")

    # ── probe: 1위가 무탠인지만 확인 (인증 불필요). Actions의 조건부 스텝용.
    if args.probe:
        try:
            hits = rank1_alert.probe(brand_keywords, section_id=section_id, sub_pan=sub_pan, log=log)
        except Exception as e:
            log.error(persona.task_failed(f"probe 실패: {e}"))
            log.debug(traceback.format_exc())
            _write_gh_output("hit", "false")
            return 0  # probe 실패로 워크플로를 붉게 만들지 않는다 (10분 뒤 재시도)
        _write_gh_output("hit", "true" if hits else "false")
        log.info(persona.task_done_ok(
            f"probe — 무탠 1위 {len(hits)}건 ({', '.join(h['view'] for h in hits) or '없음'})"
        ))
        return 0

    archive_sheet_id = cfg["archive_sheet_id"]
    screenshot_folder_id = (cfg.get("screenshot_folder_id") or "").strip()
    _crop = acfg.get("crop_to_rank", cfg.get("screenshot_crop_to_rank", 12))
    crop_to_rank = int(_crop) if _crop else None
    poll_minutes = int(acfg.get("poll_minutes", 10))
    mention_channel = bool(acfg.get("mention_channel", True))

    sheets_svc = drive_svc = None
    heroes: dict = {}
    if not args.dry_run:
        try:
            creds = get_credentials(CREDENTIALS_PATH, TOKEN_PATH)
            svc = build_services(creds)
            sheets_svc, drive_svc = svc["sheets"], svc["drive"]
            heroes = load_hero_list(sheets_svc, cfg["hero_sheet_id"])
            log.info(persona.step(f"히어로 리스트 로드 — {len(heroes)}개"))
        except Exception as e:
            log.error(persona.task_failed(f"Google 인증/히어로 로드 실패: {e}"))
            log.debug(traceback.format_exc())
            return 1

    secrets = load_secrets(SECRETS_PATH)
    slack_token = None if args.dry_run else secrets.get("slack_bot_token")
    slack_target = None if args.dry_run else (acfg.get("slack_channel") or secrets.get("slack_target"))

    try:
        rank1_alert.run(
            sheets_service=sheets_svc,
            sheet_id=archive_sheet_id,
            brand_keywords=brand_keywords,
            heroes=heroes,
            slack_bot_token=slack_token,
            slack_target=slack_target,
            log=log,
            section_id=section_id,
            sub_pan=sub_pan,
            drive_service=drive_svc,
            screenshot_folder_id=screenshot_folder_id,
            crop_to_rank=crop_to_rank,
            poll_minutes=poll_minutes,
            mention_channel=mention_channel,
            force=args.force,
            dry_run=args.dry_run,
        )
    except Exception as e:
        log.error(persona.task_failed(str(e)))
        log.debug(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
