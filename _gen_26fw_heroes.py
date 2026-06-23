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
from soo.hero_ops.triggers import load_completions, load_quantity_inputs
completions = load_completions(sheets)
print(f"완료 클릭 기록: {len(completions)}건")

# 1차수량(앱 입력) — 히어로명 기준 {role: {qty,by,at}}
qinputs = load_quantity_inputs(sheets)
print(f"1차수량 입력: {sum(len(v) for v in qinputs.values())}건 ({len(qinputs)} 히어로)")

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

# ── 실적 대시보드 데이터 주입 (build_dashboard) ──
# 소스 시트: SALES_SHEET(Databricks 잡이 매일 07:00 KST에 채우는 전용 SA 시트). raw 탭만 사용.
# goods→hero 매핑은 build_maps 가 내부 DEV_SHEET_ID(26SS 탭)에서 별도로 읽음(sheet_id 무관).
nd = 0
_DASH_HEROES = []   # IMC 히어로 시즌 판정용(대시보드 STY→시즌 큐레이션값)
try:
    from soo.hero_ops.sales_rollup import build_dashboard, SALES_SHEET_ID
    dash = build_dashboard(sheets, drive, SALES_SHEET_ID, TODAY.isoformat())
    _DASH_HEROES = dash.get("heroes", [])
    dash_block = "const DASHBOARD = " + json.dumps(dash, ensure_ascii=False) + ";"
    html2, nd = re.subn(r"const DASHBOARD = \{.*?\};", dash_block, html2, count=1, flags=re.DOTALL)
    assert nd == 1, f"DASHBOARD 교체 실패 (matched {nd})"
    print(f"DASHBOARD: 히어로 {len(dash['heroes'])}개 주입 (매핑 {dash['_stats']['mapped']}/{dash['_stats']['rows']})")
except Exception as e:
    print(f"[주의] DASHBOARD 주입 실패 — 실적 대시보드는 기존값 유지: {type(e).__name__}: {e}")

# ── 데이터 갱신 헬스체크 수집 (비어있음/구조변경 등 '조용한 실패' 가시화) ──
_HEALTH = []
SNS_SHEET_ID = "11f6JTGvms3uVcuVJW-M9Wa9-Lt4x3Tjn5IFJ2m8jifE"  # [무탠다드] SNS/CRM 콘텐츠 통합 관리
TRACKER_SHEET_ID = "1oz6zM-x2nqaDSAufWJ2a-QZh-1F6LQipttNkVKoFAn8"  # 캠페인 운영관리 트래커([히어로 PDP]에 운영 히어로 품목)
GOAL_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"  # PLM 시트(사용자 소유), '히어로 마케팅 목표' 탭=마케팅 입력란
GOAL_TAB = "히어로 마케팅 목표"
MKT_SHEET_ID = "16jqlhmynIxXckdrpjICaDNajZd-xjnrl0x332qDCtzg"  # 마케팅팀 MKT calendar (캠페인 레벨/진행상황·에너지/바이럴)


