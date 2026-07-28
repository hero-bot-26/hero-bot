# 27SS 보드 단계 스케줄(STY_SCHED_27SS) 즉시 반영 — app.html 그 블록만 교체.
# ★평시엔 생성기 `_gen_26fw_heroes.py`가 매일 CI에서 같은 값을 주입 — 이 스크립트는 즉시 반영/복구용.
#   파싱·가드 로직은 `soo/hero_ops/sched_27ss.py` 하나만 쓰므로 생성기와 결과가 항상 같다.
# 사용: python _populate_27ss_sched.py [--dry]
import datetime as dt
import json
import pathlib
import re
import sys

from soo.auth import get_credentials, build_services
from soo.hero_ops import source_registry as SRCREG
from soo.hero_ops.sched_27ss import load_27ss_sched

ROOT = pathlib.Path.home() / "hero_bot"
APP = pathlib.Path.home() / "hero-master-app" / "public" / "app.html"
DRY = "--dry" in sys.argv

svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
sheets = svc["sheets"]
reg = SRCREG.load_registry(sheets)

html = APP.read_text(encoding="utf-8")
m = re.search(r"const PLM_DATA = (\{.*?\n\});", html, re.DOTALL)
cand = set(json.loads(m.group(1)).keys()) if m else None

sched, warns = load_27ss_sched(sheets, SRCREG.source_id("plm_27ss_req", reg),
                               today=dt.date.today(), only=cand)
if not sched:
    raise SystemExit("스케줄 0건 — 기존값 유지(조용한 0 덮어쓰기 방지)")

blk = "const STY_SCHED_27SS = " + json.dumps(sched, ensure_ascii=False, indent=2) + ";"
html2, n = re.subn(r"const STY_SCHED_27SS = \{.*?\n\};", blk, html, count=1, flags=re.DOTALL)
assert n == 1, f"STY_SCHED_27SS 교체 실패 (matched {n})"

done = sum(1 for v in sched.values() for s in v["stages"] if s == "done")
delayed = sum(1 for v in sched.values() if "delayed" in v["stages"])
print(f"27SS 스케줄 {len(sched)} STY / 후보 {len(cand or [])} · 완료셀 {done} · 지연 STY {delayed}")
for w in warns:
    print(f"   [원천주의] {w}")

if DRY:
    print("(--dry) 파일 미기록")
else:
    APP.write_text(html2, encoding="utf-8")
    print(f"기록 완료: {APP}")
