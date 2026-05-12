"""매시간 정각 — 무신사 랭킹 캡처 → Sheet의 Long 탭에 직접 append.

사용:
  python run_ranking_hourly.py
  python run_ranking_hourly.py --dry-run   # Sheet 호출 없이 fetch만
"""

from __future__ import annotations

import argparse
import io
import sys
import traceback
from pathlib import Path

import yaml

from soo import persona, VIEWS
from soo.auth import build_services, get_credentials
from soo.hero_list import load_hero_list
from soo.tasks import ranking_hourly


ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"
LOG_DIR = ROOT / "logs"


def _utf8():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


def main() -> int:
    _utf8()
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    log = persona.setup_logger(LOG_DIR, dry_run=args.dry_run)
    cfg_full = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = cfg_full.get("ranking", {})
    if not cfg:
        log.error(persona.task_failed("config.yaml에 'ranking' 섹션이 없어요"))
        return 1

    brand_keywords = cfg.get("brand_keywords") or [
        "무신사 스탠다드", "무신사 스탠다드 우먼", "무신사 스탠다드 키즈",
    ]
    top_n = int(cfg.get("top_n", 100))
    section_id = int(cfg.get("section_id", 199))
    sub_pan = cfg.get("sub_pan", "product")
    archive_sheet_id = cfg["archive_sheet_id"]
    screenshot_threshold = int(cfg.get("screenshot_threshold", 10))
    screenshot_folder_id = (cfg.get("screenshot_folder_id") or "").strip()
    _crop = cfg.get("screenshot_crop_to_rank", 12)
    screenshot_crop_to_rank = int(_crop) if _crop else None

    if args.dry_run:
        from soo.scrapers.musinsa_ranking import fetch_top, filter_by_brand
        for gf, view in VIEWS:
            items = fetch_top(n=top_n, section_id=section_id, sub_pan=sub_pan, gf=gf)
            matched = filter_by_brand(items, brand_keywords)
            log.info(persona.step(f"[DRY][{view}] fetched={len(items)} matched={len(matched)}"))
        return 0

    try:
        creds = get_credentials(CREDENTIALS_PATH, TOKEN_PATH)
        svc = build_services(creds)
        heroes = load_hero_list(svc["sheets"], cfg["hero_sheet_id"])
        hero_uids = set(heroes.keys())
        log.info(persona.step(f"히어로 리스트 로드 — {len(hero_uids)}개"))
    except Exception as e:
        log.error(persona.task_failed(f"Google 인증/히어로 로드 실패: {e}"))
        log.debug(traceback.format_exc())
        return 1

    try:
        ranking_hourly.run(
            sheets_service=svc["sheets"],
            sheet_id=archive_sheet_id,
            brand_keywords=brand_keywords,
            hero_uids=hero_uids,
            log=log,
            top_n=top_n,
            section_id=section_id,
            sub_pan=sub_pan,
            drive_service=svc["drive"],
            screenshot_threshold=screenshot_threshold,
            screenshot_folder_id=screenshot_folder_id,
            screenshot_crop_to_rank=screenshot_crop_to_rank,
        )
    except Exception as e:
        log.error(persona.task_failed(str(e)))
        log.debug(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
