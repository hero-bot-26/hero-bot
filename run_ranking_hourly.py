"""mini soo — 매시간 정각 무신사 랭킹 캡처 엔트리포인트.

사용:
  python run_ranking_hourly.py             # 정상 1회 캡처
  python run_ranking_hourly.py --dry-run   # 저장 없이 fetch만 (테스트용)
"""

from __future__ import annotations

import argparse
import io
import sys
import traceback
from pathlib import Path

import yaml

from soo import persona
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

    db_path = ROOT / cfg.get("db_path", "data/rankings.db")
    brand_keywords = cfg.get("brand_keywords") or [
        "무신사 스탠다드", "무신사 스탠다드 우먼", "무신사 스탠다드 키즈",
    ]
    top_n = int(cfg.get("top_n", 300))
    section_id = int(cfg.get("section_id", 199))
    sub_pan = cfg.get("sub_pan", "product")

    # 히어로 리스트 — Sheets에서 매번 fetch (변동분 자동 반영)
    try:
        creds = get_credentials(CREDENTIALS_PATH, TOKEN_PATH)
        svc = build_services(creds)
        heroes = load_hero_list(svc["sheets"], cfg["hero_sheet_id"])
        hero_uids = set(heroes.keys())
        log.info(persona.step(f"히어로 리스트 로드 — {len(hero_uids)}개"))
    except Exception as e:
        log.warning(persona.step(f"히어로 리스트 로드 실패 (계속 진행): {e}"))
        hero_uids = set()

    if args.dry_run:
        from soo.scrapers.musinsa_ranking import fetch_top, filter_by_brand
        items = fetch_top(n=top_n, section_id=section_id, sub_pan=sub_pan)
        matched = filter_by_brand(items, brand_keywords)
        hero_hits = [it for it in matched if it.goods_no in hero_uids]
        log.info(persona.step(f"[DRY] fetched={len(items)} matched={len(matched)} hero={len(hero_hits)}"))
        return 0

    try:
        ranking_hourly.run(
            db_path=db_path,
            brand_keywords=brand_keywords,
            hero_uids=hero_uids,
            log=log,
            top_n=top_n,
            section_id=section_id,
            sub_pan=sub_pan,
        )
    except Exception as e:
        log.error(persona.task_failed(str(e)))
        log.debug(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
