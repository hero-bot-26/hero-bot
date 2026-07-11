"""보드에 포함된 생산관리 행들의 AZ(시즌) 분포 + 예정량 확인.
질문: 'AZ=26FW만 더했나?' → 실제는 품번매핑+컷오프라 AZ 혼재."""
from pathlib import Path
from collections import defaultdict, Counter
from soo.auth import get_credentials, build_services
from soo.hero_ops.inbound_board import build_pumbon2hero, CUTOFF, _pdate, _num, _g

ROOT = Path(__file__).parent
svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
sheets = svc["sheets"]
p2h = build_pumbon2hero(sheets)

PID = "13R4gcJ7cDlReY-vwjXZf0kMZ7tC4kr2-S7PC9uziVUQ"
res = sheets.spreadsheets().values().get(
    spreadsheetId=PID, range="'생산관리'", valueRenderOption="FORMATTED_VALUE").execute()
vals = res.get("values", [])

# 보드와 동일한 행 선택(품번매핑 + 컷오프), AZ(col51) 분포
az_qty = defaultdict(int)     # AZ값 → 예정량 합
az_rows = Counter()
hero_az = defaultdict(lambda: defaultdict(int))  # 히어로 → AZ → 예정량
for i in range(6, len(vals)):
    r = vals[i]
    style_k, style_m = _g(r, 10), _g(r, 12)
    sku = _g(r, 8); base = sku.rsplit("-", 1)[0] if "-" in sku else sku
    hero = p2h.get(style_k) or p2h.get(style_m) or p2h.get(base)
    if not hero:
        continue
    pd, ad = _pdate(_g(r, 36)), _pdate(_g(r, 40))
    if not ((pd and pd >= CUTOFF) or (ad and ad >= CUTOFF)):
        continue
    az = _g(r, 51) or "(빈칸)"
    pq = _num(_g(r, 37))
    az_qty[az] += pq
    az_rows[az] += 1
    hero_az[hero][az] += pq

print("=== 보드 포함 행의 AZ(시즌) 분포 — 예정량 기준 ===")
tot = sum(az_qty.values())
for az, q in sorted(az_qty.items(), key=lambda x: -x[1]):
    print(f"  {az:10s} : {q:>10,}  ({az_rows[az]}행, {q/tot*100:.0f}%)")
print(f"  {'합계':10s} : {tot:>10,}")

print("\n=== 히어로별 AZ 구성 (예정량) ===")
for hero, azm in hero_az.items():
    parts = ", ".join(f"{a}={q:,}" for a, q in sorted(azm.items(), key=lambda x: -x[1]))
    print(f"  {hero:14s}: {parts}")