def _sns_table(tab, keys, last_col="AB", max_row=900, scan=20, optional=(), sid=None):
    """탭을 읽어 (데이터행, {key: colidx}) 반환. keys={key:[헤더 별칭...]}.
    헤더행은 별칭 매칭 수가 가장 많은 행으로 자동 탐색 → 컬럼 이동/삽입·헤더행 위치 변경에 강건(#4).
    optional: 시트마다 있을 수도/없을 수도 있는 컬럼(없어도 경고 안 함). sid: 다른 스프레드시트도 가능."""
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

    def _add(type_, channel, date_, title, sub="", owner="", **extra):
        title = str(title or "").strip()
        if not date_ or not title:
            return False
        d = {"type": type_, "channel": channel, "date": date_, "title": title[:60], "sub": sub, "owner": owner}
        d.update(extra)
        _items.append(d)
        return True

    # 1) 기존 IMC 소스 (발매스케줄/캠페인/오프라인/발매이슈/일반기획전)
    for r in _IMCT.load_releases(sheets):
        if r["grade"] not in ("HERO", "HERO SUB"):
            continue
        _add("발매", "발매", r["release"].isoformat(), r["name"], f"{r['series']}/{r['grade']}")
    for c in _IMCT.load_campaigns(sheets):
        _add("캠페인", "캠페인", c["start"].isoformat(), c["name"], c["gubun"], c["owner"])
    for g in _IMCT.load_offline_gates(sheets):
        _add("오프라인", "오프라인", g["date"].isoformat(), g["label"], g["kind"], season_gate=g["season_gate"])
    for it in _IMCT.load_release_issues(sheets):
        _add("발매이슈", "발매이슈", it["when"].isoformat(), it["issue"], it["brand"], it["owner"])
    for p in _IMCT.load_general_promos(sheets):
        _add("기획전", "기획전", p["start"].isoformat(), p["title"], "", p["owner"])

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
        _CH_MAP = {  # C열 채널값 → (IMC type, sub). 여기 없는 채널은 스킵.
            "SHOOTING": ("촬영", "촬영"), "IG_OFFICIAL": ("IG", "오피셜"),
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
        _n_camp = _n_enrich = 0
        if _mhi >= 0:
            _mh = _mc[_mhi]

            def _mcol(*names):
                return next((j for j, c in enumerate(_mh) if any(n in str(c) for n in names)), None)

            _C = {k: _mcol(*v) for k, v in {"month": ["월"], "gubun": ["구분"], "issue": ["주요 이슈"],
                  "lvl": ["레벨"], "mkt": ["마케팅"], "status": ["진행 상황"],
                  "shoot": ["촬영 타겟"], "rel": ["릴리즈"]}.items()}

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
                    _n_enrich += 1
                    continue
                if _add("캠페인", "캠페인", _date, _issue, _gc(r, "gubun"), _gc(r, "mkt"),
                        level=_lvl, mstatus=_mst, source="MKT", approx=_approx):
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
        print(f"IMC MKT calendar 로드: 캠페인 신규 {_n_camp}·보강 {_n_enrich}건 + 에너지/바이럴 {_n_energy}건")
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

    # 매칭 키워드 = 26FW 별칭 ∪ 현재 운영 히어로 품목 (공백 제거 정규화)
    _alias_norm = {a.replace(" ", "") for al in _hero_alias.values() for a in al}
    _alias_norm |= {h.replace(" ", "") for h in _cur_heroes if len(h.replace(" ", "")) >= 2}
    _alias_norm = list(_alias_norm)

    def _hero_related(it):
        if it["channel"] == "발매":
            return True
        blob = (it["title"] + " " + it.get("sub", "")).replace(" ", "")
        if "히어로" in blob:
            return True
        return any(a in blob for a in _alias_norm)

    for x in _items:
        x["hero_related"] = _hero_related(x)

    # 4) 윈도우 필터 + status 부여
    #    '2)일정' 마스터 항목(source="일정")은 hero_related인 것만 유지 — 앱 히어로 중심 성격 유지,
    #    비히어로 일반 콘텐츠(일반 IG/CRM) 노이즈 제외. 그 외 소스는 전부 유지(hero 토글로 가림).
    _t = TODAY.isoformat()
    _n_master_raw = sum(1 for x in _items if x.get("source") == "일정")
    _items = [x for x in _items
              if not (x.get("source") == "일정" and not x["hero_related"])]
    _n_master_kept = sum(1 for x in _items if x.get("source") == "일정")
    _items = sorted((x for x in _items if _back <= x["date"] <= _fwd), key=lambda x: x["date"])
    print(f"2)일정 마스터 필터: {_n_master_raw}건 중 히어로관련 {_n_master_kept}건 유지")
    for x in _items:
        x["status"] = "past" if x["date"] < _t else ("today" if x["date"] == _t else "future")
    imc_block = "const IMC = " + json.dumps({"as_of": _t, "items": _items}, ensure_ascii=False) + ";"
    html2, nimc = re.subn(r"const IMC = \{.*?\};", imc_block, html2, count=1, flags=re.DOTALL)
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

    def _agg_ig(tab, ch):  # 헤더명 기반(#4)
        # 우먼(4-2)은 유형·인기게시물·히어로콘텐츠 컬럼이 없음 → optional 처리(오탐 방지)
        rows, cm = _sns_table(tab, {"date": ["발행일"], "title": ["소재"], "form": ["유형"],
                                    "views": ["조회"], "reach": ["도달"], "likes": ["좋아요"],
                                    "popular": ["인기게시물", "인기 게시물"], "hero": ["히어로콘텐츠", "히어로 콘텐츠"]},
                              optional=("form", "popular", "hero"))
        agg = {"posts": 0, "views": 0, "reach": 0, "likes": 0, "hero": 0, "popular": 0}
        tops = []
        for r in rows:
            title, v = _gv(r, cm, "title"), _n(_gv(r, cm, "views"))
            if not title or (v == 0 and _n(_gv(r, cm, "reach")) == 0):
                continue
            agg["posts"] += 1
            agg["views"] += v
            agg["reach"] += _n(_gv(r, cm, "reach"))
            agg["likes"] += _n(_gv(r, cm, "likes"))
            if _gv(r, cm, "popular").upper() == "O":
                agg["popular"] += 1
            if _gv(r, cm, "hero").upper() == "O":
                agg["hero"] += 1
                tops.append({"ch": ch, "title": title[:40], "date": _gv(r, cm, "date"), "views": v, "type": _gv(r, cm, "form")})
        if agg["posts"] == 0:
            _HEALTH.append(f"성과 '{tab}' 0건")
        return agg, tops

    agg_off, tops_off = _agg_ig("4-1)성과_오피셜 IG", "오피셜")
    agg_wm, tops_wm = _agg_ig("4-2)성과_우먼 IG", "우먼")

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

    # 히어로별 PMKT 성과 — 캠페인 트래커 [히어로 PDP](PDP 조회) + [히어로 Convs](거래) 품목별 집계.
    # 주차 컬럼이 반복돼 헤더명이 여러 번 나오므로 '모든 매칭 컬럼' 합산. 그룹 집계행(품목 有·브랜드 空)만 사용.
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
    _hero_stys = {}   # 히어로 품목 → STY_No 베이스(접미사 제거) 집합. 시즌 판정용.
    _wk_labels = []   # 주차별 추세용 라벨(W2..W10), [히어로 Convs] 실거래액 컬럼과 1:1
    _pdp_wk_labels = []   # 주차별 추세용 라벨, [히어로 PDP] 실 PDP 조회 컬럼과 1:1
    try:
        _pr = _raw("[히어로 PDP]", TRACKER_SHEET_ID)
        _hi = _hdr_idx(_pr, ["HERO 품목", "실 PDP 조회"])
        if _hi >= 0:
            _h = _pr[_hi]
            _ji = next((j for j, c in enumerate(_h) if "HERO 품목" in str(c)), 1)
            _jb = next((j for j, c in enumerate(_h) if "브랜드" in str(c)), 2)
            _jsty = next((j for j, c in enumerate(_h) if "STY" in str(c)), 3)
            _cr, _ca = _cols_with(_h, "실 PDP 조회"), _cols_with(_h, "광고 PDP 조회")
            # 주차 라벨 행(헤더 위 4행 내, 'W2 (날짜~)' 형태) → '실 PDP 조회' 컬럼은 이미 주차 순서
            _pwrow = next((_pr[_ri] for _ri in range(max(0, _hi - 4), _hi)
                           if any(_re3.match(r"\s*W\d+", str(c or "")) for c in _pr[_ri])), None)
            _pdp_wk_labels = []
            if _pwrow is not None:
                _panch = sorted((j, _re3.match(r"\s*(W\d+)", str(c)).group(1))
                                for j, c in enumerate(_pwrow) if _re3.match(r"\s*W\d+", str(c or "")))
                for j in _cr:
                    _pdp_wk_labels.append(next((al for ac, al in reversed(_panch) if ac <= j), ""))
            else:
                _pdp_wk_labels = [f"W{i + 2}" for i in range(len(_cr))]
            for r in _pr[_hi + 1:]:
                it = _g2(r, _ji)
                if not it or it.startswith("무신사 스탠다드"):
                    continue
                # STY는 상세행(브랜드 有)에 있음 — 브랜드 필터 전에 수집(시즌 판정용).
                _sty = _g2(r, _jsty).split("-")[0].strip()              # 'MMEWS5Z01-SB' → 'MMEWS5Z01'
                if _sty:
                    _hero_stys.setdefault(it, set()).add(_sty)
                if _g2(r, _jb):   # 그룹 집계행(품목 有·브랜드 空)만 PDP 합산
                    continue
                hero_perf.setdefault(it, {})
                hero_perf[it]["pdp_real"] = sum(_n(_g2(r, j)) for j in _cr)
                hero_perf[it]["pdp_ad"] = sum(_n(_g2(r, j)) for j in _ca)
                hero_perf[it]["pdp_wk"] = [_n(_g2(r, j)) for j in _cr]   # 주차별 실 PDP 조회(추세)
        _cv = _raw("[히어로 Convs]", TRACKER_SHEET_ID)
        _hi = _hdr_idx(_cv, ["HERO 품목", "실 거래액"])
        if _hi >= 0:
            _h = _cv[_hi]
            _ji = next((j for j, c in enumerate(_h) if "HERO 품목" in str(c)), 1)
            _jb = next((j for j, c in enumerate(_h) if "브랜드" in str(c)), 2)
            _cc, _cg = _cols_with(_h, "실 거래수"), _cols_with(_h, "실 거래액")
            # 광고 기여 거래액 = 바로 "거래액"(PMKT)+"거래액"(상품광고) 컬럼. "실 거래액"은 정확일치로 제외.
            _cag = [j for j, c in enumerate(_h) if str(c or "").strip() == "거래액"]
            # 주차 라벨 행(헤더 위 4행 내, 'W2 (날짜~)' 형태) → 실거래액 컬럼별 주차명 매핑
            _wrow = next((_cv[_ri] for _ri in range(max(0, _hi - 4), _hi)
                          if any(_re3.match(r"\s*W\d+", str(c or "")) for c in _cv[_ri])), None)
            if _wrow is not None:
                _anch = sorted((j, _re3.match(r"\s*(W\d+)", str(c)).group(1))
                               for j, c in enumerate(_wrow) if _re3.match(r"\s*W\d+", str(c or "")))
                for j in _cg:
                    _lab = next((al for ac, al in reversed(_anch) if ac <= j), "")
                    _wk_labels.append(_lab)
            else:
                _wk_labels = [f"W{i + 2}" for i in range(len(_cg))]
            for r in _cv[_hi + 1:]:
                it = _g2(r, _ji)
                if not it or it.startswith("무신사 스탠다드") or _g2(r, _jb):
                    continue
                hero_perf.setdefault(it, {})
                hero_perf[it]["conv"] = sum(_n(_g2(r, j)) for j in _cc)
                hero_perf[it]["gmv"] = sum(_n(_g2(r, j)) for j in _cg)
                hero_perf[it]["ad_gmv"] = sum(_n(_g2(r, j)) for j in _cag)
                hero_perf[it]["wk"] = [_n(_g2(r, j)) for j in _cg]   # 주차별 실거래액(추세)
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

    # 히어로 시즌 판정 — 새 시트/하드코딩 없이 기존 데이터로 추론(트래커엔 시즌 컬럼 없음):
    #   ① 트래커 STY가 대시보드 히어로 STY와 겹치면 → 대시보드의 큐레이션 시즌(예: 26SS)
    #   ② 아니면 26FW PLM 마스터(plm)에 있으면 → 26FW
    #   ③ 둘 다 아니면 → 직전 FW(25FW). 26FW 마스터에 없는 이전 시즌 캐리오버(예: 경량 패딩).
    _dash_sty2season = {s.get("style"): h.get("season")
                        for h in _DASH_HEROES for s in (h.get("stys") or []) if s.get("style")}
    _dash_name2season = {h.get("name"): h.get("season") for h in _DASH_HEROES if h.get("season")}

    # 진짜 현재/예정 히어로는 전부 대시보드에 시즌과 함께 있음(26SS·26FW 큐레이션).
    # ⚠ PLM '데이터' 시트의 시즌 컬럼은 전 행 '26FW' 균일(시트 스코프 라벨일 뿐 제품 시즌 아님)
    #   + 발매 2025-02짜리 캐리오버(예: 신세틱 스웨이드)도 들어있음 → "마스터에 있으면 26FW"는 오탐.
    #   따라서 대시보드에 없으면 = 이전 시즌(운영 종료)로 처리.
    def _resolve_season(name):
        stys = _hero_stys.get(name, set())
        for s in stys:                                  # ① STY가 대시보드 히어로 STY와 겹치면 그 시즌
            if s in _dash_sty2season and _dash_sty2season[s]:
                return _dash_sty2season[s]
        if name in _dash_name2season:                   # ② 이름이 대시보드 히어로와 일치(STY 없는 경우, 예: 윈드브레이커)
            return _dash_name2season[name]
        # ③ 대시보드에 없음 = 현재 운영 아님. 정확한 시즌은 신뢰할 소스 없음(PLM 시즌컬럼 균일·발매일 캐리오버)
        #    → 시즌 추측 대신 '판매종료' 딱지(사용자 결정 2026-06-22). 예: 경량패딩·신세틱.
        return "판매종료"

    hero_list = sorted(
        [{"name": k, "pdp_real": v.get("pdp_real", 0), "pdp_ad": v.get("pdp_ad", 0),
          "gmv": v.get("gmv", 0), "conv": v.get("conv", 0), "ad_gmv": v.get("ad_gmv", 0),
          "wk": v.get("wk", []), "pdp_wk": v.get("pdp_wk", []),
          "season": _resolve_season(k),
          "goal": _goals.get(k, {}).get("gmv", 0),
          "goal_roas": _goals.get(k, {}).get("roas", "")} for k, v in hero_perf.items()],
        key=lambda x: -x["gmv"])
    if not hero_list:
        _HEALTH.append("히어로 PMKT 성과 0건 — 트래커 구조 확인")
    print("IMC 히어로 시즌: " + ", ".join(f"{h['name']}={h['season']}" for h in hero_list))

    perf = {"ig": {"오피셜": agg_off, "우먼": agg_wm}, "crm": crm, "budget": budget,
            "highlights": highlights, "hero": hero_list, "weeks": _wk_labels,
            "pdp_weeks": _pdp_wk_labels}
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

HTML.write_text(html2, encoding="utf-8")

print(f"교체 완료: {len(heroes)} 히어로(시리즈) · APP_TODAY→{TODAY.isoformat()}(교체 {nt}) · SALES_AS_OF(교체 {nsa}) · DASHBOARD(교체 {nd}) · 27SS진척(교체 {n27})")
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
