# -*- coding: utf-8 -*-
"""라이브 노트북(잡 334354908178394)에 '입고일자별' 셀을 영구 추가 (매일 07시 자동 갱신).
_nb_live.py(export본) 입고현황 셀 뒤에 삽입 → import. QUERY는 _dbx_write_inbound_daily.py와 동일.
멱등: 이미 '입고일자별' 있으면 스킵."""
import os, base64, requests
from pathlib import Path
from _inbound_query import QUERY   # 동일 쿼리 재사용 (import 시 잡 재실행 없음)

ROOT = Path(__file__).parent
HOST = "https://musinsa-data-ws.cloud.databricks.com"
TOKEN = (ROOT / "dbx_token.txt").read_text(encoding="utf-8").strip()
H = {"Authorization": f"Bearer {TOKEN}"}
PATH = "/Users/sooyoung.moon@musinsa.com/히어로 마스터 앱_실적"

src = (ROOT / "_nb_live.py").read_text(encoding="utf-8")
if "입고일자별" in src:
    print("이미 '입고일자별' 셀 존재 → 스킵"); raise SystemExit

anchor = '  insert_query_result("입고현황", spark.sql(inbound_query), label="25년 11월 1일부터 전일자 누적 입고")\n'
assert anchor in src, "입고현황 앵커 없음 — 노트북 재export 필요"

# 노트북 셀 코드는 2칸 들여쓰기. 쿼리도 2칸 정렬.
q_indented = "\n".join(("  " + ln if ln.strip() else ln) for ln in QUERY.strip("\n").split("\n"))
cell = (
    "\n# COMMAND ----------\n\n"
    "# MAGIC %md\n"
    "# MAGIC ### ▼ 입고일자별 (WMS 실입고, 일자×품번-컬러)\n"
    "# MAGIC - '입고일자별' — 입고 보드 실적 소스 (BARCODE 파싱 품번-컬러 단위)\n\n"
    "# COMMAND ----------\n\n"
    "  inbound_daily_query = \"\"\"\n" + q_indented + "\n  \"\"\"\n"
    "  insert_query_result(\"입고일자별\", spark.sql(inbound_daily_query), label=\"25.11.01~전일 일자별 실입고 (BARCODE 파싱, 품번-컬러 단위)\")\n"
)
new_src = src.replace(anchor, anchor + cell, 1)
(ROOT / "_nb_live_updated.py").write_text(new_src, encoding="utf-8")
print(f"셀 삽입 → _nb_live_updated.py ({new_src.count(chr(10))}줄, +{new_src.count(chr(10))-src.count(chr(10))})")

# import (영구 반영)
requests.post(f"{HOST}/api/2.0/workspace/import", headers=H, json={
    "path": PATH, "format": "SOURCE", "language": "PYTHON",
    "content": base64.b64encode(new_src.encode("utf-8")).decode("ascii"), "overwrite": True}, timeout=60).raise_for_status()
print("라이브 노트북 import 완료 — 내일 07시 잡부터 '입고일자별' 자동 갱신")
