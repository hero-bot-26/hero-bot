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

# 앱 "완료 클릭" 기록(단계완료 탭) — 수동 단계 done 판정에 반영(재생성해도 유지).
from soo.hero_ops.triggers import load_completions, load_quantity_inputs, load_grade_inputs, load_mstrd_inputs, parse_mstrd_grades
completions = load_completions(sheets)
print(f"완료 클릭 기록: {len(completions)}건")

# 1차수량(앱 입력) — 히어로명 기준 {role: {qty,by,at}}
qinputs = load_quantity_inputs(sheets)
print(f"1차수량 입력: {sum(len(v) for v in qinputs.values())}건 ({len(qinputs)} 히어로)")

# 히어로 등급(앱 '등급 설정' 기록) — {season: {정규화명: 그룹키}} (담당자 웹 기록 영속 반영)
grade_saved = load_grade_inputs(sheets)
print(f"등급 기록: {sum(len(v) for v in grade_saved.values())}건 ({len(grade_saved)} 시즌)")

# 상품 관리판(MSTRD 상품MAP) 링크 등록 — {season: {url,by,at}} (STEP1 완료 트리거, 앱 등록 영속 반영)
mstrd_reg = load_mstrd_inputs(sheets)
print(f"상품MAP 링크 등록: {len(mstrd_reg)} 시즌 {list(mstrd_reg)}")

# 등록된 MSTRD 파일에서 등급구분 표 파싱 → {season: {정규화명: 'S'|'A'|'E'}} (STEP2 등급 자동, 못읽으면 빈값→앱 알람)
mstrd_grades = {}
for _s, _rec in mstrd_reg.items():
    _g = parse_mstrd_grades(sheets, _rec.get("url", ""))
    if _g:
        mstrd_grades[_s] = _g
print(f"MSTRD 등급 파싱: {sum(len(v) for v in mstrd_grades.values())}건 ({list(mstrd_grades)})")

# PO수량(발주량) — MD투입 시트에서 스타일별 {po:{4채널,t}, colors:{...}} (타겟시즌=2026FW 필터)
try:
    from soo.hero_ops.po_ingest import parse_po_qty, CHANNELS as PO_CH
    po_qty = parse_po_qty(sheets, "2026FW")
    print(f"PO수량: {sum(1 for v in po_qty.values() if v['po']['t'])} 스타일 (2026FW)")
except Exception as e:
    po_qty, PO_CH = {}, ("dom_on", "dom_off", "chn_on", "chn_off")
    print(f"[주의] PO수량 주입 실패 — 빈 값 유지: {type(e).__name__}: {e}")

