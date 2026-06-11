"""26FW 히어로 리스트 × PLM 마일스톤 → 목업 HEROES 배열(15 시리즈) 생성 후 index.html 교체."""
import json
import re
import datetime
from pathlib import Path
from collections import defaultdict, Counter
import sys
from soo.auth import get_credentials, build_services
from soo.hero_ops.plm_ingest import (
    parse_milestone_dbx, parse_milestone_dbx_from_drive, parse_milestone_dbx_from_sheet)

TODAY = datetime.date.today()
LOCAL_PLM = next((Path(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--local=")), None)
DO_PUSH = "--push" in sys.argv
USE_SHEET = "--sheet" in sys.argv   # 구글시트(데이터브릭스 자동출력)에서 읽기 — 자동화 경로

# MDP 26FW 추출 트랙별 베이스라인 (단계 n → 'YYYY-MM-DD'). ⚠ 사용자 확인 대상.
BASELINE = {
    "가을": {3: "2025-12-19", 4: "2026-01-22", 6: "2026-01-28", 7: "2026-01-28",
            8: "2026-02-20", 9: "2026-02-20", 10: "2026-04-17", 11: "2026-05-01",
            12: "2026-05-01", 13: "2026-08-01"},
    "겨울": {3: "2026-01-14", 4: "2026-02-05", 6: "2026-02-24", 7: "2026-02-24",
            8: "2026-02-27", 9: "2026-02-27", 10: "2026-05-04", 11: "2026-05-25",
            12: "2026-05-25", 13: "2026-09-01"},
}
def season_to_track(s):  # 간절기→가을, 겨울·기모·기타→겨울
    return "가을" if s == "간절기" else "겨울"
def _d(s):
    return datetime.date.fromisoformat(s) if s and len(s) == 10 else None

HERO_SHEET = "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"
import os
# 배포 repo 경로 — 기본은 형제 폴더, GitHub Actions 등에선 APP_REPO_PATH 로 오버라이드
APP_REPO = Path(os.environ.get("APP_REPO_PATH") or (Path(__file__).parent.parent / "hero-master-app"))
HTML = APP_REPO / "public" / "app.html"

ROOT = Path(__file__).parent
_svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
sheets, drive = _svc["sheets"], _svc["drive"]

ITEM_KO = {"Down": "다운", "Sweater": "니트", "Fleece": "플리스", "Pants": "팬츠",
           "Shirt": "셔츠", "T-Shirts": "티셔츠", "Acc": "액세서리", "Outer": "아우터"}
STYLE_RE = re.compile(r"^M[A-Z0-9]{8}$")
# 단계 n → PLM 마일스톤 (StageCell stages dict 키)
STAGE_PLM = {3: 3, 4: 4, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10, 11: 11, 12: 12, 13: 13}

# plm_status → 완료 도달 단계(=이 단계 이하는 날짜 없어도 완료 간주). 규칙 B.
# ⚠ PLM 상태는 깔끔한 순차 라이프사이클 아님 — 실측(상태별 actual 도달 중앙값)으로 보정:
#   New=4 Proto=4 QC=10 PO Issued=11 PP Confirmed=12 Final Cost Set=12. 보수적으로 아래값 사용.
PLM_STATUS_FLOOR = {
    "New": 2, "Proto Approved": 3, "QC Confirmed": 10,
    "PO Issued": 9, "PP Confirmed": 12, "Final Cost Set": 11,
}
ORDER = [3, 4, 6, 7, 8, 9, 10, 11, 12, 13]  # 실작업 단계(0~2,5 하드코딩 done)

# ── 히어로 리스트 읽기 ──
# --sheet 모드: SA가 닿는 PLM 시트의 HERO_STY 탭(★MSTRD HERO STY를 IMPORTRANGE 미러)에서 읽기.
#   (외부 SA는 ★MSTRD 직접 접근 불가 — org 외부공유 차단. 로컬은 기존대로 ★MSTRD 직접.)
if USE_SHEET:
    from soo.hero_ops.plm_ingest import DBX_SHEET_ID
    _hero_book, _hero_range = DBX_SHEET_ID, "HERO_STY!A7:M400"
else:
    _hero_book, _hero_range = HERO_SHEET, "'HERO STY'!A7:M400"
res = sheets.spreadsheets().values().get(
    spreadsheetId=_hero_book, range=_hero_range,
    valueRenderOption="UNFORMATTED_VALUE").execute()
series_rows = defaultdict(list)   # series -> list of dict
series_order = []
for r in res.get("values", []):
    def c(i): return (str(r[i]).strip() if i < len(r) and r[i] is not None else "")
    if c(1) not in ("HERO", "HERO SUB"):
        continue
    style = c(2) or c(0)
    if not STYLE_RE.match(style):
        continue
    series = c(3)
    if not series:
        continue
    if series not in series_rows:
        series_order.append(series)
    series_rows[series].append({
        "style": style, "cls": c(1), "team": c(6), "item": c(7), "season": c(9), "name": c(12),
    })

# ── PLM (공유드라이브 최신본 자동읽기; --local=경로 로 로컬 파일 사용) ──
if LOCAL_PLM:
    recs = parse_milestone_dbx(LOCAL_PLM)
    print(f"PLM 소스(로컬, 데이터브릭스버전 탭): {LOCAL_PLM}")
elif USE_SHEET:
    recs = parse_milestone_dbx_from_sheet(sheets)
    print(f"PLM 소스(구글시트, 데이터브릭스 자동출력): {len(recs)} 스타일")
else:
    meta, recs = parse_milestone_dbx_from_drive(drive)
    print(f"PLM 소스(드라이브, 데이터브릭스버전 탭): {meta['name']} (수정 {meta['modifiedTime']})")
plm = {rec.style_no: rec for rec in recs}

def rollup(matched, stage_n):
    """matched: list of plm rec. stage status + 대표 날짜."""
    mil = STAGE_PLM[stage_n]
    cells = [m.stages.get(mil) for m in matched]
    cells = [c for c in cells if c]
    total = len(matched)
    done = [c for c in cells if c.actual]
    if total == 0:
        return "pending", ""
    if len(done) == total:
        latest = max(c.actual for c in done)
        return "done", f"{len(done)}/{total} 완료 · ~{latest}"
    if len(done) > 0:
        return "progress", f"진행 {len(done)}/{total}"
    # 아무도 actual 없음 — est 있으면 진행 임박, 아니면 미시작
    est = [c.est for c in cells if c.est]
    if est:
        return "pending", f"예정 ~{min(est)}"
    return "pending", ""

heroes = []
for i, series in enumerate(series_order, 1):
    rows = series_rows[series]
    styles = [r["style"] for r in rows]
    matched = [plm[s] for s in styles if s in plm]
    # 카테고리·트랙·팀 대표값
    item = Counter(r["item"] for r in rows if r["item"]).most_common(1)
    category = ITEM_KO.get(item[0][0], item[0][0]) if item else "기타"
    seas = Counter(r["season"] for r in rows if r["season"]).most_common(1)
    track = {"간절기": "가을", "겨울": "겨울", "여름": "여름"}.get(seas[0][0] if seas else "", "겨울")

    # STY별 세부 (롤업 안 함) — 시즌→트랙 베이스라인 + plm_status floor 적용
    stys = []
    for row in rows:
        rec = plm.get(row["style"])
        track = season_to_track(row.get("season", ""))
        bl = BASELINE[track]
        unregistered = rec is None                       # 규칙 D: PLM에 코드 없음
        plm_status = rec.plm_status if rec else "PLM 미등록"
        dropped = (plm_status == "Dropped")
        carry = bool(rec.carryover) if (rec and rec.carryover is not None) else False
        # 완료 바닥선(floor): 이 단계 이하는 날짜 없어도 완료로 간주
        #   A) actual 찍힌 최대 실단계 (후속단계 완료 → 선행 완료)
        #   B) plm_status 도달 단계
        #   C) carryover면 품평회·GO-DROP(4)까지 면제
        actual_stages = [n for n in ORDER if rec and rec.stages.get(n) and rec.stages[n].actual]
        floor = max([-1] + actual_stages + [PLM_STATUS_FLOOR.get(plm_status, -1)])
        if carry:
            floor = max(floor, 4)
        sst, sdt = [], []
        for n in range(14):
            if n in (0, 1, 2):
                sst.append("done"); sdt.append("기획 완료"); continue
            if n == 5:
                sst.append("done"); sdt.append("1차수량"); continue
            cell = rec.stages.get(n) if rec else None
            base = bl.get(n)
            actual = cell.actual if cell else None
            if actual and len(actual) == 10:
                dd = (_d(actual) - _d(base)).days if base else None
                tag = f" (기준 {base}, {'+' if (dd or 0) > 0 else ''}{dd}일)" if dd is not None else ""
                sst.append("done"); sdt.append(actual + tag)
            elif unregistered:
                sst.append("unknown"); sdt.append("PLM 미등록 (신상/리뉴얼 추정 — 등록·진척 확인 필요)")
            elif n <= floor:                              # 규칙 A/B/C: 완료 추정 (날짜 미기록)
                why = "후속 단계 완료" if (actual_stages and n < max(actual_stages)) else \
                      ("캐리오버" if carry and n in (3, 4) else f"PLM 상태 '{plm_status}'")
                sst.append("done"); sdt.append(f"완료 추정 — {why} (날짜 미기록)")
            elif base and _d(base) and _d(base) < TODAY:
                sst.append("delayed"); sdt.append(f"지연! 기준 {base} 경과" + (f" / 예정 {cell.est}" if cell and cell.est else ""))
            elif cell and cell.est:
                sst.append("pending"); sdt.append(f"예정 {cell.est} (기준 {base})")
            else:
                sst.append("pending"); sdt.append(f"기준 {base}" if base else "")
        # 현재 진행 단계(첫 미완료·미지연·미unknown) = progress
        for n in ORDER:
            if sst[n] not in ("done", "delayed", "unknown"):
                sst[n] = "progress"; break
        stys.append({
            "style": row["style"], "name": row["name"] or row["style"],
            "cls": row["cls"], "team": row["team"], "track": track,
            "plm_status": plm_status, "carryover": carry,
            "unregistered": unregistered, "dropped": dropped,
            "ownerMD": (rec.md_nm if rec and rec.md_nm else "미지정"),
            "ownerDesigner": (rec.ds_nm if rec and rec.ds_nm else "미지정"),
            "ownerSourcing": (rec.sc_nm if rec and rec.sc_nm else "미지정"),
            "stages": sst, "dates": sdt,
        })
    # 정렬: Main(HERO) 먼저 → Sub, 각 안에서 남성→여성→키즈
    TEAM_ORDER = {"남성": 0, "여성": 1, "키즈": 2}
    stys.sort(key=lambda s: (0 if s["cls"] == "HERO" else 1,
                             TEAM_ORDER.get(s["team"], 3), s["style"]))

    # 히어로 단위 stages = STY 롤업 (홈 카드/KPI 일관성)
    stages, dates = [], []
    for n in range(14):
        col = [s["stages"][n] for s in stys] or ["pending"]
        if "delayed" in col:
            st = "delayed"
        elif all(x == "done" for x in col):
            st = "done"
        elif any(x in ("done", "progress") for x in col):
            st = "progress"
        else:
            st = "pending"
        stages.append(st)
        dates.append("기획 완료" if n in (0, 1, 2, 5) else f"{sum(x=='done' for x in col)}/{len(col)} 완료")

    # 히어로 대표 담당자 = 매칭된 STY 중 최빈 (실명; 없으면 미지정)
    def _top(attr):
        vals = [getattr(m, attr) for m in matched if getattr(m, attr, None)]
        return Counter(vals).most_common(1)[0][0] if vals else "미지정"
    owner_md, owner_ds = _top("md_nm"), _top("ds_nm")

    heroes.append({
        "id": f"26FW_{i:03d}", "season": "26FW", "track": track,
        "name": series, "category": category,
        "ownerMD": owner_md, "ownerDesigner": owner_ds,
        "styles": styles,
        "stages": stages, "dates": dates,
        "stage5": {"tentativeColors": [], "inputs": {},
                   "confirmed": {"online_sales": None, "offline_sales": None}, "completedAt": None},
        "stage8": {"sentAt": None, "poQuantities": {}},
        "stys": stys,
        "_plmMatched": len(matched), "_styleCount": len(styles),
    })

# ── app.html HEROES 배열 + APP_TODAY 교체 ──
html = HTML.read_text(encoding="utf-8")
clean = [{k: v for k, v in h.items() if not k.startswith("_")} for h in heroes]
new_block = "const HEROES = " + json.dumps(clean, ensure_ascii=False, indent=2) + ";"
html2, n = re.subn(r"const HEROES = \[.*?\n\];", new_block, html, count=1, flags=re.DOTALL)
assert n == 1, f"HEROES 배열 교체 실패 (matched {n})"
html2, nt = re.subn(r"const APP_TODAY = '[^']*';",
                    f"const APP_TODAY = '{TODAY.isoformat()}';", html2, count=1)
HTML.write_text(html2, encoding="utf-8")

print(f"교체 완료: {len(heroes)} 히어로(시리즈) · APP_TODAY→{TODAY.isoformat()}(교체 {nt})")
for h in heroes:
    done = sum(1 for s in h["stages"] if s == "done")
    prog = sum(1 for s in h["stages"] if s == "progress")
    print(f"  {h['id']} {h['name'][:16]:16} {h['track']:3} {h['category']:5} "
          f"STY {h['_styleCount']:2}(PLM {h['_plmMatched']:2}) | 완료{done} 진행{prog}")

# ── 배포 (--push) ──
if DO_PUSH:
    import subprocess
    def git(*a): return subprocess.run(["git", "-C", str(APP_REPO), *a])
    git("add", "public/app.html")
    if git("commit", "-m", f"데이터 갱신 {TODAY.isoformat()} — {len(heroes)} 히어로").returncode == 0:
        if git("push").returncode == 0:
            print("[완료] git push -> Vercel 자동 재배포 (1~2분 후 반영)")
        else:
            print("[주의] push 실패 — 수동으로 확인 필요")
    else:
        print("변경 없음(커밋 스킵)")
else:
    print(f"\n→ 배포하려면 --push 옵션, 또는 수동:\n  git -C \"{APP_REPO}\" add -A && git commit -m 갱신 && git push")
