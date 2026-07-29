# -*- coding: utf-8 -*-
"""실적 시트가 '전일 기준'으로 다 채워질 때까지 기다린다 (앱 갱신 CI 앞단).

배경(2026-07-29 사고): DBX 잡이 시트를 채우는 데 ~3시간 걸리는데 앱 CI가 그 도중에 읽어,
탭마다 기준일이 섞였다(MTD는 7/28인데 FWTD는 7/27 → 26FW 누계가 하루 통째로 밀림).
이제 잡은 09:30에 시작하므로, CI는 랭킹봇 완료(09시대) 직후 시작해 시트가 준비될 때까지 대기한다.

- 준비 판정 = `sales_rollup.check_freshness`(각 raw 탭 1행 라벨의 집계 종료일 == 전일)
- 타임아웃이어도 실패로 끝내지 않는다 → 생성기가 자체 신선도 게이트로 실적·PMKT 주입만 건너뛰고
  나머지(IMC·PLM 등)는 정상 갱신한다.

사용: python wait_sheet_fresh.py [--timeout-min 240] [--interval-min 5]
"""
import argparse
import datetime
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from soo.auth import build_services, get_credentials
from soo.hero_ops.sales_rollup import SALES_SHEET_ID, check_freshness

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout-min", type=int, default=240)
    ap.add_argument("--interval-min", type=float, default=5)
    a = ap.parse_args()

    sheets = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))["sheets"]
    kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    as_of = (kst.date() - datetime.timedelta(days=1)).strftime("%Y%m%d")
    print(f"[wait] 기대 기준일(전일) = {as_of} · 최대 {a.timeout_min}분 대기")

    deadline = time.time() + a.timeout_min * 60
    while True:
        fresh, bad = check_freshness(sheets, SALES_SHEET_ID, as_of)
        if fresh:
            print("[wait] 시트 준비 완료 — 생성 진행")
            return 0
        left = int((deadline - time.time()) / 60)
        print(f"[wait] 아직 {len(bad)}개 탭이 미갱신({' · '.join(bad[:4])}) — {left}분 남음")
        if time.time() >= deadline:
            print("[wait] 타임아웃 — 그대로 진행(생성기 게이트가 실적·PMKT 주입을 건너뛴다)")
            return 0
        time.sleep(min(a.interval_min * 60, max(30, deadline - time.time())))


if __name__ == "__main__":
    sys.exit(main())
