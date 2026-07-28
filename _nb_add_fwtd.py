# -*- coding: utf-8 -*-
"""노트북에 26FW 누적(7/1~) 기간 추가 — 탭 'FWTD' / '전년FWTD'.
   26FW 실적이 달력 YTD(1/1~)라 캐리오버 STY의 봄 판매가 26FW로 잡히던 문제 해결(사용자 결정: 7/1 고정).
   params 리스트 '맨 뒤'에만 덧붙인다 — 퍼널(_fp)·PMKT(_pmkt_periods)가 인덱스 0/2/4/6을 쓰므로 순서 보존 필수.
   사용: DBX_TOKEN=... python _nb_add_fwtd.py [--run]
"""
import io
import sys
from pathlib import Path

SRC = Path("_nb_live.py")
OUT = Path("_nb_live_updated.py")
BACKUP = Path("_nb_workspace_pre_fwtd.py")

s = io.open(SRC, encoding="utf-8").read()
io.open(BACKUP, "w", encoding="utf-8").write(s)          # 롤백본

OLD = '''  params = {
      "start_dt": ["20260101","20250101", mtd_start, mtd_start_yoy, week_start, week_start_yoy, day_start, day_start_yoy],
      "end_dt":   [ytd_end, ytd_end_yoy, mtd_end, mtd_end_yoy, week_end, week_end_yoy, day_end, day_end_yoy],
      "file_nm":  ["YTD","전년YTD","MTD","전년MTD","WEEK","전년WEEK","DAY","전년DAY"],
  }'''
NEW = '''  # 26FW 누적 = 7/1 고정 시작(시즌 시작). 달력 YTD로 26FW를 보면 캐리오버 STY의 1~6월 판매가
  # 섞여 실제 FW 판매를 못 본다(사용자 확정 2026-07-28). 전년 동기간은 2025-07-01~.
  fw_start     = f"{d.year}0701"
  fw_start_yoy = f"{d.year - 1}0701"

  # ★리스트 '맨 뒤'에만 추가할 것 — 아래 퍼널(_fp)·PMKT(_pmkt_periods)가 인덱스 0/2/4/6을 참조한다.
  params = {
      "start_dt": ["20260101","20250101", mtd_start, mtd_start_yoy, week_start, week_start_yoy, day_start, day_start_yoy,
                   fw_start, fw_start_yoy],
      "end_dt":   [ytd_end, ytd_end_yoy, mtd_end, mtd_end_yoy, week_end, week_end_yoy, day_end, day_end_yoy,
                   ytd_end, ytd_end_yoy],
      "file_nm":  ["YTD","전년YTD","MTD","전년MTD","WEEK","전년WEEK","DAY","전년DAY",
                   "FWTD","전년FWTD"],
  }'''

assert s.count(OLD) == 1, f"params 블록 앵커 {s.count(OLD)}건 (1이어야 함)"
s2 = s.replace(OLD, NEW)

# 헤더 주석의 출력 탭 목록도 현행화
OLD_H = "# 출력 탭(10): YTD/전년YTD/MTD/전년MTD/WEEK/전년WEEK/DAY/전년DAY · 잔여재고 · 입고현황"
NEW_H = "# 출력 탭(12): YTD/전년YTD/MTD/전년MTD/WEEK/전년WEEK/DAY/전년DAY/FWTD/전년FWTD · 잔여재고 · 입고현황\n#   FWTD = 26FW 누적(7/1~, 시즌 기준). 26FW 히어로 실적은 이 탭을 쓴다(달력 YTD는 캐리오버 봄 판매 포함)."
if s2.count(OLD_H) == 1:
    s2 = s2.replace(OLD_H, NEW_H)
else:
    print(f"[주의] 헤더 주석 앵커 {s2.count(OLD_H)}건 — 주석만 스킵(기능 영향 없음)")

io.open(OUT, "w", encoding="utf-8").write(s2)
print(f"패치 완료 → {OUT} (롤백본 {BACKUP})")
print("추가 기간: FWTD =", "d.year0701 ~ ytd_end", "/ 전년FWTD = (d.year-1)0701 ~ ytd_end_yoy")

if "--run" not in sys.argv:
    print("(--run 없음) 워크스페이스 반영·잡 실행은 하지 않음")
    raise SystemExit

import base64
import os

import requests

HOST = "https://musinsa-data-ws.cloud.databricks.com"
H = {"Authorization": f"Bearer {os.environ['DBX_TOKEN']}"}
PATH = "/Users/sooyoung.moon@musinsa.com/히어로 마스터 앱_실적"
content = base64.b64encode(s2.encode("utf-8")).decode("ascii")
r = requests.post(f"{HOST}/api/2.0/workspace/import", headers=H,
                  json={"path": PATH, "format": "SOURCE", "language": "PYTHON",
                        "content": content, "overwrite": True}, timeout=60)
print("import:", r.status_code, "" if r.status_code == 200 else r.text[:300])
if r.status_code != 200:
    raise SystemExit("import 실패 — 롤백 불필요(워크스페이스 미변경)")
jr = requests.post(f"{HOST}/api/2.1/jobs/run-now", headers=H,
                   json={"job_id": 334354908178394}, timeout=30)
print("run-now:", jr.status_code, jr.text[:200])
