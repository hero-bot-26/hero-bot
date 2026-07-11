# -*- coding: utf-8 -*-
"""app.html의 const INBOUND_BOARD 만 재생성·주입 (다른 세션 작업 클로버 방지).
launch 메타는 app.html의 기존 LAUNCH_26FW에서 추출. DBX 실입고=입고일자별 탭."""
import json, re
from pathlib import Path
from soo.auth import get_credentials, build_services
from soo.hero_ops.inbound_board import build_inbound_board, load_dbx_actuals
import datetime

ROOT = Path(__file__).parent
APP = Path(r"C:\Users\MUSINSA\hero-master-app\public\app.html")
TODAY = datetime.date.today()

svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
sheets = svc["sheets"]

html = APP.read_text(encoding="utf-8")

# 기존 LAUNCH_26FW에서 launch/status 메타 추출
_lm = {}
m = re.search(r"const LAUNCH_26FW = (\{.*?\});", html, flags=re.DOTALL)
if m:
    try:
        lo = json.loads(m.group(1))
        for x in lo.get("heroes", []):
            _lm[x["name"]] = {"launch": x.get("launch"), "status": x.get("status")}
        print(f"LAUNCH_26FW 메타 추출: {len(_lm)}종")
    except Exception as e:
        print(f"[주의] LAUNCH_26FW 파싱 실패: {e}")

dbx = load_dbx_actuals(sheets)
print(f"DBX 실입고 SKU 수: {len(dbx) if dbx else 0}")
board = build_inbound_board(sheets, as_of=TODAY, launch_meta=_lm, dbx_actuals=dbx)

block = "const INBOUND_BOARD = " + json.dumps(board, ensure_ascii=False) + ";"
html2, n = re.subn(r"const INBOUND_BOARD = \{.*?\};", lambda _m: block, html, count=1, flags=re.DOTALL)
assert n == 1, f"INBOUND_BOARD 교체 실패 (n={n})"
APP.write_text(html2, encoding="utf-8")

# 요약 출력
from collections import Counter
st = Counter(s["status"] for h in board["heroes"] for s in h["skus"])
nsku = sum(h["sku_count"] for h in board["heroes"])
nrecv = sum(1 for h in board["heroes"] for s in h["skus"] for p in s["planned"] if p.get("recv", 0) > 0)
nlefto = sum(len(s.get("leftover", [])) for h in board["heroes"] for s in h["skus"])
print(f"주입 완료: {len(board['heroes'])}히어로 · SKU {nsku} · 날짜버킷 {len(board['days'])} · 상태{dict(st)}")
print(f"차수 recv>0: {nrecv}개 · 예정외입고(leftover): {nlefto}건")
# 데님 샘플로 차수별 recv 검증
for h in board["heroes"]:
    if h["name"] == "데님팬츠":
        print("\n[데님팬츠 차수별 예정 vs 실입고]")
        for s in h["skus"][:4]:
            print(f"  {s['sku']} {s['color']}")
            for p in s["planned"]:
                print(f"    예정 {p['date']} {p['qty']:>6,} → 실 {p.get('recv',0):>6,} (입고일 {p.get('recv_dates')})")
            if s.get("leftover"):
                print(f"    예정외: {s['leftover']}")