_QROLES = ("planning_md", "online_sales", "offline_sales")

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
            if (row["style"], n) in completions and not (actual and len(actual) == 10):
                sst.append("done"); sdt.append("완료 (앱 입력)"); continue
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

    # 1차수량(앱 입력) 주입 — 히어로명 기준, 역할별 수량 + 입력자/일시
    roles = qinputs.get(series, {})
    s5_inputs = {r: roles[r]["qty"] for r in _QROLES if r in roles}
    s5_meta = {r: {"by": roles[r]["by"], "at": roles[r]["at"]} for r in roles}

    # PO수량 주입 — 스타일별 {4채널,t, colors} + 히어로 합계 (내수온/내수오프/차이나온/차이나오프)
    po_q, po_tot = {}, {c: 0 for c in PO_CH}; po_tot["t"] = 0
    for s in styles:
        pv = po_qty.get(s)
        if not pv:
            continue
        po_q[s] = {**pv["po"], "colors": pv["colors"]}
        for k in po_tot:
            po_tot[k] += pv["po"].get(k, 0)

    heroes.append({
        "id": f"26FW_{i:03d}", "season": "26FW", "track": track,
        "name": series, "category": category,
        "ownerMD": owner_md, "ownerDesigner": owner_ds,
        "styles": styles,
        "stages": stages, "dates": dates,
        "stage5": {"tentativeColors": [], "inputs": s5_inputs, "meta": s5_meta,
                   "confirmed": {"online_sales": None, "offline_sales": None}, "completedAt": None},
        "stage8": {"sentAt": None, "poQuantities": po_q, "po": po_tot},
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
# 홈 화면 실적 카드 기준일(하드코딩 SALES_AS_OF)도 DASHBOARD.as_of와 동일하게 매일 갱신
html2, nsa = re.subn(r"const SALES_AS_OF = '[^']*';",
                     f"const SALES_AS_OF = '{TODAY.isoformat()}';", html2, count=1)

# ── 히어로 등급 기록 주입(담당자 웹 '등급 설정' → 시트 → 앱, 재생성해도 유지) ──
_grade_block = "const HERO_GRADE_SAVED = " + json.dumps(grade_saved, ensure_ascii=False) + ";"
html2, ng = re.subn(r"const HERO_GRADE_SAVED = \{.*?\};", lambda _m: _grade_block, html2, count=1, flags=re.DOTALL)
assert ng == 1, f"HERO_GRADE_SAVED 교체 실패 (matched {ng})"

# ── 상품 관리판(MSTRD) 링크 등록 주입(담당자 웹 STEP1 등록 → 시트 → 앱, 재생성해도 유지) ──
_mstrd_block = "const MSTRD_REGISTRY = " + json.dumps(mstrd_reg, ensure_ascii=False) + ";"
html2, nmr = re.subn(r"const MSTRD_REGISTRY = \{.*?\};", lambda _m: _mstrd_block, html2, count=1, flags=re.DOTALL)
assert nmr == 1, f"MSTRD_REGISTRY 교체 실패 (matched {nmr})"

# ── MSTRD 파싱 등급 주입(STEP2 자동 채움, 등록된 파일서 읽음) ──
_mstrd_grades_block = "const MSTRD_GRADES = " + json.dumps(mstrd_grades, ensure_ascii=False) + ";"
html2, nmg = re.subn(r"const MSTRD_GRADES = \{.*?\};", lambda _m: _mstrd_grades_block, html2, count=1, flags=re.DOTALL)
assert nmg == 1, f"MSTRD_GRADES 교체 실패 (matched {nmg})"

# ── 실적 대시보드 데이터 주입 (build_dashboard) ──
# 소스 시트: SALES_SHEET(Databricks 잡이 매일 07:00 KST에 채우는 전용 SA 시트). raw 탭만 사용.
# goods→hero 매핑은 build_maps 가 내부 DEV_SHEET_ID(26SS 탭)에서 별도로 읽음(sheet_id 무관).
nd = 0
_DASH_HEROES = []   # IMC 히어로 시즌 판정용(대시보드 STY→시즌 큐레이션값)
try:
    from soo.hero_ops.sales_rollup import build_dashboard, SALES_SHEET_ID, build_style_to_hero, read_tab
    # 홈 실적 = 시트39 확정 26SS 매핑(uid+신품번, 사용자 검증 524.5억=525.4). 성과 탭과 동일 히어로 정의.
    _map26 = json.load(open(ROOT / "hero_goods_26ss.json", encoding="utf-8"))
    _dash_s2h = _map26["style_to_hero"]
    dash = build_dashboard(sheets, drive, SALES_SHEET_ID, TODAY.isoformat(),
                           style2hero=_dash_s2h, goods2hero=_map26["goods_to_hero"])
    _DASH_HEROES = dash.get("heroes", [])
    dash_block = "const DASHBOARD = " + json.dumps(dash, ensure_ascii=False) + ";"
    html2, nd = re.subn(r"const DASHBOARD = \{.*?\};", dash_block, html2, count=1, flags=re.DOTALL)
    assert nd == 1, f"DASHBOARD 교체 실패 (matched {nd})"
    print(f"DASHBOARD: 히어로 {len(dash['heroes'])}개 주입 (매핑 {dash['_stats']['mapped']}/{dash['_stats']['rows']})")
    # 스타일명(발매센터·홈 26FW STY 드릴다운 표시용) — 26SS 시트39 품명 + 26FW MSTRD 품명(M열) 병합.
    #   26FW STY(양말 7팩·10팩 등)가 STY_NAMES에 없어 '기타'로 폴백되던 것 보강.
    _sty_names = dict(_map26.get("style_names", {}))
    try:
        _fw_snap = json.load(open(ROOT / "hero_goods_26fw.json", encoding="utf-8"))
        for _b, _s in _fw_snap.get("styles", {}).items():
            if _s.get("name") and _b not in _sty_names:
                _sty_names[_b] = _s["name"]
    except Exception:
        pass    # 26FW 스냅샷 없으면 26SS 품명만(첫 실행 등)
    sn_block = "const STY_NAMES = " + json.dumps(_sty_names, ensure_ascii=False) + ";"
    html2, nsn = re.subn(r"const STY_NAMES = \{.*?\};", lambda _m: sn_block, html2, count=1, flags=re.DOTALL)
    print(f"STY_NAMES 주입: {len(_sty_names)}개 (교체 {nsn})")
except Exception as e:
    print(f"[주의] DASHBOARD 주입 실패 — 실적 대시보드는 기존값 유지: {type(e).__name__}: {e}")

# ── 데이터 갱신 헬스체크 수집 (비어있음/구조변경 등 '조용한 실패' 가시화) ──
_HEALTH = []
SNS_SHEET_ID = "11f6JTGvms3uVcuVJW-M9Wa9-Lt4x3Tjn5IFJ2m8jifE"  # [무탠다드] SNS/CRM 콘텐츠 통합 관리
TRACKER_SHEET_ID = "1oz6zM-x2nqaDSAufWJ2a-QZh-1F6LQipttNkVKoFAn8"  # 캠페인 운영관리 트래커([히어로 PDP]에 운영 히어로 품목)
GOAL_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"  # PLM 시트(사용자 소유), '히어로 마케팅 목표' 탭=마케팅 입력란
GOAL_TAB = "히어로 마케팅 목표"
MKT_SHEET_ID = "16jqlhmynIxXckdrpjICaDNajZd-xjnrl0x332qDCtzg"  # 마케팅팀 MKT calendar (캠페인 레벨/진행상황·에너지/바이럴)


_TAB_TITLES = {}  # sid → 실제 탭 제목 목록(1회 조회 캐시)


def _sheet_tabs(sid):
    if sid not in _TAB_TITLES:
        try:
            meta = sheets.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties.title").execute()
            _TAB_TITLES[sid] = [s["properties"]["title"] for s in meta.get("sheets", [])]
        except Exception as _e:
            _HEALTH.append(f"탭 목록 조회 실패({type(_e).__name__}) — 권한 확인")
            _TAB_TITLES[sid] = []
    return _TAB_TITLES[sid]


def _match_tabs(key, sid=None):
    """제목에 key가 들어간 실제 탭 전부(정렬). 기간 분할 탭(예: 오피셜 IG (26.7~)/(~26.6)) 대응."""
    return sorted(t for t in _sheet_tabs(sid or SNS_SHEET_ID) if key in t)


def _resolve_tab(tab, sid=None):
    """논리 탭명 → 실제 탭명. 정확일치 없으면 접두일치로 해석(운영팀이 '(26.7~)' 같은 기간 접미사를
    붙여도 조용히 0건이 되지 않게). 후보 여러 개면 첫 번째 + 경고."""
    titles = _sheet_tabs(sid or SNS_SHEET_ID)
    if not titles or tab in titles:
        return tab
    cands = [t for t in titles if t.startswith(tab)]
    if not cands:
        return tab  # 아래 읽기에서 실패로 처리 → _HEALTH 경고
    if len(cands) > 1:
        _HEALTH.append(f"'{tab}' 후보 여러 개 {cands} → '{cands[0]}' 사용")
    else:
        _HEALTH.append(f"'{tab}' → '{cands[0]}'로 해석(탭 이름 변경 감지)")
    return cands[0]


def _sns_table(tab, keys, last_col="AB", max_row=900, scan=20, optional=(), sid=None):
    """탭을 읽어 (데이터행, {key: colidx}) 반환. keys={key:[헤더 별칭...]}.
    헤더행은 별칭 매칭 수가 가장 많은 행으로 자동 탐색 → 컬럼 이동/삽입·헤더행 위치 변경에 강건(#4).
    optional: 시트마다 있을 수도/없을 수도 있는 컬럼(없어도 경고 안 함). sid: 다른 스프레드시트도 가능."""
    tab = _resolve_tab(tab, sid)
    try:
        rows = sheets.spreadsheets().values().get(
            spreadsheetId=sid or SNS_SHEET_ID, range=f"'{tab}'!A1:{last_col}{max_row}",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    except Exception as _e:
        _HEALTH.append(f"'{tab}' 읽기 실패({type(_e).__name__}) — 권한/이름 확인")
        return [], {}
    best_i, best_hits = None, 0
    for i, r in enumerate(rows[:scan]):
        cells = [str(c or "") for c in r]
        hits = sum(1 for al in keys.values() if any(any(a in c for a in al) for c in cells))
        if hits > best_hits:
            best_hits, best_i = hits, i
    if best_i is None or best_hits < 2:
        _HEALTH.append(f"'{tab}' 헤더 인식 실패 — 시트 구조 변경 의심")
        return [], {}
    hdr = [str(c or "").strip() for c in rows[best_i]]
    cmap = {}
    for k, al in keys.items():
        for j, c in enumerate(hdr):
            if any(a in c for a in al):
                cmap[k] = j
                break
    missing = [k for k in keys if k not in cmap and k not in optional]
    if missing:
        _HEALTH.append(f"'{tab}' 컬럼 못 찾음: {missing}")
    return rows[best_i + 1:], cmap


def _gv(row, cmap, k):
    j = cmap.get(k)
    return str(row[j]).strip() if j is not None and j < len(row) and row[j] is not None else ""


# ── 무탠본부 아이템마스터 = 26FW 발매일자 진실소스 (IMC·발매센터 공용) ──
# '발매스케줄'(상품MAP)은 stale(리커버리 발주전·힛탠다드 등 지난날짜) → 기획MD팀이 실제
# 발매일 관리하는 '무탠' 탭 B열로 교정. 실패해도 발매스케줄 폴백(리커버리만 미노출로 회귀).
_SER_ALIAS = {"그리드/알파 플리스": "그리드/메시 플리스"}   # 무탠 레지스트리 표기 → 앱 표준명


def _ser_key(s):
    s = _SER_ALIAS.get(str(s or "").strip(), str(s or "").strip())
    return re.sub(r"\s+", "", s)


_MUTAN_REL = {"rep_first": {}, "heroes": {}}
try:
    from soo.hero_ops import imc_triggers as _IMCT0
    _MUTAN_REL = _IMCT0.load_mutan_release_dates(sheets)
    print(f"무탠 발매일자 로드: {len(_MUTAN_REL['heroes'])} 히어로 · 대표품번 {len(_MUTAN_REL['rep_first'])}건")
except Exception as e:
    print(f"[주의] 무탠 발매일자 로드 실패 — 발매스케줄 폴백: {type(e).__name__}: {e}")
_MUT_BY_KEY = {_ser_key(s): h for s, h in _MUTAN_REL.get("heroes", {}).items()}

# 26FW 히어로 스타일 진실소스(MSTRD 'HERO STY' B열=HERO/HERO SUB) — 발매 캘린더/실적 공통 기준.
#   ★발매 이벤트를 이 품번 집합으로 필터(사용자 지시): 무탠 히어로(26FW) 탭엔 스웨터·가방·코트·
#   데일리푸퍼·머플러·스웨트집업 등 15히어로 외 시리즈도 있어 발매에 새므로 HERO STY 품번만 남긴다.
#   hero_perf 블록에서 재사용(재로드 방지). 실패 시 필터 스킵(기존 동작=전체 주입).
_FW_HERO_MAP = None
_FW_STY_NUMS = None
try:
    _FW_HERO_MAP = _IMCT0.load_26fw_hero_goods(sheets)
    _FW_STY_NUMS = set(_FW_HERO_MAP["style_to_hero"].keys())
    print(f"HERO STY 발매 필터 기준: {len(_FW_STY_NUMS)} 품번 (15 시리즈)")
except Exception as e:
    print(f"[주의] HERO STY 로드 실패 — 발매 시리즈 필터 스킵: {type(e).__name__}: {e}")


# ── IMC 통합(과거·현재·미래) 주입 → const IMC ──
# 소스: 발매/캠페인/오프라인/발매이슈/기획전(imc_triggers, 별도 파일) + SNS/CRM 콘텐츠 통합 관리 시트
#       (온사이트/PR/IG광고). 각 액션에 status(past/today/future)·channel 부여. 윈도우 TODAY-365~+150.
# 슬랙 알람(imc_triggers)은 온라인MD용이라 GRADES 셋 다 유지, 앱은 발매를 HERO·HERO SUB만(핵심상품 제외).
nimc = 0
try:
    import datetime as _dt
    import re as _re2
    from soo.hero_ops import imc_triggers as _IMCT
    _back = (TODAY - _dt.timedelta(days=365)).isoformat()
    _fwd = (TODAY + _dt.timedelta(days=150)).isoformat()
    _items = []

    def _clean(v):   # 셀 내부 줄바꿈/탭/연속공백 → 단일 공백(셀 멀티라인 값이 JSON·표시 깨는 것 방지)
        return _re2.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v

    def _add(type_, channel, date_, title, sub="", owner="", **extra):
        title = _clean(str(title or ""))
        if not date_ or not title:
            return False
        d = {"type": type_, "channel": channel, "date": date_, "title": title[:60],
             "sub": _clean(sub), "owner": _clean(owner)}
        d.update({k: _clean(v) for k, v in extra.items()})
        _items.append(d)
        return True

    # 1) 발매 이벤트 = 무탠본부 아이템마스터 진실소스 단독(대표품번=STY 단위, 정확한 발매일).
    #    ★발매스케줄(상품MAP)은 stale STY(슬랙스 옛 20FW품번을 26FW신규로 오기 등)가 섞여 있어 폐기.
    #    무탠 26FW 발매일 있는 히어로 STY만(등급 HERO/HERO SUB) 정확일자로 매핑.
    _rel_skip = 0
    for _ser, _h in _MUTAN_REL.get("heroes", {}).items():
        for _e in _h.get("events", []):
            # ★HERO STY 품번(B열 HERO/HERO SUB)만 발매 대상. 대표품번이 그 집합에 없으면 제외
            #   (스웨터·가방·코트·데일리푸퍼·머플러·스웨트집업 = 무탠 등록됐으나 앱 히어로 아님).
            if _FW_STY_NUMS is not None and _e["style"] not in _FW_STY_NUMS:
                _rel_skip += 1
                continue
            _add("발매", "발매", _e["release"].isoformat(), _e["name"], f"{_ser}/{_e.get('grade', 'HERO')}")
    if _rel_skip:
        print(f"발매 필터: HERO STY 외 {_rel_skip}건 제외")
    # (캠페인/오프라인/발매이슈/기획전은 기존 소스 유지)
    for c in _IMCT.load_campaigns(sheets):
        _add("캠페인", "캠페인", c["start"].isoformat(), c["name"], c["gubun"], c["owner"])
    for g in _IMCT.load_offline_gates(sheets):
        _add("오프라인", "오프라인", g["date"].isoformat(), g["label"], g["kind"], season_gate=g["season_gate"])
    # 오프라인 전개 플랜 본문(히어로별 조닝 전개 + 브랜드협업/IP) — 게이트 외 실제 '전개' 내용
    for it in _IMCT.load_offline_rollout(sheets):
        _add("오프라인", "오프라인", it["date"].isoformat(), it["title"], it["sub"],
             it.get("owner", ""), approx=it.get("approx", False))
    for it in _IMCT.load_release_issues(sheets):
        _add("입고알람", "입고알람", it["when"].isoformat(), it["issue"], it["brand"], it["owner"])
    for p in _IMCT.load_general_promos(sheets):
        _add("기획전", "기획전", p["start"].isoformat(), p["title"], "", p["owner"])
    # 온라인 캠페인 스케줄('[통합] 26년 프로모션 스케줄') — 월별 SUMMARY 상세(1~7월, 자가확장) + 연간 백본(8~12월)
    _n_on = 0
    try:
        for it in _IMCT.load_online(sheets):
            if _add("온라인", "온라인", it["date"].isoformat(), it["name"], it["sub"],
                    approx=it.get("approx", False),
                    end=(it["end"].isoformat() if it.get("end") else ""), guide=it.get("guide", "")):
                _n_on += 1
        print(f"IMC 온라인 캠페인 로드: {_n_on}건")
        if _n_on == 0:
            _HEALTH.append("온라인 캠페인 0건 — 시트 권한/구조 확인")
    except Exception as _e_on:
        _HEALTH.append(f"온라인 캠페인 로드 예외: {type(_e_on).__name__}")
        print(f"[주의] 온라인 캠페인 로드 실패(기존 소스 유지): {type(_e_on).__name__}: {_e_on}")

    # 2) SNS/CRM 브랜드 콘텐츠 통합 관리 시트 (별개 파일) — 온사이트/PR/IG광고. 헤더명 기반 파싱(#4).
    def _date_ymd(s):       # "2025/9/4" · "2025.09.04"
        m = _re2.findall(r"\d+", str(s or ""))
        if len(m) >= 3 and 2024 <= int(m[0]) <= 2027:
            try:
                return _dt.date(int(m[0]), int(m[1]), int(m[2])).isoformat()
            except ValueError:
                return None
        return None

    def _date_yymmdd(s):    # "260611"
        s = _re2.sub(r"\D", "", str(s or ""))
        if len(s) == 6:
            try:
                return _dt.date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6])).isoformat()
            except ValueError:
                return None
        return None

    try:
        n_os = n_pr = n_ig = 0
        rows, cm = _sns_table("5)온사이트", {"date": ["발행일"], "type": ["유형"], "title": ["타이틀", "제목"]})
        for r in rows:
            n_os += _add("온사이트", "온사이트", _date_ymd(_gv(r, cm, "date")), _gv(r, cm, "title"), _gv(r, cm, "type"), "권정은")
        rows, cm = _sns_table("6)PR", {"owner": ["요청자"], "date": ["발행 일자", "발행일자", "발행 일"], "type": ["유형"], "title": ["타이틀", "제목"]})
        for r in rows:
            n_pr += _add("PR", "PR", _date_ymd(_gv(r, cm, "date")), _gv(r, cm, "title"), _gv(r, cm, "type"), _gv(r, cm, "owner"))
        rows, cm = _sns_table("4)인스타그램 게시물 광고",
                              {"start": ["광고시작", "시작일"], "title": ["세트명", "광고 세트", "세트"],
                               "acct": ["게재 계정", "계정"], "form": ["유형"], "req": ["요청자"]})
        for r in rows:
            t = _gv(r, cm, "title")
            if not t or "예산" in t or "총 금액" in t or _gv(r, cm, "start").lower().startswith("ex"):
                continue
            n_ig += _add("SNS", "SNS광고", _date_yymmdd(_gv(r, cm, "start")), t,
                         "/".join(x for x in [_gv(r, cm, "acct"), _gv(r, cm, "form")] if x), _gv(r, cm, "req"))
        for nm, cnt in [("온사이트", n_os), ("PR", n_pr), ("IG광고", n_ig)]:
            if cnt == 0:
                _HEALTH.append(f"SNS/CRM {nm} 0건 — 윈도우 밖이거나 파싱 실패")
        print(f"IMC SNS/CRM 콘텐츠 로드: 온사이트 {n_os}·PR {n_pr}·IG광고 {n_ig}")
    except Exception as e2:
        _HEALTH.append(f"SNS/CRM 콘텐츠 로드 예외: {type(e2).__name__}")
        print(f"[주의] SNS/CRM 콘텐츠 로드 실패(기존 소스만 유지): {type(e2).__name__}: {e2}")

    # 2.5) SNS/CRM 마스터 캘린더 '2)일정' — 주(週)밴드 × 가로 13개월(9월~익년9월) 그리드.
    #   레이아웃: C열=채널(병합셀, 아래로 carry-forward) · 날짜밴드행(요일별 M/D, ≥5개)이 컬럼→날짜 정의.
    #   소셜 실행 레이어(촬영/IG오피셜/IG글로벌)+포워드 CRM(앱푸시/인앱/카카오)만 추출.
    #   이슈행(공통캠페인/기획전/오프라인/글로벌/PRODUCT/PR/매거진)은 imc_triggers·6)PR 권위소스와
    #   중복이라 제외. 셀의 'OOO_' 프리픽스=포맷(피드/릴스/스토리), '히어로_'=SNS팀 정답 히어로태그.
    try:
        _CH_MAP = {  # C열 채널값 → (IMC type, sub). 여기 없는 채널은 스킵. (촬영은 마케팅 내부용이라 제외)
            "IG_OFFICIAL": ("IG", "오피셜"),
            "IG_GLOBAL": ("IG", "글로벌"), "CRM": ("CRM", "CRM"),
            "인앱메시지": ("CRM", "인앱"), "KKO": ("CRM", "카카오"),
        }

        def _is_date_cell(s):
            return bool(_re2.fullmatch(r"\d{1,2}/\d{1,2}", str(s or "").strip()))

        _svals = sheets.spreadsheets().values().get(
            spreadsheetId=SNS_SHEET_ID, range="'2)일정'!A1:DB200",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        _col2date, _cur_C, _seen_m = {}, None, set()
        _cnt = {"IG": 0, "촬영": 0, "CRM": 0}
        for _row in _svals:
            _dh = [(j, str(c).strip()) for j, c in enumerate(_row) if _is_date_cell(c)]
            if len(_dh) >= 5:  # 날짜밴드행 → 컬럼→날짜 재설정(좌→우 월 감소 시 연도+1: 9~12=2025, 1~9=2026)
                _col2date, _cur_C, _yr, _pmo = {}, None, 2025, 0
                for j, md in _dh:
                    _mo, _da = (int(x) for x in md.split("/"))
                    if _pmo and _mo < _pmo:
                        _yr += 1
                    _pmo = _mo
                    try:
                        _col2date[j] = _dt.date(_yr, _mo, _da).isoformat()
                    except ValueError:
                        pass
                continue
            _c2 = str(_row[2]).strip() if len(_row) > 2 and _row[2] else ""
            if _c2:
                _cur_C = _c2
            _tt = _CH_MAP.get(_cur_C)
            if not _tt or not _col2date:
                continue
            _type_, _sub_ = _tt
            for j, _cell in enumerate(_row):
                if j < 3 or j not in _col2date:
                    continue
                _ttl = str(_cell or "").strip()
                if not _ttl or _is_date_cell(_ttl):
                    continue
                _k = (_col2date[j], _type_, _re2.sub(r"\s+", "", _ttl))
                if _k in _seen_m:
                    continue
                _seen_m.add(_k)
                if _add(_type_, _type_, _col2date[j], _ttl, _sub_, source="일정"):
                    _cnt[_type_] += 1
        if sum(_cnt.values()) == 0:
            _HEALTH.append("2)일정 마스터 0건 — 구조변경/권한 확인")
        print(f"IMC 마스터일정(2)일정) 로드: IG {_cnt['IG']}·촬영 {_cnt['촬영']}·CRM {_cnt['CRM']}")
    except Exception as _es:
        _HEALTH.append(f"2)일정 마스터 로드 예외: {type(_es).__name__}")
        print(f"[주의] 2)일정 마스터 로드 실패(기존 소스만 유지): {type(_es).__name__}: {_es}")

    # 2.7) 마케팅팀 MKT calendar (별개 파일) — ①캠페인 레벨(S/A/B)·진행상황 ②에너지/바이럴 액션.
    #   레벨/진행상황은 '26년 캠페인 통합 관리' 표(월별·릴리즈/촬영 일자), 에너지/바이럴은 메인 그리드 레인.
    #   기존 캠페인/기획전 항목과 제목 매칭되면 레벨/상태만 보강(중복 추가 방지), 아니면 신규 추가.
    def _mkt(rng):
        try:
            return sheets.spreadsheets().values().get(
                spreadsheetId=MKT_SHEET_ID, range=rng,
                valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        except Exception:
            return []

    def _mdate(s):       # "2026. 1. 14." / "2026-02-24" → iso
        m = _re2.findall(r"\d+", str(s or ""))
        if len(m) >= 3 and 2025 <= int(m[0]) <= 2027:
            try:
                return _dt.date(int(m[0]), int(m[1]), int(m[2])).isoformat()
            except ValueError:
                return None
        return None

    try:
        _norm = lambda s: _re2.sub(r"\s+", "", str(s or ""))
        # ① 캠페인 통합 관리 → 레벨/진행상황(+담당·일자). 헤더: 월|구분|주요이슈|...|레벨|마케팅|...|진행상황|촬영타겟일|에셋전달일|릴리즈일자
        _mc = _mkt("'26년 캠페인 통합 관리 시트'!A23:AQ90")
        _mhi = next((i for i, r in enumerate(_mc) if any("주요 이슈" in str(c) for c in r)), -1)
        _n_camp = _n_enrich = _n_plan = 0
        if _mhi >= 0:
            _mh = _mc[_mhi]

            def _mcol(*names):
                return next((j for j, c in enumerate(_mh) if any(n in str(c) for n in names)), None)

            # '콘텐츠'·'마케팅'은 담당(I/H)이 기획안(N/M)보다 앞이라 첫 매칭=담당. '주력'=E IMC 주력 상품.
            _C = {k: _mcol(*v) for k, v in {"month": ["월"], "gubun": ["구분"], "issue": ["주요 이슈"],
                  "prod": ["주력"], "lvl": ["레벨"], "mkt": ["마케팅"], "cont": ["콘텐츠"], "photo": ["포토"],
                  "status": ["진행 상황"], "shoot": ["촬영 타겟"], "rel": ["릴리즈"]}.items()}

            def _gc(r, k):
                j = _C.get(k)
                return str(r[j]).strip() if j is not None and j < len(r) and r[j] is not None else ""

            _exist = [x for x in _items if x["type"] in ("캠페인", "기획전")]
            _cur_m = 0
            for r in _mc[_mhi + 1:]:
                _mm = _re2.match(r"(\d{1,2})", _gc(r, "month"))
                if _mm:
                    _cur_m = int(_mm.group(1))
                _issue = _gc(r, "issue")
                if not _issue or not _cur_m:
                    continue
                _lvl = _gc(r, "lvl").upper()[:1]
                _lvl = _lvl if _lvl in ("S", "A", "B") else ""
                _mst = _gc(r, "status")
                if "기획" in _mst:   # '기획중'=아직 확정 안 된 내부 기획 업무 → IMC에 반영하지 않음(신규추가·보강 모두 스킵)
                    _n_plan += 1
                    continue
                _prod = _gc(r, "prod")   # E IMC 주력 상품(겨냥 히어로/상품)
                _owners = " · ".join(f"{_role} {_nm}" for _role, _nm in
                                     [("마케팅", _gc(r, "mkt")), ("콘텐츠", _gc(r, "cont")), ("포토", _gc(r, "photo"))]
                                     if _nm)
                _date = _mdate(_gc(r, "rel")) or _mdate(_gc(r, "shoot"))
                _approx = not _date
                if _approx:
                    try:
                        _date = _dt.date(2026, _cur_m, 1).isoformat()
                    except ValueError:
                        continue
                # 기존 캠페인/기획전과 제목 매칭(정규화 포함관계, 짧은 쪽 ≥4자)되면 보강
                _ni = _norm(_issue)
                _hit = next((x for x in _exist if len(min(_ni, _norm(x["title"]), key=len)) >= 4
                             and (_ni in _norm(x["title"]) or _norm(x["title"]) in _ni)), None)
                if _hit:
                    if _lvl:
                        _hit["level"] = _lvl
                    if _mst:
                        _hit["mstatus"] = _mst
                    if _prod:
                        _hit["prod"] = _prod
                    if _owners:
                        _hit["owners"] = _owners
                    _n_enrich += 1
                    continue
                if _add("캠페인", "캠페인", _date, _issue, _gc(r, "gubun"), _gc(r, "mkt"),
                        level=_lvl, mstatus=_mst, prod=_prod, owners=_owners,
                        source="MKT", approx=_approx):
                    _n_camp += 1

        # ② 메인 캘린더 그리드 에너지/바이럴 레인 (R3 월·R4 일자 → 컬럼별 날짜 매핑, 2026년)
        _grid = _mkt("'26년 MKT 캘린더'!A1:NZ70")
        _n_energy = 0
        if _grid:
            _dri = max(range(min(8, len(_grid))),
                       key=lambda i: sum(1 for c in _grid[i] if str(c).strip().isdigit()))
            _drow = _grid[_dri]
            _mrow = _grid[_dri - 1] if _dri > 0 else []
            _c2d, _cm2 = {}, None
            for j in range(len(_drow)):
                _ml = str(_mrow[j]).strip() if j < len(_mrow) else ""
                _mq = _re2.match(r"(\d{1,2})\s*월", _ml)
                if _mq:
                    _cm2 = int(_mq.group(1))
                _dv = str(_drow[j]).strip()
                if _cm2 and _dv.isdigit() and 1 <= int(_dv) <= 31:
                    try:
                        _c2d[j] = _dt.date(2026, _cm2, int(_dv)).isoformat()
                    except ValueError:
                        pass
            _ELANES = {"릴스": "릴스·리포터즈", "리포터즈": "릴스·리포터즈", "큐레이터": "큐레이터·스냅",
                       "스냅": "큐레이터·스냅", "인플루언서": "인플루언서", "유튜버": "인플루언서", "바이럴": "바이럴"}
            for r in _grid:
                _lab = (str(r[0]).strip() if len(r) > 0 and r[0] else "") + " " \
                       + (str(r[1]).strip() if len(r) > 1 and r[1] else "")
                _lane = next((v for k, v in _ELANES.items() if k in _lab), None)
                if not _lane:
                    continue
                for j, _iso in _c2d.items():
                    _v = str(r[j]).strip() if j < len(r) and r[j] is not None else ""
                    if len(_v) > 2:
                        if _add("에너지", "에너지", _iso, _v, _lane, source="MKT"):
                            _n_energy += 1
        print(f"IMC MKT calendar 로드: 캠페인 신규 {_n_camp}·보강 {_n_enrich}건 + 에너지/바이럴 {_n_energy}건 (기획중 {_n_plan}건 제외)")
        if _n_camp == 0 and _n_enrich == 0 and _n_energy == 0:
            _HEALTH.append("MKT calendar 0건 — 구조변경/권한 확인")
    except Exception as _emkt:
        _HEALTH.append(f"MKT calendar 로드 예외: {type(_emkt).__name__}")
        print(f"[주의] MKT calendar 로드 실패(기존 소스만 유지): {type(_emkt).__name__}: {_emkt}")

    # 3) 히어로 별칭 자동생성(#4) + 각 항목 hero_related 태깅
    #    판별: 발매(정의상 히어로) / 제목에 '히어로' 명시(2)일정의 '히어로_' 프리픽스 등) / 26FW 제품명 키워드.
    #    까다로운 품목만 수동 override, 나머지는 히어로명에서 자동 생성 → 품목 바뀌면 자동 반영.
    _ALIAS_OVERRIDE = {
        "커브드팬츠": ["커브드팬츠", "커브드 팬츠", "커브드 데님"],
        "그리드/메시 플리스": ["그리드", "메시 플리스", "플리스"],
        "에센셜 플리스": ["에센셜 플리스", "플리스"],
        "심리스 브라": ["심리스 브라", "심리스브라"],
        "라이트다운": ["라이트다운", "라이트 다운"],
        "헤비다운": ["헤비다운", "헤비 다운"],
    }
    _hero_alias = {}
    for _h in heroes:
        _nm = _h["name"]
        _hero_alias[_nm] = _ALIAS_OVERRIDE.get(_nm) or sorted({_nm, _nm.replace(" ", "")}, key=len, reverse=True)

    # 현재 운영 중인 히어로 품목 — 캠페인 운영관리 트래커 [히어로 PDP]의 정답 레지스트리에서.
    # 앱 26FW 기획 히어로 ∪ 현재 운영 히어로 = "히어로" 정의(합집합). 마케팅 가시성 목적.
    _cur_heroes = []
    try:
        _rows, _cm = _sns_table("[히어로 PDP]", {"item": ["HERO 품목"], "brand": ["브랜드"], "sty": ["STY_No"]},
                                sid=TRACKER_SHEET_ID)
        _seen = set()
        for _r in _rows:
            _it = _gv(_r, _cm, "item")
            if _it and not _it.startswith("무신사 스탠다드") and _it not in _seen:
                _seen.add(_it)
                _cur_heroes.append(_it)
        if not _cur_heroes:
            _HEALTH.append("캠페인 트래커 [히어로 PDP] 품목 0건 — 권한/구조 확인")
    except Exception as _e:
        _HEALTH.append(f"캠페인 트래커 히어로 로드 실패: {type(_e).__name__}")

    # 히어로 라인업(히어로별 뷰·매칭용) = 대시보드 히어로(시즌有, 쿨탠다드·슬랙스·버뮤다 등) ∪ 26FW 기획 히어로.
    #   별칭=풀네임+공백제거+한글 첫토큰+짧은형(쿨탠/힛탠). 'NEW' 등 비한글 토큰은 제외(오탐 방지).
    _SHORT = {"쿨탠다드": ["쿨탠"], "힛탠다드": ["힛탠"]}

    # first_token: 첫 단어를 별칭으로 추가할지. 대시보드 히어로(쿨탠다드·슬랙스 등 첫단어 고유)만 True.
    #   26FW 히어로는 첫 단어가 일반 수식어("에센셜 플리스"의 에센셜·"웜 팬츠"의 웜)라 오탐 → False(큐레이션만).
    def _mk_aliases(name, base=(), first_token=True):
        al = {name, name.replace(" ", "")} | set(base)
        toks = name.split()
        first = toks[0] if toks else ""
        if first_token and len(first) >= 2 and _re2.search(r"[가-힣]", first):
            al.add(first)
        for _kk, _ss in _SHORT.items():   # 쿨탠/힛탠 짧은형은 first_token 무관하게 이름에 키 있으면 추가
            if _kk in name.replace(" ", ""):
                al.update(_ss)
        return [a for a in sorted(al, key=len, reverse=True) if len(a.replace(" ", "")) >= 2]

    _lineup = {}  # 정규화명 → {name, aliases, season}
    for _dh in _DASH_HEROES:
        _dn = _dh.get("name", "")
        if _dn:
            _lineup[_dn.replace(" ", "")] = {"name": _dn, "aliases": _mk_aliases(_dn), "season": _dh.get("season", "")}
    for _hn, _al in _hero_alias.items():
        _k = _hn.replace(" ", "")
        if _k not in _lineup:
            _lineup[_k] = {"name": _hn, "aliases": _mk_aliases(_hn, _al, first_token=False), "season": "26FW"}
    hero_lineup = list(_lineup.values())

    # 매칭 키워드 = 라인업 별칭 ∪ 현재 운영 히어로 품목 (공백 제거 정규화)
    _alias_norm = {a.replace(" ", "") for h in hero_lineup for a in h["aliases"]}
    _alias_norm |= {h.replace(" ", "") for h in _cur_heroes if len(h.replace(" ", "")) >= 2}
    _alias_norm = list(_alias_norm)

    def _hero_related(it):
        if it["channel"] == "발매":
            return True
        blob = (it["title"] + " " + it.get("sub", "") + " " + it.get("prod", "")).replace(" ", "")
        if "히어로" in blob:
            return True
        return any(a in blob for a in _alias_norm)

    for x in _items:
        x["hero_related"] = _hero_related(x)

    # 4) 윈도우 필터 + status 부여
    #    ⚠ 예전엔 비히어로 일정(source="일정")을 영구 드롭 → 봄 히어로 시즌 종료 후 5/6월 비히어로
    #    활동(여름상품·매장)이 통째로 사라져 '마케팅이 멈춘 듯' 보임. 이제 전량 유지하고
    #    '히어로 관련만' 토글로만 가림(실제 활동 가시화). 비히어로 노이즈는 토글 ON이 기본이라 평소엔 숨김.
    _t = TODAY.isoformat()
    _n_master_raw = sum(1 for x in _items if x.get("source") == "일정")
    _n_master_hero = sum(1 for x in _items if x.get("source") == "일정" and x["hero_related"])
    _items = sorted((x for x in _items if _back <= x["date"] <= _fwd), key=lambda x: x["date"])
    print(f"2)일정 마스터: {_n_master_raw}건(히어로 {_n_master_hero}) 전량 유지 — 토글로 가림")
    for x in _items:
        x["status"] = "past" if x["date"] < _t else ("today" if x["date"] == _t else "future")
    imc_block = "const IMC = " + json.dumps({"as_of": _t, "items": _items}, ensure_ascii=False) + ";"
    # 람다 치환 — 치환문자열의 \n·\g 등 백슬래시 이스케이프 처리 방지(값에 \ 남아도 안전)
    html2, nimc = re.subn(r"const IMC = \{.*?\};", lambda _m: imc_block, html2, count=1, flags=re.DOTALL)
    assert nimc == 1, f"IMC 교체 실패 (matched {nimc})"
    _np = sum(1 for x in _items if x["status"] == "past")
    _nh = sum(1 for x in _items if x["hero_related"])
    print(f"IMC 주입: {len(_items)}건 (과거 {_np}/미래 {len(_items) - _np} · 히어로관련 {_nh}/{len(_items)} · 운영히어로 {len(_cur_heroes)}종: {_cur_heroes})")

    _alias_block = "const HERO_IMC_ALIASES = " + json.dumps(_hero_alias, ensure_ascii=False) + ";"
    html2, _na = re.subn(r"const HERO_IMC_ALIASES = \{.*?\};", _alias_block, html2, count=1, flags=re.DOTALL)
    if _na != 1:
        _HEALTH.append("HERO_IMC_ALIASES 교체 실패(앱 플레이스홀더 확인)")
    else:
        print(f"히어로 IMC 별칭 주입: {len(_hero_alias)}개")

    # 히어로별 뷰용 라인업(이름/별칭/시즌) 주입
    _lineup_block = "const HERO_LINEUP = " + json.dumps(hero_lineup, ensure_ascii=False) + ";"
    html2, _nl = re.subn(r"const HERO_LINEUP = \[.*?\];", lambda _m: _lineup_block, html2, count=1, flags=re.DOTALL)
    if _nl != 1:
        _HEALTH.append("HERO_LINEUP 교체 실패(앱 플레이스홀더 확인)")
    else:
        print(f"히어로 라인업 주입: {len(hero_lineup)}종")
except Exception as e:
    _HEALTH.append(f"IMC 주입 예외: {type(e).__name__}")
    print(f"[주의] IMC 주입 실패 — 기존값 유지: {type(e).__name__}: {e}")

# ── IMC 채널별 성과(과거 회고) 주입 → const IMC_PERF ──
# SNS/CRM 통합 관리 시트의 성과 탭(4-1/4-2 IG, 시트16 CRM) + 예산 탭 집계.
nperf = 0
try:
    import re as _re3

    def _n(s):
        d = _re3.sub(r"[^\d]", "", str(s or ""))
        return int(d) if d else 0

    def _agg_ig(key, ch):  # 헤더명 기반(#4)
        # ★운영팀이 성과 탭을 기간별로 쪼갬(오피셜 IG = '(26.7~)' + '(~26.6)') → 제목에 key가 든 탭
        #   전부 합산. 앞으로 '(26.10~)'이 더 생겨도 자동 편입. 두 탭에 같은 게시물이 겹쳐 있어
        #   (발행일+소재)로 중복 제거.
        tabs = _match_tabs(key)
        if not tabs:
            _HEALTH.append(f"성과 탭 '{key}' 없음 — 시트 탭 이름 확인")
        agg = {"posts": 0, "views": 0, "reach": 0, "likes": 0, "hero": 0, "popular": 0}
        tops = []
        # ★중복 제거는 '탭 간'만. 한 탭 안의 같은 (발행일+소재)는 서로 다른 게시물일 수 있어
        #   (우먼 탭은 유형 컬럼이 없어 피드/릴스 구분 불가) 건드리지 않는다.
        seen_prev, dupes = set(), 0
        for tab in tabs:
            cur = set()
            # 우먼(4-2)은 유형·인기게시물·히어로콘텐츠 컬럼이 없음 → optional 처리(오탐 방지)
            rows, cm = _sns_table(tab, {"date": ["발행일"], "title": ["소재"], "form": ["유형"],
                                        "views": ["조회"], "reach": ["도달"], "likes": ["좋아요"],
                                        "popular": ["인기게시물", "인기 게시물"], "hero": ["히어로콘텐츠", "히어로 콘텐츠"]},
                                  optional=("form", "popular", "hero"))
            for r in rows:
                title, v = _gv(r, cm, "title"), _n(_gv(r, cm, "views"))
                if not title or (v == 0 and _n(_gv(r, cm, "reach")) == 0):
                    continue
                sig = (_gv(r, cm, "date"), title)
                if sig in seen_prev:  # 앞선 탭에 이미 있는 게시물(기간 분할 경계 중복)
                    dupes += 1
                    continue
                cur.add(sig)
                agg["posts"] += 1
                agg["views"] += v
                agg["reach"] += _n(_gv(r, cm, "reach"))
                agg["likes"] += _n(_gv(r, cm, "likes"))
                if _gv(r, cm, "popular").upper() == "O":
                    agg["popular"] += 1
                if _gv(r, cm, "hero").upper() == "O":
                    agg["hero"] += 1
                    tops.append({"ch": ch, "title": title[:40], "date": _gv(r, cm, "date"), "views": v, "type": _gv(r, cm, "form")})
            seen_prev |= cur
        print(f"성과 '{ch} IG': 탭 {tabs} → {agg['posts']}건(탭간 중복 {dupes} 제외)")
        if agg["posts"] == 0:
            _HEALTH.append(f"성과 '{key}' 0건")
        return agg, tops

    agg_off, tops_off = _agg_ig("성과_오피셜 IG", "오피셜")
    agg_wm, tops_wm = _agg_ig("성과_우먼 IG", "우먼")

    # CRM(시트16): 채널/발송수/GMV/ROAS (헤더명 기반)
    crm = {"count": 0, "sends": 0, "gmv": 0, "roas": 0}
    _ro_sum = _ro_n = 0
    rows, cm = _sns_table("시트16", {"ch": ["채널"], "sends": ["발송수"], "gmv": ["GMV"], "roas": ["ROAS"]})
    for r in rows:
        g = _n(_gv(r, cm, "gmv"))
        if g == 0:
            continue
        crm["count"] += 1
        crm["gmv"] += g
        crm["sends"] += _n(_gv(r, cm, "sends"))
        try:
            _ro_sum += float(_gv(r, cm, "roas").replace("%", "").replace(",", "")); _ro_n += 1
        except ValueError:
            pass
    crm["roas"] = round(_ro_sum / _ro_n) if _ro_n else 0
    if crm["count"] == 0:
        _HEALTH.append("CRM(시트16) 성과 0건")

    # 예산(PMKT/CRM 예산): 구분 라벨 행 × 월 컬럼 (헤더명 기반)
    _mlbl = ["2026/01", "2026/02", "2026/03", "2026/04", "2026/05", "2026/06"]
    _mkey = ["m1", "m2", "m3", "m4", "m5", "m6"]
    budget = {"months": _mlbl, "hero": [], "perf": []}
    _bkeys = {"gubun": ["구분"]}
    _bkeys.update({k: [lbl] for k, lbl in zip(_mkey, _mlbl)})
    rows, cm = _sns_table("PMKT/CRM 예산", _bkeys, last_col="P", max_row=40)
    _hrow = next((r for r in rows if _gv(r, cm, "gubun") == "Hero"), None)
    _prow = next((r for r in rows if "퍼포먼스" in _gv(r, cm, "gubun")), None)
    for k in _mkey:
        budget["hero"].append(_n(_gv(_hrow, cm, k)) if _hrow else 0)
        budget["perf"].append(_n(_gv(_prow, cm, k)) if _prow else 0)
    if not _hrow:
        _HEALTH.append("예산 Hero 행 못 찾음")

    highlights = sorted(tops_off + tops_wm, key=lambda x: -x["views"])[:10]

    # 시트 읽기 헬퍼(_raw/_g2/_hdr_idx) — 아래 '히어로 마케팅 목표' 로드 등에서 사용.
    # ★히어로별 PMKT 성과는 더 이상 캠페인 트래커가 아니라 Databricks 'PMKT주차'/'PMKT경로'(team.sales.pdp_path_daily_summary_v 기반)에서 로드(하단 hero_perf 블록).
    def _raw(tab, sid, last_col="BZ", max_row=400):
        try:
            return sheets.spreadsheets().values().get(
                spreadsheetId=sid, range=f"'{tab}'!A1:{last_col}{max_row}",
                valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        except Exception:
            return []

    def _g2(r, j):
        return str(r[j]).strip() if 0 <= j < len(r) and r[j] is not None else ""

    def _hdr_idx(rows, must):
        for i, r in enumerate(rows[:25]):
            cells = [str(c or "") for c in r]
            if all(any(k in c for c in cells) for k in must):
                return i
        return -1

    def _cols_with(hdr, kw):
        return [j for j, c in enumerate(hdr) if kw in str(c or "")]

    hero_perf = {}
    _PERIODS = ["YTD", "MTD", "WEEK"]   # PMKT기간 탭의 period 값. 프론트 토글과 1:1.

    def _num(v):   # read_tab은 UNFORMATTED_VALUE라 숫자를 실제 number로 줌 → float 직접(★_n은 float ".0"를 ×10로 깨뜨림)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    try:
        # ★26SS 히어로 매핑 — 시트39(gid1392316906) 확정 매핑(uid+신품번, 사용자 검증 524.5억=525.4).
        #   style_to_hero(행별 신품번→hero) + goods_to_hero(uid 폴백: 신품번 빈칸/누락 goods). 26FW는 파일 교체.
        _sty_map = json.load(open(ROOT / "hero_goods_26ss.json", encoding="utf-8"))
        _s2h = _sty_map["style_to_hero"]
        _g2h = _sty_map.get("goods_to_hero", {})
        _HERO_SEASON = _sty_map.get("season", "26SS")

        def _hero_of(style, goods=None):
            h = _s2h.get(str(style or "").split("-")[0].strip())
            if not h and goods is not None:          # 신품번 빈칸/누락 → uid 폴백
                try: h = _g2h.get(str(int(goods)))
                except (TypeError, ValueError): h = None
            return h

        # ★26FW 히어로 매핑(MSTRD 'HERO STY' B열=HERO/HERO SUB 진실소스) — 26SS와 별도.
        #   26SS·26FW는 히어로 이름이 겹치지만(커브드 SS 7STY vs FW 14STY 등) 스타일 구성이
        #   달라 이름 조인이 불가 → 26FW 실적은 이 매핑으로 따로 롤업(hero_perf_fw).
        _fw2h_sty, _fw2h_goods, _fw2sty = {}, {}, {}
        try:
            from soo.hero_ops.imc_triggers import load_26fw_hero_goods
            _fwm = _FW_HERO_MAP if _FW_HERO_MAP is not None else load_26fw_hero_goods(sheets)   # 발매필터서 이미 로드했으면 재사용
            _fw2h_sty, _fw2h_goods, _fw2sty = _fwm["style_to_hero"], _fwm["goods_to_hero"], _fwm["goods_to_style"]
            json.dump({k: _fwm[k] for k in ("season", "style_to_hero", "goods_to_hero", "goods_to_style", "styles")},
                      open(ROOT / "hero_goods_26fw.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            _nou = sum(1 for s in _fwm["styles"].values() if s["uid_src"] == "없음")
            print(f"26FW 히어로 매핑(MSTRD HERO STY): 스타일 {len(_fw2h_sty)} · uid {len(_fw2h_goods)} · uid미생성 {_nou}")
        except Exception as _efw:      # 라이브 읽기 실패 → 스냅샷 폴백(조용한 0 방지)
            _HEALTH.append(f"26FW 히어로 매핑 로드 실패({type(_efw).__name__}) — 스냅샷 사용")
            print(f"[주의] 26FW 매핑 라이브 실패 — 스냅샷 폴백: {type(_efw).__name__}: {_efw}")
            try:
                _snap = json.load(open(ROOT / "hero_goods_26fw.json", encoding="utf-8"))
                _fw2h_sty, _fw2h_goods = _snap["style_to_hero"], _snap["goods_to_hero"]
                _fw2sty = _snap.get("goods_to_style", {})
            except Exception:
                _HEALTH.append("26FW 히어로 매핑 스냅샷도 없음 — 26FW 실적 0")

        def _hero_of_fw(style, goods=None):
            h = _fw2h_sty.get(str(style or "").split("-")[0].strip())
            if not h and goods is not None:
                try: h = _fw2h_goods.get(str(int(goods)))
                except (TypeError, ValueError): h = None
            return h

        hero_perf_fw = {}
        # 26FW STY별 실적(홈 26FW 행 펼침 드릴다운용) — hero → {품번: {기간: {gmv, qty}}}
        #   ★기존 프론트는 DASHBOARD(26SS)에서 이름으로 stys를 끌어와 26SS 스타일을 보여줬다
        #     (벨트 26FW 합계 7.8억인데 하위 STY가 10.4억, 양말에 26SS 전용 FMASC101 노출).
        hero_sty_fw = {}

        def _styd_fw(hero, base, per):
            return hero_sty_fw.setdefault(hero, {}).setdefault(base, {}).setdefault(per, {"gmv": 0, "qty": 0})

        def _base_of(style, goods):
            # ★MSTRD 등록 품번을 우선. 매출 시트의 style_no는 '리뉴얼 이전품번'인 경우가 있어
            #   그대로 쓰면 같은 상품이 옛 품번으로 갈라진다(양말 FMASC101 = MEASC0Z70의 구 품번,
            #   같은 uid가 양쪽에 등록됨 → 품명 동일 '라이트웨이트 크루 삭스 1팩').
            try:
                b = _fw2sty.get(str(int(goods)), "")
                if b:
                    return b
            except (TypeError, ValueError):
                pass
            return str(style or "").split("-")[0].strip()

        def _perd_fw(hero, per):
            P = hero_perf_fw.setdefault(hero, {"periods": {}, "season": "26FW"})
            return P["periods"].setdefault(per, {"gmv": 0, "qty": 0, "gmv_ly": 0,
                                                 "pmkt_gmv": 0, "pdp_real": 0, "conv": 0,
                                                 "ad_gmv": 0, "pdp_ad": 0})

        def _perd(hero, per):
            P = hero_perf.setdefault(hero, {"periods": {}, "season": _HERO_SEASON})
            # _ly = 전년 동기간(YoY 분모). gmv_ly=실적누판 전년(전년매출탭), 나머지=PMKT기간 *_ly.
            #   ad_gmv_ly/pdp_ad_ly = 마케팅기여·유입기여 YoY 분자(전년) — DBX 노트북 mkt_*_ly 백필 후 채워짐(없으면 0).
            return P["periods"].setdefault(per, {"gmv": 0, "gmv_ly": 0, "pmkt_gmv": 0, "pdp_real": 0, "conv": 0,
                                                 "ad_gmv": 0, "pdp_ad": 0,
                                                 "pdp_real_ly": 0, "conv_ly": 0, "pmkt_gmv_ly": 0,
                                                 "ad_gmv_ly": 0, "pdp_ad_ly": 0})

        # 히어로 STY별 성과(드릴다운) — style_no(품번) 단위 PMKT direct 유입·전환·거래액.
        #   hero → {품번: {기간: {pdp, buy, gmv, pdp_ly, buy_ly, name}}}
        hero_sty = {}

        def _hsty(hero, base, per):
            return hero_sty.setdefault(hero, {}).setdefault(base, {}).setdefault(
                per, {"pdp": 0, "buy": 0, "gmv": 0, "pdp_ly": 0, "buy_ly": 0, "gmv_ly": 0,
                      "mkt_gmv": 0, "mkt_pdp": 0, "mkt_gmv_ly": 0, "mkt_pdp_ly": 0})

        # (1a) 성과 GMV = 실적 누판(gmv=실판매가) — 매출 YTD/MTD/WEEK 탭을 신품번→히어로로 롤업.
        #      PMKT의 gmv는 직접경로 어트리뷰션이라 실적보다 작음 → 헤드라인 GMV엔 누판을 씀.
        for _per in _PERIODS:
            for r in read_tab(sheets, SALES_SHEET_ID, _per):
                hero = _hero_of(r.get("style_no"), r.get("goods_no"))
                if hero:
                    _perd(hero, _per)["gmv"] += round(_num(r.get("gmv")))
                hero_fw = _hero_of_fw(r.get("style_no"), r.get("goods_no"))   # 26FW 기준 별도 롤업
                if hero_fw:
                    _dfw = _perd_fw(hero_fw, _per)
                    _g0, _q0 = round(_num(r.get("gmv"))), round(_num(r.get("qty")))
                    _dfw["gmv"] += _g0
                    _dfw["qty"] += _q0
                    _b = _base_of(r.get("style_no"), r.get("goods_no"))
                    if _b:
                        _sd0 = _styd_fw(hero_fw, _b, _per)
                        _sd0["gmv"] += _g0
                        _sd0["qty"] += _q0
        # 26FW 전년 동기간(YoY 분모) — 전년YTD/전년MTD/전년WEEK 탭을 26FW 매핑으로 롤업.
        #   ★프론트가 26SS DASHBOARD를 이름으로 조인해 쓰지 않도록 26FW 자체 기간·전년을 갖춘다.
        for _per in _PERIODS:
            try:
                for r in read_tab(sheets, SALES_SHEET_ID, "전년" + _per):
                    hero_fw = _hero_of_fw(r.get("style_no"), r.get("goods_no"))
                    if hero_fw:
                        _perd_fw(hero_fw, _per)["gmv_ly"] += round(_num(r.get("gmv")))
                    hero = _hero_of(r.get("style_no"), r.get("goods_no"))   # 26SS 성과탭 거래액 YoY
                    if hero:
                        _perd(hero, _per)["gmv_ly"] += round(_num(r.get("gmv")))
            except Exception as _ely:
                _HEALTH.append(f"전년{_per} 로드 실패({type(_ely).__name__}) — 거래액 YoY 미표시")
        # (1b) PMKT기간 — 퍼널 지표(전환=buy_uv/pdp_uv · 마케팅기여=mkt_gmv/mkt_pdp_uv, 캠페인기획전+외부유입)
        #      + 마케팅기여율 분모용 pmkt_gmv(직접경로 GMV). 헤드라인 GMV는 위 누판을 쓰므로 여기 gmv는 pmkt_gmv로만.
        for r in read_tab(sheets, SALES_SHEET_ID, "PMKT기간"):
            per = str(r.get("period") or "").strip()
            hero_fw = _hero_of_fw(r.get("style_no"), r.get("goods_no"))   # 26FW 기준 퍼널 지표
            if hero_fw and per in _PERIODS:
                dfw = _perd_fw(hero_fw, per)
                dfw["pmkt_gmv"] += round(_num(r.get("gmv")))
                dfw["pdp_real"] += round(_num(r.get("pdp_uv")))
                dfw["conv"] += round(_num(r.get("buy_uv")))
                dfw["ad_gmv"] += round(_num(r.get("mkt_gmv")))
                dfw["pdp_ad"] += round(_num(r.get("mkt_pdp_uv")))
            hero = _hero_of(r.get("style_no"))
            if not hero or per not in _PERIODS:
                continue
            d = _perd(hero, per)
            d["pmkt_gmv"] += round(_num(r.get("gmv")))
            d["pdp_real"] += round(_num(r.get("pdp_uv")))
            d["conv"] += round(_num(r.get("buy_uv")))
            d["ad_gmv"] += round(_num(r.get("mkt_gmv")))
            d["pdp_ad"] += round(_num(r.get("mkt_pdp_uv")))
            # 전년 동기간(YoY) — PMKT기간 셀의 *_ly 컬럼(같은 goods 전년 날짜). 신규 goods는 전년 0 → 프론트서 null 처리.
            d["pdp_real_ly"] += round(_num(r.get("pdp_uv_ly")))
            d["conv_ly"] += round(_num(r.get("buy_uv_ly")))
            d["pmkt_gmv_ly"] += round(_num(r.get("gmv_ly")))
            # 마케팅기여·유입기여 YoY 분자(전년) — mkt_gmv_ly/mkt_pdp_uv_ly. DBX 백필 전엔 컬럼 없음→0.
            d["ad_gmv_ly"] += round(_num(r.get("mkt_gmv_ly")))
            d["pdp_ad_ly"] += round(_num(r.get("mkt_pdp_uv_ly")))
            # STY(품번)별 드릴다운 — 유입(pdp)·구매전환(buy)·거래액(direct), 전년(YoY)
            _sb = str(r.get("style_no") or "").split("-")[0].strip()
            if _sb:
                s = _hsty(hero, _sb, per)
                s["pdp"] += round(_num(r.get("pdp_uv")))
                s["buy"] += round(_num(r.get("buy_uv")))
                s["gmv"] += round(_num(r.get("gmv")))
                s["pdp_ly"] += round(_num(r.get("pdp_uv_ly")))
                s["buy_ly"] += round(_num(r.get("buy_uv_ly")))
                s["gmv_ly"] += round(_num(r.get("gmv_ly")))   # direct GMV 전년(마케팅기여 YoY 분모)
                # 마케팅기여(mkt_gmv/gmv)·유입기여(mkt_pdp/pdp) — 히어로와 동일 소스, 품번단위. *_ly는 백필 전 0.
                s["mkt_gmv"] += round(_num(r.get("mkt_gmv")))
                s["mkt_pdp"] += round(_num(r.get("mkt_pdp_uv")))
                s["mkt_gmv_ly"] += round(_num(r.get("mkt_gmv_ly")))
                s["mkt_pdp_ly"] += round(_num(r.get("mkt_pdp_uv_ly")))
        # (2) PMKT주차 — goods×ISO주차 → 히어로별 최근 2주(WoW). 스파크라인 폐기(가시성↓, 사용자 요청).
        #   WoW = 최근 완료주 vs 직전주. pdp(유입)·buy(구매UV)·gmv(direct 거래액). 전환율 WoW는 프론트서 buy/pdp.
        _wk_keys, _hero_wk, _wk_label, _wk_span, _sty_wk = set(), {}, {}, {}, {}
        for r in read_tab(sheets, SALES_SHEET_ID, "PMKT주차"):
            hero = _hero_of(r.get("style_no"), r.get("goods_no"))
            if not hero:
                continue
            try:
                _key = (int(_num(r.get("yyyy"))), int(_num(r.get("week_no"))))
            except (TypeError, ValueError):
                continue
            _wk_keys.add(_key)
            W = _hero_wk.setdefault(hero, {}).setdefault(_key, {"gmv": 0, "pdp": 0, "buy": 0, "mkt_gmv": 0, "mkt_pdp": 0})
            W["gmv"] += round(_num(r.get("gmv")))
            W["pdp"] += round(_num(r.get("pdp_uv")))
            W["buy"] += round(_num(r.get("buy_uv")))
            W["mkt_gmv"] += round(_num(r.get("mkt_gmv")))     # 마케팅기여 WoW 분자
            W["mkt_pdp"] += round(_num(r.get("mkt_pdp_uv")))  # 유입기여 WoW 분자
            # STY(품번) 단위 주차 롤업 — STY 드릴다운 전주비(PDP·전환·마케팅기여·유입기여)용
            _swb = str(r.get("style_no") or "").split("-")[0].strip()
            if _swb:
                SW = _sty_wk.setdefault(hero, {}).setdefault(_swb, {}).setdefault(_key, {"pdp": 0, "buy": 0, "gmv": 0, "mkt_gmv": 0, "mkt_pdp": 0})
                SW["pdp"] += round(_num(r.get("pdp_uv")))
                SW["buy"] += round(_num(r.get("buy_uv")))
                SW["gmv"] += round(_num(r.get("gmv")))
                SW["mkt_gmv"] += round(_num(r.get("mkt_gmv")))
                SW["mkt_pdp"] += round(_num(r.get("mkt_pdp_uv")))
            _wk_label.setdefault(_key, str(r.get("week_start") or "")[5:].replace("-", "/"))
            # 주 일수(span) — 소스 주 경계가 불규칙(W29=1일, W28=5일 등, 데이터 경계로 잘림).
            #   진행중(1일짜리) 주는 WoW에서 제외하고, 남은 주는 '일평균'으로 정규화해 공정 비교.
            if _key not in _wk_span:
                try:
                    _ws2 = datetime.date.fromisoformat(str(r.get("week_start"))[:10])
                    _we2 = datetime.date.fromisoformat(str(r.get("week_end"))[:10])
                    _wk_span[_key] = (_we2 - _ws2).days + 1
                except (ValueError, TypeError):
                    _wk_span[_key] = 7
        # 진행중 주(span<2=사실상 1일) 제외 → 남은 최근 2주. 볼륨은 일평균(÷span)으로 비교.
        _usable = [k for k in sorted(_wk_keys) if _wk_span.get(k, 7) >= 2]
        _cur_k = _usable[-1] if _usable else None
        _prev_k = _usable[-2] if len(_usable) >= 2 else None
        _cd = _wk_span.get(_cur_k, 7) or 7
        _pd = _wk_span.get(_prev_k, 7) or 7
        for hero, P in hero_perf.items():
            _hw = _hero_wk.get(hero, {})
            _c = _hw.get(_cur_k, {}) if _cur_k else {}
            _p = _hw.get(_prev_k, {}) if _prev_k else {}
            # 볼륨(pdp/buy/gmv)은 일평균으로 저장 → 프론트 비율계산이 곧 일평균 WoW.
            #   전환율 WoW는 buy/pdp라 정규화 무관(같은 span으로 약분).
            P["wow"] = {
                "cur_w": f"W{_cur_k[1]}" if _cur_k else "", "prev_w": f"W{_prev_k[1]}" if _prev_k else "",
                "cur_from": _wk_label.get(_cur_k, ""), "prev_from": _wk_label.get(_prev_k, ""),
                "pdp": round(_c.get("pdp", 0) / _cd), "pdp_p": round(_p.get("pdp", 0) / _pd),
                "buy": round(_c.get("buy", 0) / _cd), "buy_p": round(_p.get("buy", 0) / _pd),
                "gmv": round(_c.get("gmv", 0) / _cd), "gmv_p": round(_p.get("gmv", 0) / _pd),
                # 마케팅기여 WoW = (mkt_gmv/gmv) · 유입기여 WoW = (mkt_pdp/pdp) — 프론트서 비율 계산(정규화 무관)
                "mkt_gmv": round(_c.get("mkt_gmv", 0) / _cd), "mkt_gmv_p": round(_p.get("mkt_gmv", 0) / _pd),
                "mkt_pdp": round(_c.get("mkt_pdp", 0) / _cd), "mkt_pdp_p": round(_p.get("mkt_pdp", 0) / _pd),
            }
        # STY 드릴다운 배열을 각 히어로 P에 주입(유입순 상위, 잡음 제거 위해 pdp>0만)
        for hero, P in hero_perf.items():
            _stys = []
            _swh = _sty_wk.get(hero, {})
            for _b, _pers in hero_sty.get(hero, {}).items():
                _y = _pers.get("YTD", {})
                if (_y.get("pdp", 0) or 0) <= 0:
                    continue
                # STY 전주비 — 히어로와 동일하게 최근 완료주 vs 직전주(일평균 정규화). PDP·전환·마케팅기여·유입기여.
                _swc = (_swh.get(_b, {}).get(_cur_k, {}) if _cur_k else {})
                _swp = (_swh.get(_b, {}).get(_prev_k, {}) if _prev_k else {})
                _stys.append({"style": _b,
                    "wow": {"pdp": round(_swc.get("pdp", 0) / _cd), "pdp_p": round(_swp.get("pdp", 0) / _pd),
                            "buy": round(_swc.get("buy", 0) / _cd), "buy_p": round(_swp.get("buy", 0) / _pd),
                            "gmv": round(_swc.get("gmv", 0) / _cd), "gmv_p": round(_swp.get("gmv", 0) / _pd),
                            "mkt_gmv": round(_swc.get("mkt_gmv", 0) / _cd), "mkt_gmv_p": round(_swp.get("mkt_gmv", 0) / _pd),
                            "mkt_pdp": round(_swc.get("mkt_pdp", 0) / _cd), "mkt_pdp_p": round(_swp.get("mkt_pdp", 0) / _pd)},
                    "periods": {
                    _pp: {"pdp": (_pers.get(_pp) or {}).get("pdp", 0), "buy": (_pers.get(_pp) or {}).get("buy", 0),
                          "gmv": (_pers.get(_pp) or {}).get("gmv", 0),
                          "pdp_ly": (_pers.get(_pp) or {}).get("pdp_ly", 0), "buy_ly": (_pers.get(_pp) or {}).get("buy_ly", 0),
                          "gmv_ly": (_pers.get(_pp) or {}).get("gmv_ly", 0),
                          "mkt_gmv": (_pers.get(_pp) or {}).get("mkt_gmv", 0), "mkt_pdp": (_pers.get(_pp) or {}).get("mkt_pdp", 0),
                          "mkt_gmv_ly": (_pers.get(_pp) or {}).get("mkt_gmv_ly", 0), "mkt_pdp_ly": (_pers.get(_pp) or {}).get("mkt_pdp_ly", 0)}
                    for _pp in _PERIODS}})
            _stys.sort(key=lambda x: -x["periods"]["YTD"]["pdp"])
            P["stys"] = _stys
        _wk_labels = []
        _pdp_wk_labels = []
    except Exception as _eh:
        _HEALTH.append(f"히어로 PMKT 성과 로드 실패: {type(_eh).__name__}")

    # 히어로 마케팅 목표(사람이 입력) — PLM 시트의 '히어로 마케팅 목표' 탭. 비면 목표 미설정.
    # 목표를 자동 산출하지 않음(억지 목표 금지). 마케팅팀이 입력하면 앱에 달성율 자동 표시.
    _goals = {}
    try:
        _gr = _raw(GOAL_TAB, GOAL_SHEET_ID, last_col="E", max_row=60)
        _ghi = _hdr_idx(_gr, ["히어로 품목", "목표 GMV"])
        if _ghi >= 0:
            _gh = _gr[_ghi]
            _gpj = next((j for j, c in enumerate(_gh) if "품목" in str(c)), 0)
            _ggj = next((j for j, c in enumerate(_gh) if "목표 GMV" in str(c)), 1)
            _grj = next((j for j, c in enumerate(_gh) if "ROAS" in str(c)), None)
            for r in _gr[_ghi + 1:]:
                _gn = _g2(r, _gpj)
                if not _gn:
                    continue
                _goals[_gn] = {"gmv": _n(_g2(r, _ggj)),
                               "roas": _g2(r, _grj) if _grj is not None else ""}
    except Exception as _eg:
        _HEALTH.append(f"히어로 마케팅 목표 로드 실패: {type(_eg).__name__}")

    # 히어로 시즌 — 26SS 스냅샷(hero_sty_26ss.json)의 season을 전 히어로에 부여(위 _perd에서 P["season"]=_HERO_SEASON).
    #   26FW 전환 시엔 스냅샷 파일만 교체(season·style_to_hero).
    hero_list = sorted(
        [{"name": k, "periods": v.get("periods", {}),
          "wow": v.get("wow", {}), "stys": v.get("stys", []),
          "season": v.get("season", ""),
          "goal": _goals.get(k, {}).get("gmv", 0),
          "goal_roas": _goals.get(k, {}).get("roas", "")} for k, v in hero_perf.items()],
        key=lambda x: -(x["periods"].get("YTD", {}).get("gmv", 0)))
    if not hero_list:
        _HEALTH.append("히어로 PMKT 성과 0건 — 트래커 구조 확인")
    print("IMC 히어로 시즌: " + ", ".join(f"{h['name']}={h['season']}" for h in hero_list))

    # ★조용한 0 덮어쓰기 방지(2026-07-15). 소스 탭 읽기가 실패하면(이름 변경·권한·일시 오류) 집계가
    #   0으로 나오는데 그대로 주입하면 라이브 실데이터가 지워진다 — 실제로 오피셜 IG 탭이
    #   '(26.7~)'로 개명되며 posts 374→0·reach 11.6M→0으로 매일 CI가 덮어썼다.
    #   0건이면 앱 HTML에 이미 있는 직전 값을 보존한다(다음 정상 실행 때 자동 복구).
    _mprev = re.search(r"const IMC_PERF = (\{.*?\});", html2, re.DOTALL)
    try:
        _prev = json.loads(_mprev.group(1)) if _mprev else {}
    except Exception:
        _prev = {}

    for _ch, _agg in (("오피셜", agg_off), ("우먼", agg_wm)):
        if _agg["posts"] == 0:
            _old = ((_prev.get("ig") or {}).get(_ch)) or {}
            if _old.get("posts"):
                _agg.update(_old)
                _HEALTH.append(f"성과 '{_ch} IG' 0건 → 기존값 보존({_old['posts']}건)")
                print(f"[보존] '{_ch} IG' 읽기 0건 — 앱 기존값 유지({_old['posts']}건)")
    if crm["count"] == 0 and (_prev.get("crm") or {}).get("count"):
        crm = dict(_prev["crm"])
        _HEALTH.append(f"CRM 성과 0건 → 기존값 보존({crm['count']}건)")
        print(f"[보존] CRM 읽기 0건 — 앱 기존값 유지({crm['count']}건)")

    perf = {"ig": {"오피셜": agg_off, "우먼": agg_wm}, "crm": crm, "budget": budget,
            "highlights": highlights, "hero": hero_list}
    perf_block = "const IMC_PERF = " + json.dumps(perf, ensure_ascii=False) + ";"
    html2, nperf = re.subn(r"const IMC_PERF = \{.*?\};", perf_block, html2, count=1, flags=re.DOTALL)
    assert nperf == 1, f"IMC_PERF 교체 실패 (matched {nperf})"
    print(f"IMC_PERF 주입: 오피셜 {agg_off['posts']}·우먼 {agg_wm['posts']} · CRM {crm['count']} · 히어로PMKT {len(hero_list)}종")
except Exception as e:
    _HEALTH.append(f"IMC_PERF 주입 예외: {type(e).__name__}")
    print(f"[주의] IMC_PERF 주입 실패 — 기존값 유지: {type(e).__name__}: {e}")

# ── 데이터 갱신 헬스체크 (조용한 실패 가시화: #3 권한 / #4 구조변경) ──
# 핵심 데이터가 비었으면 경고. CI에서 SLACK_BOT_TOKEN 있으면 슬랙 DM으로도 통지.
try:
    if _HEALTH:
        print("\n[HEALTHCHECK] 경고 " + str(len(_HEALTH)) + "건:")
        for w in _HEALTH:
            print("  - " + w)
        if os.environ.get("SLACK_BOT_TOKEN"):
            try:
                from soo.hero_ops import notify as _notify
                _notify.send("⚠️ 히어로 앱 데이터 갱신 경고 (" + TODAY.isoformat() + ")\n- " + "\n- ".join(_HEALTH[:12]))
            except Exception:
                pass
    else:
        print("\n[HEALTHCHECK] 정상 (모든 IMC 소스 로드됨)")
except Exception:
    pass

# ── 27SS 진척 카드 주입 (기획 관리판 #.상세일정 → SEASON_27SS_PROGRESS) ──
# 품평회 일자는 소스에 없어 제외, GO-DROP을 앵커로. 봄=G·여름=J(좌측 블록). 트랙별 D-day 자동.
n27 = 0
try:
    from soo.hero_ops.baseline_ingest import parse_mdp_date, SEASON_MDP_MAP
    sm27 = SEASON_MDP_MAP["27SS"]
    # (단계, 라벨, MDP 행, {트랙: 열}) — 킥오프는 공통 단일, 나머지는 봄/여름
    CARD_STAGES = [
        (1, "킥오프",       122, {"공통": "G"}),
        (2, "매트릭스 합의", 124, {"봄": "G", "여름": "J"}),
        (3, "GO-DROP",     129, {"봄": "G", "여름": "J"}),
        (4, "Initial PO",  138, {"봄": "G", "여름": "J"}),
    ]
    ranges = [f"'{sm27.tab}'!{col}{row}"
              for _, _, row, cols in CARD_STAGES for col in set(cols.values())]
    resp = sheets.spreadsheets().values().batchGet(
        spreadsheetId=sm27.spreadsheet_id, ranges=ranges).execute()
    cmap = {}
    for vr in resp.get("valueRanges", []):
        a1 = vr["range"].split("!")[-1]
        vals = vr.get("values", [])
        cmap[a1] = vals[0][0] if vals and vals[0] else ""

    def _mk_track(track, col, row):
        d = parse_mdp_date(cmap.get(f"{col}{row}", ""), sm27.year)
        if not d:
            return None
        md = f"{d.month}/{d.day}"
        delta = (d - TODAY).days
        if delta < 0:
            status, msg = "done", f"✓ 완료 ({md})"
        elif delta == 0:
            status, msg = "imminent", f"D-DAY ({md})"
        elif delta <= 7:
            status, msg = "imminent", f"D-{delta} ({md})"
        else:
            status, msg = "upcoming", f"D-{delta} ({md})"
        return {"track": track, "status": status, "date": d.isoformat(), "msg": msg}

    prog = []
    for stage, label, row, cols in CARD_STAGES:
        tracks = [t for t in (_mk_track(tk, col, row) for tk, col in cols.items()) if t]
        if tracks:
            prog.append({"stage": stage, "label": label, "tracks": tracks})
    if prog:
        blk = "const SEASON_27SS_PROGRESS = " + json.dumps(prog, ensure_ascii=False, indent=2) + ";"
        html2, n27 = re.subn(r"const SEASON_27SS_PROGRESS = \[.*?\n\];", blk, html2, count=1, flags=re.DOTALL)
        assert n27 == 1, f"SEASON_27SS_PROGRESS 교체 실패 (matched {n27})"
        print(f"27SS 진척: {len(prog)}단계 주입 (트랙 {sum(len(p['tracks']) for p in prog)})")
except Exception as e:
    print(f"[주의] 27SS 진척 주입 실패 — 기존값 유지: {type(e).__name__}: {e}")

# ── 26FW 발매센터 데이터 주입 (const LAUNCH_26FW) ──
# 준비(상품기획 14단계 완료율=heroes) + 발매(★MSTRD_26FW 상품MAP 발매스케줄 품번→시리즈→발매일)
#   + 판매(IMC_PERF 현재 누판 YTD, 이름정규화 조인). 상태=발매일 vs TODAY 자동전환.
nlaunch = 0
try:
    _26FW_MAP_ID = "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"   # ★MSTRD_26FW 상품MAP
    _FW_GRADE = {"라이트다운": "S", "힛탠다드": "S", "커브드팬츠": "S",
                 "웜 팬츠": "A", "빅토리아 울": "A", "그리드/메시 플리스": "A", "에센셜 플리스": "A", "리커버리": "A",
                 "헤비다운": "E", "슬랙스": "E", "데님팬츠": "E", "스웨트팬츠": "E", "벨트": "E", "양말": "E", "심리스 브라": "E"}
    _FW_ALIAS = {"그리드/알파 플리스": "그리드/메시 플리스"}   # 발매스케줄 표기 → 표준 히어로명
    _FW_MD_PLANNING = {"리커버리"}                          # 발매일 미정 중 MD 기획진행(사용자 명시); 그 외 무일정=캐리오버
    def _fw_norm(s): return re.sub(r"\s+", "", str(s or ""))
    def _fw_date(s):
        m = re.match(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", str(s or ""))
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None

    _sch = sheets.spreadsheets().values().get(
        spreadsheetId=_26FW_MAP_ID, range="'발매스케줄'!A9:R600").execute().get("values", [])
    _fw_agg = {n: {"dates": [], "new": 0, "carry": 0, "styles": set()} for n in _FW_GRADE}
    for r in _sch:
        if len(r) < 15:
            continue
        ser = _FW_ALIAS.get(str(r[4]).strip(), str(r[4]).strip())
        if ser not in _FW_GRADE:
            continue
        a = _fw_agg[ser]; season = str(r[6]).strip(); nc = str(r[5]).strip(); d = _fw_date(r[14])
        if season == "26FW":
            a["styles"].add(str(r[3]).strip())
            if "신규" in nc: a["new"] += 1
            elif "캐리오버" in nc: a["carry"] += 1
            if d: a["dates"].append(d)

    # 준비 완료율 (heroes 매트릭스, 이름정규화 조인)
    _prep = {_fw_norm(h["name"]): h for h in heroes}
    # 판매 = 26FW 히어로 스타일(MSTRD 'HERO STY' B열=HERO/HERO SUB) 기준 누판 롤업.
    # ★사용자 확정(2026-07-15): 26FW 라이브 실적은 MSTRD 26FW 스타일만 집계(26SS 전용 STY 제외).
    #   기존엔 26SS 기준 hero_list를 히어로 '이름'으로 조인했는데, 26SS·26FW는 이름이 겹쳐도
    #   STY 구성이 달라(커브드=SS 7STY/FW 14STY, 공통 3) 완전히 틀린 값이었다:
    #   640 정답 uid 중 178개(28%)만 잡히고 8종은 실적 0(이름 미존재), 캐리오버는 26SS 매출까지 과다.
    _perf_fw = globals().get("hero_perf_fw", {}) or {}
    _sales = {_fw_norm(k): v for k, v in _perf_fw.items()}

    _fw_list = []
    for name, grade in _FW_GRADE.items():
        a = _fw_agg[name]
        # 발매일 = 무탠 진실소스 단독. ★발매스케줄 폴백 폐기 — 슬랙스처럼 무탠에 26FW 신규
        # 발매 STY가 없는 히어로는 발매스케줄이 옛 품번을 신규로 오기한 stale 날짜(7/29)를
        # 물고 있어 캘린더와 어긋남 → 무탠 무일정=캐리오버로 통일(잘못된 데이터 제거).
        _mh = _MUT_BY_KEY.get(_ser_key(name))
        _mut_dates = _mh["dates"] if (_mh and _mh.get("dates")) else []
        fw = list(_mut_dates)
        first = fw[0] if fw else None
        if not first:
            status = "MD기획중" if name in _FW_MD_PLANNING else "캐리오버"
        else:
            dd = (first - TODAY).days
            status = "판매중" if dd <= 0 else ("임박" if dd <= 21 else "준비")
        # SKU 카운트도 무탠 기준(발매 STY 없으면 0 — 캐리오버 히어로는 신규 SKU 없음)
        if _mut_dates:
            _sku_new, _sku_carry, _style_cnt = _mh["new"], _mh["carry"], len(_mh["reps"])
        else:
            _sku_new, _sku_carry, _style_cnt = 0, 0, 0
        h = _prep.get(_fw_norm(name))
        prep_done = sum(1 for s in h["stages"] if s == "done") if h else 0
        prep_prog = sum(1 for s in h["stages"] if s == "progress") if h else 0
        prep_total = len(h["stages"]) if h else 14
        sp = _sales.get(_fw_norm(name))
        sales = None
        if sp:
            _y = (sp.get("periods") or {}).get("YTD") or {}
            _g = _y.get("gmv") or 0
            if _g:
                _pdp = _y.get("pdp_real") or 0        # PDP 조회 UV
                _buy = _y.get("conv") or 0            # 구매 UV
                _pmkt = _y.get("pmkt_gmv") or 0       # 직접경로 거래액(마케팅기여 분모)
                # 기간별(주간/당월/누계) gmv·수량·전년비 — 전부 26FW 스타일 기준.
                #   프론트 홈 26FW 컬럼이 이걸 그대로 씀(26SS 이름 조인 폐기).
                _pp = {}
                for _p in _PERIODS:
                    _d = (sp.get("periods") or {}).get(_p) or {}
                    _ly = _d.get("gmv_ly") or 0
                    _pp[_p.lower()] = {"gmv": _d.get("gmv", 0), "qty": _d.get("qty", 0),
                                       "yoy": ((_d.get("gmv", 0) - _ly) / _ly) if _ly else None}
                # STY 드릴다운(26FW 스타일만) — 프론트가 DASHBOARD(26SS) stys를 안 쓰게.
                _st = []
                for _b, _pers in (globals().get("hero_sty_fw", {}) or {}).get(name, {}).items():
                    _st.append({"style": _b,
                                "periods": {p.lower(): {"gmv": (_pers.get(p) or {}).get("gmv", 0),
                                                        "qty": (_pers.get(p) or {}).get("qty", 0)}
                                            for p in _PERIODS}})
                _st.sort(key=lambda x: -x["periods"]["ytd"]["gmv"])
                sales = {"gmv": _g, "periods": _pp, "stys": _st,
                         # 전환율 = 구매UV/PDP조회UV (실적·퍼널 정의 통일)
                         "conv": round(_buy / _pdp * 100, 1) if _pdp else None,
                         # 마케팅기여 = 마케팅 유입(캠페인/기획전+외부) 거래액 / PMKT 직접경로 거래액
                         "mkt": round(_y.get("ad_gmv", 0) / _pmkt * 100) if _pmkt else None}
        _fw_list.append({
            "name": name, "grade": grade, "status": status,
            "launch": first.isoformat() if first else None,
            "launch_last": fw[-1].isoformat() if fw else None,
            "dday": (first - TODAY).days if first else None,
            "sku_new": _sku_new, "sku_carry": _sku_carry, "style_count": _style_cnt,
            "prep_done": prep_done, "prep_prog": prep_prog, "prep_total": prep_total,
            "prep_pct": round(prep_done / prep_total * 100) if prep_total else 0,
            "sales": sales,
        })
    # 정렬: 발매일 asc(무일정 뒤) → 무일정은 MD기획중 먼저 → 등급
    _grk = {"S": 0, "A": 1, "E": 2}
    _fw_list.sort(key=lambda x: (x["launch"] or "9999", 0 if x["status"] == "MD기획중" else 1, _grk.get(x["grade"], 9)))
    launch_obj = {"as_of": TODAY.isoformat(), "heroes": _fw_list}
    launch_block = "const LAUNCH_26FW = " + json.dumps(launch_obj, ensure_ascii=False) + ";"
    html2, nlaunch = re.subn(r"const LAUNCH_26FW = \{.*?\};", lambda _m: launch_block, html2, count=1, flags=re.DOTALL)
    _nsold = sum(1 for x in _fw_list if x["sales"])
    print(f"LAUNCH_26FW 주입: {len(_fw_list)}종 (판매중 {sum(x['status']=='판매중' for x in _fw_list)}·임박 {sum(x['status']=='임박' for x in _fw_list)}·준비 {sum(x['status']=='준비' for x in _fw_list)}·MD기획중 {sum(x['status']=='MD기획중' for x in _fw_list)}·캐리오버 {sum(x['status']=='캐리오버' for x in _fw_list)}, 누판연동 {_nsold}종)")
    if nlaunch != 1:
        _HEALTH.append("LAUNCH_26FW 교체 실패(앱 플레이스홀더 확인)")
except Exception as e:
    print(f"[주의] LAUNCH_26FW 주입 실패 — 기존값 유지: {type(e).__name__}: {e}")

# ── 26FW 입고 보드 데이터 주입 (const INBOUND_BOARD) ──
# 예정=생산관리 탭(AK/AL), 실적=시트 실입고(AO/AP). 히어로 15종, SKU(품번-컬러) 단위.
ninb = 0
try:
    from soo.hero_ops.inbound_board import build_inbound_board, load_dbx_actuals
    _lm = {}
    try:
        for x in _fw_list:   # LAUNCH_26FW 히어로 메타 재사용(발매일/상태)
            _lm[x["name"]] = {"launch": x.get("launch"), "status": x.get("status")}
    except Exception:
        pass
    _dbx_act = load_dbx_actuals(sheets)   # DBX WMS 실입고(입고일자별 탭). 없으면 None→시트 AO/AP 폴백
    inbound_obj = build_inbound_board(sheets, as_of=TODAY, launch_meta=_lm, dbx_actuals=_dbx_act)
    inbound_block = "const INBOUND_BOARD = " + json.dumps(inbound_obj, ensure_ascii=False) + ";"
    html2, ninb = re.subn(r"const INBOUND_BOARD = \{.*?\};", lambda _m: inbound_block, html2, count=1, flags=re.DOTALL)
    _nsku = sum(h["sku_count"] for h in inbound_obj["heroes"])
    _st = {}
    for h in inbound_obj["heroes"]:
        for s in h["skus"]:
            _st[s["status"]] = _st.get(s["status"], 0) + 1
    print(f"INBOUND_BOARD 주입: {len(inbound_obj['heroes'])}히어로 · SKU {_nsku} · 날짜버킷 {len(inbound_obj['days'])} · 상태{_st} · 실적={'DBX' if _dbx_act is not None else '시트AO/AP'}({len(_dbx_act) if _dbx_act else 0} SKU)")
    if ninb != 1:
        _HEALTH.append("INBOUND_BOARD 교체 실패(앱 플레이스홀더 확인)")
except Exception as e:
    print(f"[주의] INBOUND_BOARD 주입 실패 — 기존값 유지: {type(e).__name__}: {e}")

HTML.write_text(html2, encoding="utf-8")

print(f"교체 완료: {len(heroes)} 히어로(시리즈) · APP_TODAY→{TODAY.isoformat()}(교체 {nt}) · SALES_AS_OF(교체 {nsa}) · DASHBOARD(교체 {nd}) · 27SS진척(교체 {n27}) · LAUNCH_26FW(교체 {nlaunch}) · INBOUND_BOARD(교체 {ninb})")
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
