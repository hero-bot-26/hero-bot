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
# ★PLM 소스 기본값 = 구글시트(데이터브릭스 자동출력, 매일 갱신). CI는 원래 --sheet를 붙여 돌지만
#   수동 실행에서 빼먹으면 드라이브의 오래된 마일스톤 엑셀로 폴백해 PLM 미등록이 부풀었다
#   (2026-07-31 실측: 시트 9건 → 드라이브 15건, 리커버리 6종이 통째로 미등록으로 찍힘).
#   → 기본을 시트로 두고, 옛 경로가 필요할 때만 --drive 로 명시한다.
USE_SHEET = "--drive" not in sys.argv

# MDP 26FW 트랙별 베이스라인 (단계 n → 'YYYY-MM-DD').
# ★2026-08-01: 아래 값은 이제 **폴백**이다 — MDP 기획 관리판 `#.상세일정`에서 매 실행 자동으로 읽는다
#   (`soo.hero_ops.mdp_baseline`). 시트가 바뀌면 앱도 따라가고, 27FW·28SS도 같은 방식으로 자동 인식된다.
#   읽기 실패 시에만 이 하드코딩 값을 쓴다(조용한 0/공백 방지).
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


def _load_mdp_baseline_26fw(sheets):
    """MDP `#.상세일정`의 26FW 블록 → BASELINE 형태로. 실패하면 하드코딩 폴백을 그대로 쓴다."""
    try:
        from soo.hero_ops.mdp_baseline import load_mdp_baseline
        warns = []
        bl = load_mdp_baseline(sheets, "26FW", warns=warns)
        got = {t: {n: d.isoformat() for n, d in st.items()} for t, st in bl.items() if t in ("가을", "겨울")}
        if len(got) < 2 or any(len(v) < 5 for v in got.values()):
            print(f"[주의] MDP 26FW 기준일 부족 — 폴백 사용 ({ {k: len(v) for k, v in got.items()} })")
            for w in warns[:3]:
                print("   ", w)
            return None
        # 폴백에만 있는 단계(시트에 행이 없는 것)는 채워 넣는다 — 있던 기준이 사라지지 않게.
        for trk, base in BASELINE.items():
            for n, v in base.items():
                got.setdefault(trk, {}).setdefault(n, v)
        _diff = [f"{t} 단계{n} {BASELINE[t].get(n, '-')}→{got[t][n]}"
                 for t in got for n in sorted(got[t]) if BASELINE.get(t, {}).get(n) != got[t][n]]
        print(f"MDP 26FW 기준일 시트 로드: 가을 {len(got['가을'])}단계 · 겨울 {len(got['겨울'])}단계"
              + (f" · 폴백과 다른 값 {len(_diff)}건 ({' / '.join(_diff[:4])})" if _diff else " · 폴백과 동일"))
        for w in warns[:3]:
            print("   ", w)
        return got
    except Exception as e:
        print(f"[주의] MDP 26FW 기준일 로드 실패 — 폴백 사용: {type(e).__name__}: {e}")
        return None
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

# ── 소스 레지스트리(앱 "소스" 탭) → 원천 시트 링크 동적 로드 ──────────────────
# 담당자가 앱에서 링크만 갈아끼우면 그 소스만 여기서 바뀜. 비어 있으면(현재) DEFAULTS=현재 하드코딩값.
# 스펙: hero-master-app/docs/source-registry.md · 절대 예외/0덮어쓰기 없음(load_registry가 실패 시 {}).
# ★sheets 정의 직후에 로드 — DASHBOARD 등 일부 주입 블록이 상수 정의부보다 앞서 _src()를 씀.
from soo.hero_ops import source_registry as _SRCREG
_REG = _SRCREG.load_registry(sheets)

def _src(key):
    """소스키 → 현재 유효 스프레드시트 ID(레지스트리 우선, 없으면 DEFAULTS)."""
    return _SRCREG.source_id(key, _REG)

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
_EARLY_MSGS = []    # _HEALTH 정의(아래) 전에 생기는 경고 임시 보관 — ★블록 순서=스코프 함정 회피
try:
    from soo.hero_ops.po_ingest import parse_po_qty, CHANNELS as PO_CH
    po_qty = parse_po_qty(sheets, "2026FW")
    print(f"PO수량: {sum(1 for v in po_qty.values() if v['po']['t'])} 스타일 (2026FW)")
except Exception as e:
    po_qty, PO_CH = {}, ("dom_on", "dom_off", "chn_on", "chn_off")
    print(f"[주의] PO수량 로드 실패: {type(e).__name__}: {e}")
    _EARLY_MSGS.append(f"PO수량(MD투입) 로드 실패({type(e).__name__}) — 발주량 직전값 유지")

# 준비수량(26FW) — MSTRD 상품MAP 'SKU' 탭 AA/AB/AC. 수량 탭·실적 소진율의 공통 기준(사용자 확정 2026-07-30).
try:
    from soo.hero_ops.imc_triggers import load_26fw_prep
    prep26 = load_26fw_prep(sheets, sid=_src("mstrd"))
    print(f"준비수량(26FW): 스타일 {len(prep26)}개 · 합계 {sum(v['t'] for v in prep26.values()):,}")
except Exception as e:
    prep26 = {}
    print(f"[주의] 준비수량 로드 실패: {type(e).__name__}: {e}")
    _EARLY_MSGS.append(f"준비수량(MSTRD SKU AA/AB/AC) 로드 실패({type(e).__name__}) — 직전값 유지")

# 히어로별 준비수량 — ★귀속은 MSTRD 'HERO STY' 매핑(스냅샷 hero_goods_26fw.json)으로.
#   수량 탭이 PLM style 목록으로 합산하면 실적 대시보드(같은 MSTRD 매핑)와 값이 어긋난다
#   (커브드 559,231 vs 508,831 처럼). 두 화면이 같은 수를 보이도록 기준을 하나로 묶는다.
_PREP_HERO, _PREP_STY = {}, {}
try:
    _snap26 = json.load(open(ROOT / "hero_goods_26fw.json", encoding="utf-8"))
    _pnorm = lambda x: re.sub(r"\s+", "", str(x or ""))
    for _sty, _hero in (_snap26.get("style_to_hero") or {}).items():
        _pv = prep26.get(_sty)
        if not _pv:
            continue
        _k = _pnorm(_hero)
        _acc = _PREP_HERO.setdefault(_k, {"t": 0, "o": 0, "f": 0})
        for _c in _acc:
            _acc[_c] += _pv.get(_c, 0)
        _PREP_STY.setdefault(_k, {})[_sty] = dict(_pv)
    print(f"준비수량 히어로 귀속: {len(_PREP_HERO)}종 (MSTRD HERO STY 기준)")
except Exception as _eph:
    print(f"[주의] 준비수량 히어로 귀속 실패: {type(_eph).__name__}: {_eph}")

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

# ★단계 기준일 = MDP 시트에서 로드(실패 시 위 하드코딩 폴백). 26FW·27FW·28SS 모두 같은 경로.
_MDP_BL = _load_mdp_baseline_26fw(sheets)
if _MDP_BL:
    BASELINE = _MDP_BL

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

    # 준비수량 — MSTRD HERO STY 기준(실적 대시보드 소진율 분모와 동일 집합)
    _pk = re.sub(r"\s+", "", str(series or ""))
    prep_q = dict(_PREP_STY.get(_pk) or {})
    prep_tot = dict(_PREP_HERO.get(_pk) or {"t": 0, "o": 0, "f": 0})

    heroes.append({
        "id": f"26FW_{i:03d}", "season": "26FW", "track": track,
        "name": series, "category": category,
        "ownerMD": owner_md, "ownerDesigner": owner_ds,
        "styles": styles,
        "stages": stages, "dates": dates,
        "stage5": {"tentativeColors": [], "inputs": s5_inputs, "meta": s5_meta,
                   "confirmed": {"online_sales": None, "offline_sales": None}, "completedAt": None},
        "stage8": {"sentAt": None, "poQuantities": po_q, "po": po_tot,
                   "prepQuantities": prep_q, "prep": prep_tot},
        "stys": stys,
        "_plmMatched": len(matched), "_styleCount": len(styles),
    })

# ── app.html HEROES 배열 + APP_TODAY 교체 ──
html = HTML.read_text(encoding="utf-8")
# ★조용한 0 방지(2026-07-30) — PO수량 로드가 실패한 날(시트 헤더 개명·권한 등) 0을 덮어쓰면
#   단계8 발주량이 전 히어로 0이 된다(실제로 오늘 배포본이 15종 전부 0이었다). 직전값을 보존한다.
if not po_qty or not prep26:
    try:
        _pm = re.search(r"const HEROES = (\[.*?\n\]);", html, re.DOTALL)
        _prev_h8 = {h["name"]: (h.get("stage8") or {}) for h in json.loads(_pm.group(1))} if _pm else {}
        _rest = 0
        _rest_p = 0
        for _h in heroes:
            _p8 = _prev_h8.get(_h["name"]) or {}
            if not po_qty and (_p8.get("po") or {}).get("t"):
                _h["stage8"]["po"] = _p8["po"]
                _h["stage8"]["poQuantities"] = _p8.get("poQuantities") or {}
                _rest += 1
            if not prep26 and (_p8.get("prep") or {}).get("t"):
                _h["stage8"]["prep"] = _p8["prep"]
                _h["stage8"]["prepQuantities"] = _p8.get("prepQuantities") or {}
                _rest_p += 1
        if _rest_p:
            print(f"[보존] 준비수량 실패 — 직전값 유지({_rest_p}종)")
        if _rest:
            print(f"[보존] PO수량 실패 — 직전 발주량 유지({_rest}종)")
    except Exception as _epo:
        print(f"[주의] PO수량 직전값 보존 실패: {type(_epo).__name__}: {_epo}")
clean = [{k: v for k, v in h.items() if not k.startswith("_")} for h in heroes]
new_block = "const HEROES = " + json.dumps(clean, ensure_ascii=False, indent=2) + ";"
html2, n = re.subn(r"const HEROES = \[.*?\n\];", new_block, html, count=1, flags=re.DOTALL)
assert n == 1, f"HEROES 배열 교체 실패 (matched {n})"
html2, nt = re.subn(r"const APP_TODAY = '[^']*';",
                    f"const APP_TODAY = '{TODAY.isoformat()}';", html2, count=1)
# 홈 화면 실적 카드 기준일(하드코딩 SALES_AS_OF)도 DASHBOARD.as_of와 동일하게 매일 갱신
html2, nsa = re.subn(r"const SALES_AS_OF = '[^']*';",
                     f"const SALES_AS_OF = '{TODAY.isoformat()}';", html2, count=1)
# 실제 생성(갱신) 시각(KST). GitHub Actions 스케줄 지연으로 '매일 10시' 고정표기가 실제(≈13시)와
# 어긋나므로, 화면엔 하드코딩 시각 대신 이 실측 타임스탬프를 노출한다. CI는 UTC라 +9h.
_GEN_KST = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
html2, nga = re.subn(r"const GEN_AT = '[^']*';",
                     f"const GEN_AT = '{_GEN_KST.strftime('%Y-%m-%d %H:%M')}';", html2, count=1)

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
_STALE_MSGS = []    # _HEALTH 정의(아래) 전에 생기는 경고 임시 보관 — ★블록 순서=스코프 함정 회피
_SALES_FRESH = True
try:
    from soo.hero_ops.sales_rollup import build_dashboard, SALES_SHEET_ID, build_style_to_hero, read_tab
    from soo.hero_ops.sales_rollup import check_freshness as _check_fresh
    _SALES_ID = _src("dashboard") or SALES_SHEET_ID   # dashboard 소스키 오버라이드(실적 전용 시트)
    # ★신선도 게이트(2026-07-29) — DBX 잡(09:30~약 3h)이 시트를 쓰는 도중 CI가 읽으면 탭마다 기준일이 섞인다.
    #   실제로 07-29엔 MTD는 7/28인데 FWTD는 전날 실행분(7/27)이라 26FW 누계가 하루 통째로 밀렸다.
    #   → 하나라도 어긋나면 실적·PMKT 블록을 '주입만' 건너뛰어 앱 직전값(하루 stale, 대신 정합)을 유지한다.
    _SALES_ASOF = (TODAY - datetime.timedelta(days=1)).strftime("%Y%m%d")
    _SALES_FRESH, _SALES_BAD = _check_fresh(sheets, _SALES_ID, _SALES_ASOF)
    # ★그룹별 판정(2026-07-31) — 매출 탭과 PMKT 계열은 노트북에서 따로 채워져 한쪽만 늦는 날이 있다.
    #   한 덩어리로 막으면 멀쩡한 매출까지 옛값으로 묶여 '누계<당월' 같은 모순이 남는다.
    _SALES_TAB_SET = {"YTD", "MTD", "WEEK", "DAY", "FWTD", "직전WEEK"}
    _BAD_TABS = {b.split()[0] for b in _SALES_BAD}
    _FRESH_SALES = not (_BAD_TABS & _SALES_TAB_SET)      # 매출(대시보드·홈 26FW)
    _FRESH_PMKT = not (_BAD_TABS - _SALES_TAB_SET)       # PMKT·퍼널·경로·PDP일별
    if not _SALES_FRESH:
        print(f"[신선도] 실적시트 기준일 불일치 {len(_SALES_BAD)}건 — 실적·PMKT 주입 스킵(직전값 유지): "
              + " · ".join(_SALES_BAD[:6]))
        _STALE_MSGS.append("실적시트 기준일 불일치 → 실적·PMKT 갱신 보류(직전값 유지): " + " · ".join(_SALES_BAD[:4]))
    # 홈 실적 = 시트39 확정 26SS 매핑(uid+신품번, 사용자 검증 524.5억=525.4). 성과 탭과 동일 히어로 정의.
    _map26 = json.load(open(ROOT / "hero_goods_26ss.json", encoding="utf-8"))
    _dash_s2h = _map26["style_to_hero"]
    dash = build_dashboard(sheets, drive, _SALES_ID, TODAY.isoformat(),
                           style2hero=_dash_s2h, goods2hero=_map26["goods_to_hero"],
                           # ★시즌은 매핑 파일(26SS)로 고정 — 안 주면 8/1부터 current_season이 26FW로 넘어가
                           #   26SS 히어로 14종이 26FW 배지로 뒤집힌다(2026-08-01 발생·수정).
                           force_season="26SS",
                           inbound_season="26SS")   # SS입고 = 전년 12/1~
    _DASH_HEROES = dash.get("heroes", [])
    # ── 26FW 히어로도 같은 대시보드에 싣는다 (홈 26FW → '상세'가 빈 화면이던 문제) ──
    #   ★누계(YTD 슬롯)는 FWTD(7/1~) 탭 = 시즌 누계. 달력 YTD면 캐리오버 STY의 봄 판매가 섞인다.
    #   퍼널도 같은 기간으로 — PDP퍼널 탭에 FWTD 행이 있으면 그걸 누계 슬롯으로 읽는다(노트북 _fp에 FWTD 추가, 2026-07-29).
    #   FWTD 행이 아직 없으면 aggregate_funnel이 퍼널을 통째로 꺼서 '-'로 남긴다(어긋난 값 방지).
    try:
        from soo.hero_ops.sales_rollup import PERIOD_TABS as _PT
        _fw_map = json.load(open(ROOT / "hero_goods_26fw.json", encoding="utf-8"))
        _fw_tabs = dict(_PT)
        _fw_tabs["YTD"] = ("FWTD", "전년FWTD")
        try:
            read_tab(sheets, _SALES_ID, "FWTD", max_row=2)
        except Exception:
            _fw_tabs["YTD"] = _PT["YTD"]     # 탭 없으면 폴백(노트북 잡 완료 전)
            print("[주의] FWTD 탭 없음 — 대시보드 26FW 누계를 달력 YTD로 폴백")   # ★_HEALTH는 아직 미정의(380행)
        # ── 26FW 목표·준비수량 (담당자 시트 `26FW HERO 일자별 목표 셋팅`) ──
        #   ★기존엔 26SS 소스('히어로목표(거량)' 탭, 1/1~ 달력 기준)를 26FW 행에도 붙여 달성율이
        #     무조건 미달로 보였다(사용자 지적 2026-07-30). 26FW 전용 일자별 목표로 교체.
        FW_TARGETS, _fw_prep = None, None
        try:
            from soo.hero_ops.target_26fw import parse_26fw_targets, style_prep_map
            FW_TARGETS = parse_26fw_targets(drive, TODAY.isoformat(), fid=(_src("target_26fw") or None))
            _fw_tmeta = FW_TARGETS.pop("_meta", {})
            _fw_prep = style_prep_map(FW_TARGETS)
            print(f"26FW 목표: 스타일 {len(FW_TARGETS)}개 · 시즌시작 {_fw_tmeta.get('season_start')} "
                  f"· 누계창 {(_fw_tmeta.get('windows') or {}).get('YTD')}")
        except Exception as _etg:
            FW_TARGETS, _fw_prep = None, None
            print(f"[주의] 26FW 목표 로드 실패 — 목표 미설정 유지: {type(_etg).__name__}: {_etg}")
            _STALE_MSGS.append(f"26FW 목표 시트 로드 실패({type(_etg).__name__}) — 달성율·소진율 미표시")
        _dash_fw = build_dashboard(sheets, drive, _SALES_ID, TODAY.isoformat(),
                                   style2hero=_fw_map["style_to_hero"],
                                   goods2hero=_fw_map["goods_to_hero"],
                                   period_tabs=_fw_tabs, force_season="26FW",
                                   funnel_periods=({"YTD": "FWTD"} if _fw_tabs["YTD"][0] == "FWTD" else None),
                                   # ★HERO SUB까지 전건 노출(발매 전 STY는 pending 행) — 'MAIN만 잡힌다' 대응
                                   style_meta=_fw_map.get("styles") or {}, include_all_styles=True,
                                   goods_to_style=_fw_map.get("goods_to_style") or None,
                                   # ★목표·준비물량 미부착 — 소스가 26SS 시즌 기준이라 26FW 누계와 비교 불가
                                   #   대신 26FW 전용 목표 시트를 주입(없으면 목표 미설정으로 남는다).
                                   # ★소진율 분모 = MSTRD SKU 준비물량(수량 탭과 동일 기준). 목표 시트 준비수량은 미사용.
                                   with_targets=False, targets_map=FW_TARGETS,
                                   prep_map=(prep26 or _fw_prep),
                                   inbound_season="26FW")   # FW입고 = 6/1~
        _fw_heroes = _dash_fw.get("heroes", [])
        for _fh in _fw_heroes:
            _fh["ytd_from"] = "2026-07-01" if _fw_tabs["YTD"][0] == "FWTD" else None   # 앱 라벨용
        dash["heroes"] = dash["heroes"] + _fw_heroes
        print(f"DASHBOARD 26FW: 히어로 {len(_fw_heroes)}개 추가 (누계 탭 {_fw_tabs['YTD'][0]})")
        # ★시즌 뒤집힘 트립와이어(2026-08-01 사고 재발 방지) — 26SS 블록 히어로가 26FW로 표시되는 사고가
        #   달이 바뀌는 날 조용히 났다. 배지는 이제 force_season으로 고정돼 있지만, 다른 경로로 다시
        #   틀어지면 여기서 잡아 슬랙까지 올린다(주입은 계속 — 값 자체는 정상이므로).
        _exp_ss = set(_map26["style_to_hero"].values()) | set(_map26["goods_to_hero"].values())
        _exp_fw = set(_fw_map["goods_to_hero"].values()) | set((_fw_map.get("style_to_hero") or {}).values())
        _bad_season = [f"{h.get('name')}={h.get('season')}" for h in dash["heroes"]
                       if h.get("season") == "26SS" and h.get("name") not in _exp_ss]
        _bad_season += [f"{h.get('name')}={h.get('season')}" for h in dash["heroes"]
                        if h.get("season") == "26FW" and h.get("name") not in _exp_fw]
        if _bad_season:
            _HEALTH.append("★시즌 배지 불일치 — " + " · ".join(_bad_season[:6]))
            print("[경고] 시즌 배지가 매핑과 안 맞는 히어로: " + " · ".join(_bad_season[:10]))
        _sc_dash = {}
        for _h in dash["heroes"]:
            _sc_dash[_h.get("season")] = _sc_dash.get(_h.get("season"), 0) + 1
        print("DASHBOARD 시즌 분포: " + " · ".join(f"{k} {v}종" for k, v in sorted(_sc_dash.items())))
        if _sc_dash.get("26SS", 0) < 10:      # 26SS는 확정 15종 — 급감 = 시즌 배지 사고
            _HEALTH.append(f"★시즌 배지 이상 — 26SS 히어로가 {_sc_dash.get('26SS', 0)}종뿐(정상 15종)")
            print(f"[경고] 26SS 히어로 {_sc_dash.get('26SS', 0)}종 — 시즌 배지 뒤집힘 의심")
    except Exception as _efw:
        print(f"[주의] DASHBOARD 26FW 블록 스킵 — 26SS만 유지: {type(_efw).__name__}: {_efw}")
    if _FRESH_SALES:
        dash_block = "const DASHBOARD = " + json.dumps(dash, ensure_ascii=False) + ";"
        html2, nd = re.subn(r"const DASHBOARD = \{.*?\};", dash_block, html2, count=1, flags=re.DOTALL)
        assert nd == 1, f"DASHBOARD 교체 실패 (matched {nd})"
        print(f"DASHBOARD: 히어로 {len(dash['heroes'])}개 주입 (매핑 {dash['_stats']['mapped']}/{dash['_stats']['rows']})")
    else:
        nd = 1   # 주입 스킵(직전값 유지) — 아래 STY_NAMES·시즌판정은 계산본 그대로 사용
        print(f"DASHBOARD: 주입 스킵(직전값 유지) — 계산본 히어로 {len(dash['heroes'])}개는 시즌 판정에만 사용")
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
_HEALTH.extend(_EARLY_MSGS)   # ★_HEALTH 정의 전(PO수량 블록)에 쌓인 경고
_HEALTH.extend(_STALE_MSGS)   # ★_HEALTH 정의 전(대시보드 블록)에 쌓인 경고를 여기서 합류시킨다
SNS_SHEET_ID = "11f6JTGvms3uVcuVJW-M9Wa9-Lt4x3Tjn5IFJ2m8jifE"  # [무탠다드] SNS/CRM 콘텐츠 통합 관리
TRACKER_SHEET_ID = "1oz6zM-x2nqaDSAufWJ2a-QZh-1F6LQipttNkVKoFAn8"  # 캠페인 운영관리 트래커([히어로 PDP]에 운영 히어로 품목)
GOAL_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"  # PLM 시트(사용자 소유), '히어로 마케팅 목표' 탭=마케팅 입력란
GOAL_TAB = "히어로 마케팅 목표"
MKT_SHEET_ID = "16jqlhmynIxXckdrpjICaDNajZd-xjnrl0x332qDCtzg"  # 마케팅팀 MKT calendar (캠페인 레벨/진행상황·에너지/바이럴)

# SNS 클러스터 4개 소스키는 현재 물리적으로 같은 시트에 공존(imc_calendar). 소스키별 독립 오버라이드 유지:
#   imc_calendar → SNS_SHEET_ID 전역(일정/온사이트/PR/IG광고) · sns_perf/crm_perf/budget → 각 호출부 sid=
# (_REG·_src 는 파일 상단 sheets 정의 직후에 로드됨 — DASHBOARD 주입 블록이 여기보다 앞서 _src 를 씀)
SNS_SHEET_ID = _src("imc_calendar") or SNS_SHEET_ID
print("[소스] " + " · ".join(_SRCREG.describe(_REG)))


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
    _FW_HERO_MAP = _IMCT0.load_26fw_hero_goods(sheets, sid=_src("mstrd"))
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
        _on_items = _IMCT.load_online(sheets)
        for it in _on_items:
            # 전사 캠페인(무진장·빅세일·멤버스데이 등)은 별도 타입 '전사' — 프론트가 히어로 필터와
            # 채널 토글을 우회해 항상 노출(사용자 지시 2026-07-27).
            _ty = "전사" if it.get("company") else "온라인"
            if _add(_ty, _ty, it["date"].isoformat(), it["name"], it["sub"],
                    approx=it.get("approx", False),
                    end=(it["end"].isoformat() if it.get("end") else ""), guide=it.get("guide", "")):
                _n_on += 1
        print(f"IMC 온라인 캠페인 로드: {_n_on}건")
        if _n_on == 0:
            _HEALTH.append("온라인 캠페인 0건 — 시트 권한/구조 확인")

        # ★월별 SUMMARY 감시(사용자 지시 2026-07-28: "매일 업데이트할 때 놓치지 말고 봐야 한다").
        #   매월 말 다음 달 상세 탭("26' N월 SUMMARY")이 새로 생긴다. 탭 이름이 달라지거나 담당자가
        #   안 채우면 조용히 연간 백본만 남아 캘린더가 헐거워지는데, 지금껏 아무 신호가 없었다.
        #   (실제로 7·8월 '맨'이 통째로 비어 있었는데 두 달간 아무도 몰랐다.)
        #   → 매일 갱신에서 ①이번 달 ②다음 달 ③브랜드 실종을 헬스체크로 잡아 슬랙 통지.
        _mon_by = {}
        for _x in _on_items:
            if _x.get("month"):
                _mon_by.setdefault(_x["month"], []).append(_x.get("brand") or "(공백)")
        _m_now = TODAY.month
        _m_next = _m_now % 12 + 1
        print("월별 SUMMARY 커버리지: "
              + " · ".join(f"{m}월 {len(v)}" for m, v in sorted(_mon_by.items())))
        if _m_now not in _mon_by:
            _HEALTH.append(f"온라인 {_m_now}월 SUMMARY 상세 0건 — 탭명('26' {_m_now}월 SUMMARY')·헤더 확인")
        if TODAY.day >= 25 and _m_next > _m_now and _m_next not in _mon_by:
            _HEALTH.append(f"온라인 {_m_next}월 SUMMARY 아직 없음 — 월말이면 다음 달 상세가 나와야 함")
        # 브랜드 실종: 직전 달에 3건 이상이던 브랜드가 이번·다음 달 모두 0건이면 원천 공백 의심.
        _b_prev = Counter(_mon_by.get(_m_now - 1 or 12, []))
        _b_cur = set(_mon_by.get(_m_now, [])) | set(_mon_by.get(_m_next, []))
        _gone = sorted(b for b, n in _b_prev.items() if n >= 3 and b != "(공백)" and b not in _b_cur)
        if _gone:
            _HEALTH.append(f"온라인 SUMMARY 브랜드 실종: {'·'.join(_gone)} — "
                           f"{_m_now}·{_m_next}월 0건(직전 달엔 있었음). 원천 미기입/이관 확인")
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

        # ③ '주요 세일즈 캠페인' 섹션(쇼케이스·캠페인·맨·우먼·키즈·홈·라이브커머스) 전량 → 타입 '전사'.
        #   사용자 지시(2026-07-28): 세일즈 캠페인은 히어로 무관하게 캘린더에 항상 보이게 = '전사' 꼭지.
        #   ★그동안 이 파일에서 읽던 건 ①캠페인 통합관리(레벨/상태) ②에너지 레인뿐이라, 그리드의
        #     세일즈 캠페인 행(빅토리아울x스토커즈 8/12 등)이 통째로 누락돼 있었음.
        #   섹션 경계는 A열 라벨로 탐지(행 이동에 강함). 병합셀은 시작일 컬럼에만 값이 들어옴.
        def _sales_end(txt, start):     # "(6/14~6/24)" · "(5/11~17)" 같은 기간 표기 → 종료일
            m = _re2.search(r"(\d{1,2})\s*/\s*(\d{1,2})\s*[~\-–]\s*(?:(\d{1,2})\s*/\s*)?(\d{1,2})", txt)
            if not m:
                return ""
            _em = int(m.group(3) or m.group(1))
            try:
                e = _dt.date(start.year + (1 if _em < start.month else 0), _em, int(m.group(4)))
            except ValueError:
                return ""
            return e.isoformat() if start <= e <= start + _dt.timedelta(days=120) else ""

        _n_sales = _n_sdup = 0
        if _grid and _c2d:
            _si = next((i for i, r in enumerate(_grid) if r and _norm(r[0]) == "주요세일즈캠페인"), -1)
            if _si >= 0:
                _send = next((i for i in range(_si + 1, len(_grid))
                              if _grid[i] and str(_grid[i][0]).strip()), len(_grid))
                for i in range(_si, _send):
                    r = _grid[i]
                    _lane = str(r[1]).strip() if len(r) > 1 and r[1] else "세일즈 캠페인"
                    for j, _iso in sorted(_c2d.items()):
                        _v = _clean(str(r[j]).strip() if j < len(r) and r[j] is not None else "")
                        if len(_v) <= 2:
                            continue
                        _nv, _dd = _norm(_v), _dt.date.fromisoformat(_iso)
                        # 이미 다른 소스(온라인 프로모션 스케줄 등)에 같은 캠페인이 있으면 스킵(이중 노출 방지).
                        #   ★타입은 건드리지 않는다 — '전사' 꼭지는 프로모션 스케줄 R17~R21(무신사 전사
                        #   레벨=빅세일·무진장·멤버스데이·입점회) 전용이라는 게 사용자 정의(2026-07-28).
                        # ★부분일치 기준을 레인별로 다르게: 전사급 레인(쇼케이스/캠페인)은 4자,
                        #   품목 레인(맨/우먼/키즈/홈/라이브커머스)은 6자 — '쿨탠다드'·'밀리터리' 같은
                        #   짧은 공통어로 성격이 다른 액션끼리 잘못 합쳐지는 것 방지.
                        _mlen = 4 if _norm(_lane) in ("쇼케이스", "캠페인") else 6
                        _dup = next((x for x in _items
                                     if x["type"] in ("전사", "캠페인", "온라인", "기획전")
                                     and abs((_dt.date.fromisoformat(x["date"]) - _dd).days) <= 7
                                     and (_norm(x["title"]) == _nv
                                          or (len(min(_nv, _norm(x["title"]), key=len)) >= _mlen
                                              and (_nv in _norm(x["title"]) or _norm(x["title"]) in _nv)))), None)
                        if _dup:
                            _n_sdup += 1
                            continue
                        if _add("전사", "전사", _iso, _v, _lane, source="MKT",
                                sales_lane=_lane, end=_sales_end(_v, _dd)):
                            _n_sales += 1
            else:
                _HEALTH.append("MKT '주요 세일즈 캠페인' 섹션 못 찾음 — A열 라벨 확인")

        print(f"IMC MKT calendar 로드: 캠페인 신규 {_n_camp}·보강 {_n_enrich}건 + 에너지/바이럴 {_n_energy}건 "
              f"+ 세일즈 캠페인 신규 {_n_sales}·중복스킵 {_n_sdup}건 (기획중 {_n_plan}건 제외)")
        if _n_camp == 0 and _n_enrich == 0 and _n_energy == 0:
            _HEALTH.append("MKT calendar 0건 — 구조변경/권한 확인")
        if _n_sales + _n_sdup == 0:
            _HEALTH.append("MKT 세일즈 캠페인 0건 — 섹션 구조 확인")
    except Exception as _emkt:
        _HEALTH.append(f"MKT calendar 로드 예외: {type(_emkt).__name__}")
        print(f"[주의] MKT calendar 로드 실패(기존 소스만 유지): {type(_emkt).__name__}: {_emkt}")

    # 3) 히어로 별칭 자동생성(#4) + 각 항목 hero_related 태깅
    #    판별: 발매(정의상 히어로) / 제목에 '히어로' 명시(2)일정의 '히어로_' 프리픽스 등) / 26FW 제품명 키워드.
    #    까다로운 품목만 수동 override, 나머지는 히어로명에서 자동 생성 → 품목 바뀌면 자동 반영.
    _ALIAS_OVERRIDE = {
        "커브드팬츠": ["커브드팬츠", "커브드 팬츠", "커브드 데님"],
        # ★맨 '플리스'는 두 플리스 히어로에 다 걸려 IMC 값이 똑같이 나왔다(27건 전부 중복).
        #   사용자 정의(2026-07-28): 플러피/폴라 플리스 = 에센셜 플리스, 그리드·메시 = 그리드/메시 플리스.
        "그리드/메시 플리스": ["그리드", "메시 플리스"],
        "에센셜 플리스": ["에센셜 플리스", "플러피", "폴라 플리스"],
        "심리스 브라": ["심리스 브라", "심리스브라"],
        "라이트다운": ["라이트다운", "라이트 다운"],
        "헤비다운": ["헤비다운", "헤비 다운"],
    }
    # 제외 키워드 — 별칭이 걸려도 이 말이 있으면 그 히어로가 아니다.
    #   '그리드 폴라 플리스'가 에센셜의 '폴라 플리스'에 걸리는 것을 막는다(그리드/메시가 정답).
    _ALIAS_EXCLUDE = {
        "에센셜 플리스": ["그리드", "메시"],
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

    # 3b) MKT 주요 세일즈 캠페인 중 히어로가 제목에 명시된 건('빅토리아울ⅹ스토커즈' 등)은 '전사'가 아니라
    #     그 히어로 딱지로 — 프론트 별칭 매칭(HERO_IMC_ALIASES)이 제목을 보고 히어로에 붙인다.
    #     '전사'는 히어로 필터를 우회(=모든 히어로에 노출)하므로 히어로 캠페인에 달면 오히려 틀림.
    #     ★전사 꼭지 = 프로모션 스케줄 '26년 캠페인 스케줄' R17~R21 전용(사용자 지시 2026-07-28).
    #     히어로가 안 걸리는 세일즈 캠페인(FW캠페인·시티레저·슈퍼세일 등)만 '전사'로 남겨 항상 노출.
    _n_shero = 0
    for x in _items:
        if x.get("sales_lane") and x["type"] == "전사" and x["hero_related"]:
            x["type"] = x["channel"] = "캠페인"
            _n_shero += 1
    if _items:
        print(f"세일즈 캠페인 히어로 딱지 전환: {_n_shero}건 (나머지는 '전사' 유지)")

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
    # ★발매 0건 가드(2026-07-27). 발매 소스(무탠 아이템마스터)와 폴백(발매스케줄)이 둘 다 실패하면
    #   (2026-07-27 09:02 CI: Sheets 429 연쇄) 발매 이벤트 0건이 조용히 주입돼 IMC 발매가 통째로 사라진다.
    #   IG/CRM/히어로PMKT 가드와 동일 철학 — 0건이면 앱 HTML의 직전 발매 항목을 보존(다음 정상 실행 때 자동 갱신).
    if not any(x["type"] == "발매" for x in _items):
        try:
            _mimc = re.search(r"const IMC = (\{.*?\});", html2, re.DOTALL)
            _prev_rel = [x for x in (json.loads(_mimc.group(1)).get("items") or []) if x.get("type") == "발매"] if _mimc else []
        except Exception:
            _prev_rel = []
        _prev_rel = [x for x in _prev_rel if _back <= str(x.get("date", "")) <= _fwd]   # 오늘 윈도우로 재필터
        if _prev_rel:
            for x in _prev_rel:                                  # 보존분도 오늘 기준으로 과거/오늘/미래 재계산
                x["status"] = "past" if x["date"] < _t else ("today" if x["date"] == _t else "future")
            _items = sorted(_items + _prev_rel, key=lambda x: x["date"])
            _HEALTH.append(f"발매 이벤트 0건 → 기존값 보존({len(_prev_rel)}건)")
            print(f"[보존] 발매 이벤트 0건 — 앱 기존값 유지({len(_prev_rel)}건)")

    imc_block = "const IMC = " + json.dumps({"as_of": _t, "items": _items}, ensure_ascii=False) + ";"
    # 람다 치환 — 치환문자열의 \n·\g 등 백슬래시 이스케이프 처리 방지(값에 \ 남아도 안전)
    html2, nimc = re.subn(r"const IMC = \{.*?\};", lambda _m: imc_block, html2, count=1, flags=re.DOTALL)
    assert nimc == 1, f"IMC 교체 실패 (matched {nimc})"
    _np = sum(1 for x in _items if x["status"] == "past")
    _nh = sum(1 for x in _items if x["hero_related"])
    print(f"IMC 주입: {len(_items)}건 (과거 {_np}/미래 {len(_items) - _np} · 히어로관련 {_nh}/{len(_items)} · 운영히어로 {len(_cur_heroes)}종: {_cur_heroes})")

    _excl = {k: v for k, v in _ALIAS_EXCLUDE.items() if k in _hero_alias}
    _excl_block = "const HERO_IMC_EXCLUDE = " + json.dumps(_excl, ensure_ascii=False) + ";"
    html2, _nex = re.subn(r"const HERO_IMC_EXCLUDE = \{.*?\};", lambda _m: _excl_block, html2, count=1, flags=re.DOTALL)
    if _nex != 1:
        print(f"[주의] HERO_IMC_EXCLUDE 교체 실패(matched {_nex}) — 앱 기본값 유지")
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
        _sperf = _src("sns_perf") or SNS_SHEET_ID   # sns_perf 소스키 오버라이드(현재 SNS 시트와 동일)
        tabs = _match_tabs(key, sid=_sperf)
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
                                  optional=("form", "popular", "hero"), sid=_sperf)
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
    rows, cm = _sns_table("시트16", {"ch": ["채널"], "sends": ["발송수"], "gmv": ["GMV"], "roas": ["ROAS"]},
                          sid=_src("crm_perf") or SNS_SHEET_ID)
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
    rows, cm = _sns_table("PMKT/CRM 예산", _bkeys, last_col="P", max_row=40,
                          sid=_src("budget") or SNS_SHEET_ID)
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

    # ★26FW 누계는 달력 YTD(1/1~)가 아니라 시즌 누계 FWTD(7/1~)를 쓴다(사용자 확정 2026-07-28).
    #   달력 YTD로 보면 캐리오버 STY의 봄 판매가 26FW 실적으로 잡혀(커브드 36.7억 중 대부분이 1~6월분)
    #   실제 FW 판매를 못 본다. 26SS(hero_perf)는 그대로 YTD.
    #   탭이 아직 없으면(노트북 반영 전) YTD로 폴백 — 조용히 0으로 떨어지지 않게.
    _FW_YTD_TAB, _FW_YTD_LY_TAB = "FWTD", "전년FWTD"
    try:
        read_tab(sheets, _SALES_ID, _FW_YTD_TAB, max_row=2)
    except Exception:
        _HEALTH.append("FWTD 탭 없음 — 26FW 누계를 달력 YTD로 폴백(노트북 잡 완료 후 자동 정상화)")
        _FW_YTD_TAB, _FW_YTD_LY_TAB = "YTD", "전년YTD"
    _fw_tab = lambda per: (_FW_YTD_TAB if per == "YTD" else per)                 # noqa: E731
    _fw_tab_ly = lambda per: (_FW_YTD_LY_TAB if per == "YTD" else "전년" + per)   # noqa: E731

    def _num(v):   # read_tab은 UNFORMATTED_VALUE라 숫자를 실제 number로 줌 → float 직접(★_n은 float ".0"를 ×10로 깨뜨림)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    _PATH_NAMES = []          # 주차 스냅샷 경로 사전(아래 try 안에서 채움. 실패해도 emit이 죽지 않게 기본값)
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
            _fwm = _FW_HERO_MAP if _FW_HERO_MAP is not None else load_26fw_hero_goods(sheets, sid=_src("mstrd"))   # 발매필터서 이미 로드했으면 재사용
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

        # ★26FW 마케팅 성과(IMC 성과 탭) — 26SS와 같은 모양으로 한 벌 더 만든다.
        #   MSTRD `HERO STY`가 진실소스라 히어로/스타일이 추가되면 다음 실행부터 자동으로 붙는다.
        hero_sty_pm_fw = {}       # hero → {품번: {기간: {...}}}  (26SS hero_sty와 동일 스키마)
        _hero_wk_fw, _sty_wk_fw, _path_wk_fw = {}, {}, {}
        _sales_wk_fw = {"cur": {}, "prev": {}}
        _sales_wk_sty_fw = {"cur": {}, "prev": {}}

        def _hsty_fw(hero, base, per):
            return hero_sty_pm_fw.setdefault(hero, {}).setdefault(base, {}).setdefault(
                per, {"pdp": 0, "buy": 0, "gmv": 0, "pdp_ly": 0, "buy_ly": 0, "gmv_ly": 0,
                      "mkt_gmv": 0, "mkt_pdp": 0, "mkt_gmv_ly": 0, "mkt_pdp_ly": 0,
                      "sales_gmv": 0, "sales_gmv_ly": 0})

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
                      "mkt_gmv": 0, "mkt_pdp": 0, "mkt_gmv_ly": 0, "mkt_pdp_ly": 0,
                      "sales_gmv": 0, "sales_gmv_ly": 0})   # 누판(실판매가) — 표시용 거래액. gmv(direct)는 마케팅기여 분모 전용.

        # (1a) 성과 GMV = 실적 누판(gmv=실판매가) — 매출 YTD/MTD/WEEK 탭을 신품번→히어로로 롤업.
        #      PMKT의 gmv는 직접경로 어트리뷰션이라 실적보다 작음 → 헤드라인 GMV엔 누판을 씀.
        for _per in _PERIODS:
            for r in read_tab(sheets, _SALES_ID, _per):
                hero = _hero_of(r.get("style_no"), r.get("goods_no"))
                if hero:
                    _g26 = round(_num(r.get("gmv")))
                    _perd(hero, _per)["gmv"] += _g26
                    # STY별 누판 — 성과탭 드릴다운 '거래액'을 히어로 행과 같은 누판 기준으로 통일(직접경로 표시 폐기).
                    #   style_no 빈칸(uid 폴백 매칭)은 STY로 못 갈라 히어로 합에만 반영(STY합≈히어로, 미세 차이 허용).
                    _sb26 = str(r.get("style_no") or "").split("-")[0].strip()
                    if _sb26:
                        _hsty(hero, _sb26, _per)["sales_gmv"] += _g26
        # 거래액 전주비(WoW)를 '화면에 찍히는 값과 같은 기준'인 실적 누판으로 산출하기 위한 주간 2개.
        #   기존엔 PMKT 직접경로 주간(wow.gmv)으로 냈는데 표시값은 누판이라 기준이 어긋났다.
        #   WEEK=최근 7일 / 직전WEEK=그 직전 7일(노트북 params). 같은 7일 폭이라 정규화 불필요.
        _sales_wk = {"cur": {}, "prev": {}}          # {when: {hero: gmv}}
        _sales_wk_sty = {"cur": {}, "prev": {}}      # {when: {(hero, base): gmv}}
        for _when, _tab in (("cur", "WEEK"), ("prev", "직전WEEK")):
            try:
                for r in read_tab(sheets, _SALES_ID, _tab):
                    # 26FW도 같은 주간 탭에서 롤업(FW는 누계만 FWTD 별도 탭이고 주간 탭은 공용).
                    _hfw_w = _hero_of_fw(r.get("style_no"), r.get("goods_no"))
                    if _hfw_w:
                        _gwf = round(_num(r.get("gmv")))
                        _sales_wk_fw[_when][_hfw_w] = _sales_wk_fw[_when].get(_hfw_w, 0) + _gwf
                        _bwf = _base_of(r.get("style_no"), r.get("goods_no"))
                        if _bwf:
                            _kf = (_hfw_w, _bwf)
                            _sales_wk_sty_fw[_when][_kf] = _sales_wk_sty_fw[_when].get(_kf, 0) + _gwf
                    hero = _hero_of(r.get("style_no"), r.get("goods_no"))
                    if not hero:
                        continue
                    _gw = round(_num(r.get("gmv")))
                    _sales_wk[_when][hero] = _sales_wk[_when].get(hero, 0) + _gw
                    _sbw = str(r.get("style_no") or "").split("-")[0].strip()
                    if _sbw:
                        _k = (hero, _sbw)
                        _sales_wk_sty[_when][_k] = _sales_wk_sty[_when].get(_k, 0) + _gw
            except Exception as _esw:
                _HEALTH.append(f"{_tab} 로드 실패({type(_esw).__name__}) — 거래액 전주비 '–' 표시")

        # 26FW 롤업 — 누계만 FWTD(7/1~) 탭에서 읽는다(위 26SS 루프와 분리한 이유).
        for _per in _PERIODS:
            for r in read_tab(sheets, _SALES_ID, _fw_tab(_per)):
                hero_fw = _hero_of_fw(r.get("style_no"), r.get("goods_no"))
                if not hero_fw:
                    continue
                _dfw = _perd_fw(hero_fw, _per)
                _g0, _q0 = round(_num(r.get("gmv"))), round(_num(r.get("qty")))
                _dfw["gmv"] += _g0
                _dfw["qty"] += _q0
                _b = _base_of(r.get("style_no"), r.get("goods_no"))
                if _b:
                    _sd0 = _styd_fw(hero_fw, _b, _per)
                    _sd0["gmv"] += _g0
                    _sd0["qty"] += _q0
                    _hsty_fw(hero_fw, _b, _per)["sales_gmv"] += _g0   # 성과탭 드릴다운 거래액(누판 기준 통일)
        # 26FW 전년 동기간(YoY 분모) — 누계는 전년FWTD(전년 7/1~), 나머지는 전년MTD/전년WEEK.
        for _per in _PERIODS:
            try:
                for r in read_tab(sheets, _SALES_ID, _fw_tab_ly(_per)):
                    hero_fw = _hero_of_fw(r.get("style_no"), r.get("goods_no"))
                    if hero_fw:
                        _gflyv = round(_num(r.get("gmv")))
                        _perd_fw(hero_fw, _per)["gmv_ly"] += _gflyv
                        _bfly = _base_of(r.get("style_no"), r.get("goods_no"))
                        if _bfly:
                            _hsty_fw(hero_fw, _bfly, _per)["sales_gmv_ly"] += _gflyv
            except Exception as _elyf:
                _HEALTH.append(f"{_fw_tab_ly(_per)} 로드 실패({type(_elyf).__name__}) — 26FW 거래액 YoY 미표시")
        # 26SS 전년 동기간(YoY 분모) — 전년YTD/전년MTD/전년WEEK.
        for _per in _PERIODS:
            try:
                for r in read_tab(sheets, _SALES_ID, "전년" + _per):
                    hero = _hero_of(r.get("style_no"), r.get("goods_no"))   # 26SS 성과탭 거래액 YoY
                    if hero:
                        _gly26 = round(_num(r.get("gmv")))
                        _perd(hero, _per)["gmv_ly"] += _gly26
                        _sbly = str(r.get("style_no") or "").split("-")[0].strip()
                        if _sbly:
                            _hsty(hero, _sbly, _per)["sales_gmv_ly"] += _gly26   # STY 누판 전년(드릴다운 거래액 YoY)
            except Exception as _ely:
                _HEALTH.append(f"전년{_per} 로드 실패({type(_ely).__name__}) — 거래액 YoY 미표시")
        # (1b) PMKT기간 — 퍼널 지표(전환=buy_uv/pdp_uv · 마케팅기여=mkt_gmv/mkt_pdp_uv, 캠페인기획전+외부유입)
        #      + 마케팅기여율 분모용 pmkt_gmv(직접경로 GMV). 헤드라인 GMV는 위 누판을 쓰므로 여기 gmv는 pmkt_gmv로만.
        # ★26FW 누계 기간 정합 — 매출은 FWTD(7/1~)인데 PMKT는 달력 YTD(1/1~)라 그대로 섞으면 어긋난다.
        #   탭에 FWTD 행이 있으면 그걸 누계 슬롯으로 쓰고, 없으면 26FW 누계 유입은 비운다(월누계·주간은 정상).
        _pmkt_rows = read_tab(sheets, _SALES_ID, "PMKT기간")
        _PMKT_FWTD = any(str(r.get("period") or "").strip() == "FWTD" for r in _pmkt_rows)
        if not _PMKT_FWTD:
            _HEALTH.append("PMKT기간에 FWTD 없음 — 26FW 누계 유입/전환은 '–'(월누계·주간은 정상)")

        def _fw_slot(per):
            """26FW 성과 슬롯 — FWTD→누계(YTD). FWTD가 없으면 달력 YTD는 버린다(기간 불일치)."""
            if per == "FWTD":
                return "YTD"
            if per == "YTD" and not _PMKT_FWTD:
                return None
            return per if per in _PERIODS else None

        for r in _pmkt_rows:
            per = str(r.get("period") or "").strip()
            hero_fw = _hero_of_fw(r.get("style_no"), r.get("goods_no"))   # 26FW 기준 퍼널 지표
            _slot_fw = _fw_slot(per)
            if hero_fw and _slot_fw:
                per = _slot_fw
            if hero_fw and _slot_fw:
                dfw = _perd_fw(hero_fw, per)
                dfw["pmkt_gmv"] += round(_num(r.get("gmv")))
                dfw["pdp_real"] += round(_num(r.get("pdp_uv")))
                dfw["conv"] += round(_num(r.get("buy_uv")))
                dfw["ad_gmv"] += round(_num(r.get("mkt_gmv")))
                dfw["pdp_ad"] += round(_num(r.get("mkt_pdp_uv")))
                # 전년(YoY 분모) — 26FW는 대부분 신규 품번이라 0(프론트서 '–'). 캐리오버는 실제 값이 붙는다.
                dfw["pdp_real_ly"] = dfw.get("pdp_real_ly", 0) + round(_num(r.get("pdp_uv_ly")))
                dfw["conv_ly"] = dfw.get("conv_ly", 0) + round(_num(r.get("buy_uv_ly")))
                dfw["pmkt_gmv_ly"] = dfw.get("pmkt_gmv_ly", 0) + round(_num(r.get("gmv_ly")))
                dfw["ad_gmv_ly"] = dfw.get("ad_gmv_ly", 0) + round(_num(r.get("mkt_gmv_ly")))
                dfw["pdp_ad_ly"] = dfw.get("pdp_ad_ly", 0) + round(_num(r.get("mkt_pdp_uv_ly")))
                # STY 드릴다운(26SS와 동일 스키마) — 품번은 MSTRD 등록 품번으로 통일(_base_of).
                _sbf = _base_of(r.get("style_no"), r.get("goods_no"))
                if _sbf:
                    sf = _hsty_fw(hero_fw, _sbf, per)
                    sf["pdp"] += round(_num(r.get("pdp_uv")))
                    sf["buy"] += round(_num(r.get("buy_uv")))
                    sf["gmv"] += round(_num(r.get("gmv")))
                    sf["pdp_ly"] += round(_num(r.get("pdp_uv_ly")))
                    sf["buy_ly"] += round(_num(r.get("buy_uv_ly")))
                    sf["gmv_ly"] += round(_num(r.get("gmv_ly")))
                    sf["mkt_gmv"] += round(_num(r.get("mkt_gmv")))
                    sf["mkt_pdp"] += round(_num(r.get("mkt_pdp_uv")))
                    sf["mkt_gmv_ly"] += round(_num(r.get("mkt_gmv_ly")))
                    sf["mkt_pdp_ly"] += round(_num(r.get("mkt_pdp_uv_ly")))
            per = str(r.get("period") or "").strip()      # ★26SS는 원본 period 그대로(위에서 FW 슬롯으로 덮었을 수 있음)
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
        _wk_range = {}          # {(yyyy,week): (시작 YYYY-MM-DD, 종료)} — 주차 스냅샷 라벨용
        for r in read_tab(sheets, _SALES_ID, "PMKT주차"):
            _hfw_k = _hero_of_fw(r.get("style_no"), r.get("goods_no"))
            if _hfw_k:
                try:
                    _kf2 = (int(_num(r.get("yyyy"))), int(_num(r.get("week_no"))))
                except (TypeError, ValueError):
                    _kf2 = None
                if _kf2:
                    _wk_keys.add(_kf2)
                    WF = _hero_wk_fw.setdefault(_hfw_k, {}).setdefault(_kf2, {"gmv": 0, "pdp": 0, "buy": 0, "mkt_gmv": 0, "mkt_pdp": 0})
                    WF["gmv"] += round(_num(r.get("gmv")))
                    WF["pdp"] += round(_num(r.get("pdp_uv")))
                    WF["buy"] += round(_num(r.get("buy_uv")))
                    WF["mkt_gmv"] += round(_num(r.get("mkt_gmv")))
                    WF["mkt_pdp"] += round(_num(r.get("mkt_pdp_uv")))
                    _swbf = _base_of(r.get("style_no"), r.get("goods_no"))
                    if _swbf:
                        SWF = _sty_wk_fw.setdefault(_hfw_k, {}).setdefault(_swbf, {}).setdefault(_kf2, {"pdp": 0, "buy": 0, "gmv": 0, "mkt_gmv": 0, "mkt_pdp": 0})
                        SWF["pdp"] += round(_num(r.get("pdp_uv")))
                        SWF["buy"] += round(_num(r.get("buy_uv")))
                        SWF["gmv"] += round(_num(r.get("gmv")))
                        SWF["mkt_gmv"] += round(_num(r.get("mkt_gmv")))
                        SWF["mkt_pdp"] += round(_num(r.get("mkt_pdp_uv")))
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
            _wk_range.setdefault(_key, (str(r.get("week_start") or "")[:10], str(r.get("week_end") or "")[:10]))
            # 주 일수(span) — 소스 주 경계가 불규칙(W29=1일, W28=5일 등, 데이터 경계로 잘림).
            #   진행중(1일짜리) 주는 WoW에서 제외하고, 남은 주는 '일평균'으로 정규화해 공정 비교.
            if _key not in _wk_span:
                try:
                    _ws2 = datetime.date.fromisoformat(str(r.get("week_start"))[:10])
                    _we2 = datetime.date.fromisoformat(str(r.get("week_end"))[:10])
                    _wk_span[_key] = (_we2 - _ws2).days + 1
                except (ValueError, TypeError):
                    _wk_span[_key] = 7
        # (3) 유입경로(prev_path1) x 기간 — 온사이트 경로 구성·전환율·유입 전년비. PMKT경로기간 탭(노트북 산출).
        #   히어로별 {period: {path: {pdp,buy,pdp_ly,buy_ly}}}. 프론트가 비중(pdp/합)·전환율(buy/pdp)·전년비(pdp/pdp_ly) 계산.
        try:
            for r in read_tab(sheets, _SALES_ID, "PMKT경로기간"):
                _phero = _hero_of(None, r.get("goods_no"))
                _pper = str(r.get("period") or "").strip()
                _ppath = str(r.get("path") or "").strip()
                _pfw = _hero_of_fw(None, r.get("goods_no"))
                _pslot = _fw_slot(_pper)                      # 26FW 경로도 누계=FWTD 기준으로
                if _pfw and _pslot and _ppath:
                    _pbf = hero_perf_fw.setdefault(_pfw, {"periods": {}, "season": "26FW"}).setdefault("paths", {}).setdefault(_pslot, {})
                    _pcf = _pbf.setdefault(_ppath, {"pdp": 0, "buy": 0, "pdp_ly": 0, "buy_ly": 0})
                    _pcf["pdp"] += round(_num(r.get("pdp_uv")))
                    _pcf["buy"] += round(_num(r.get("buy_uv")))
                    _pcf["pdp_ly"] += round(_num(r.get("pdp_uv_ly")))
                    _pcf["buy_ly"] += round(_num(r.get("buy_uv_ly")))
                if not _phero or _pper not in _PERIODS or not _ppath:
                    continue
                _pbk = hero_perf.setdefault(_phero, {"periods": {}, "season": _HERO_SEASON}).setdefault("paths", {}).setdefault(_pper, {})
                _pc = _pbk.setdefault(_ppath, {"pdp": 0, "buy": 0, "pdp_ly": 0, "buy_ly": 0})
                _pc["pdp"] += round(_num(r.get("pdp_uv")))
                _pc["buy"] += round(_num(r.get("buy_uv")))
                _pc["pdp_ly"] += round(_num(r.get("pdp_uv_ly")))
                _pc["buy_ly"] += round(_num(r.get("buy_uv_ly")))
        except Exception as _epath:
            _HEALTH.append(f"PMKT경로기간 로드 실패({type(_epath).__name__}) — 유입경로 뷰 미표시")

        # (3b) 유입경로 x 주차 — 경로별 전주비(WoW) + ★주차 스냅샷(과거 주차 회고) 원천.
        #   히어로 행 WoW와 '같은 주차 키·같은 일평균 정규화'를 써야 화면에서 기준이 어긋나지 않는다.
        #   ★2026-08-01: 탭이 goods 단위 최근 12주 → **히어로 x 시즌 x 경로 x 주차(2025-01-01~)** 로 바뀌었다.
        #   (goods x 경로 x 83주는 40만행이라 시트에 못 싣는다.) 옛 포맷(goods_no 열)도 그대로 읽는다 —
        #   노트북 잡이 아직 새 셀로 안 돌았을 때 조용히 비지 않게.
        _path_wk = {}          # {hero: {path: {(yyyy,week): {pdp,buy,gmv}}}}
        _pw_label, _pw_span = {}, {}
        try:
            for r in read_tab(sheets, _SALES_ID, "PMKT경로주차"):
                _pp = str(r.get("path") or "").strip()
                if not _pp:
                    continue
                try:
                    _pk = (int(_num(r.get("yyyy"))), int(_num(r.get("week_no"))))
                except (TypeError, ValueError):
                    continue
                _pv = {"pdp": round(_num(r.get("pdp_uv"))), "buy": round(_num(r.get("buy_uv"))),
                       "gmv": round(_num(r.get("gmv")))}
                if _pk not in _pw_label:
                    _ws3, _we3 = str(r.get("week_start") or "")[:10], str(r.get("week_end") or "")[:10]
                    _pw_label[_pk] = (_ws3, _we3)
                    try:
                        _pw_span[_pk] = (datetime.date.fromisoformat(_we3) - datetime.date.fromisoformat(_ws3)).days + 1
                    except (ValueError, TypeError):
                        _pw_span[_pk] = 7

                def _acc(_tgt, _hero):
                    _c = _tgt.setdefault(_hero, {}).setdefault(_pp, {}).setdefault(_pk, {"pdp": 0, "buy": 0, "gmv": 0})
                    for _mk, _mv in _pv.items():
                        _c[_mk] += _mv

                _hname = str(r.get("hero") or "").strip()
                if _hname:                                   # 새 포맷 — 히어로·시즌이 원천에 들어 있다
                    if str(r.get("season") or "").strip() == "26FW":
                        _acc(_path_wk_fw, _hname)
                    else:
                        _acc(_path_wk, _hname)
                    continue
                _phf = _hero_of_fw(None, r.get("goods_no"))  # 옛 포맷 — goods를 매핑으로 롤업
                if _phf:
                    _acc(_path_wk_fw, _phf)
                _ph = _hero_of(None, r.get("goods_no"))
                if _ph:
                    _acc(_path_wk, _ph)
        except Exception as _epw:
            _HEALTH.append(f"PMKT경로주차 로드 실패({type(_epw).__name__}) — 유입경로 전주비 '–' 표시")

        # (3c) 유입경로 상세(중분류 prev_path2) x 기간 — PMKT경로상세 탭(노트북 (5)셀, 2026-08-01 신설).
        #   대분류만 보면 온라인팀 기획전 유입이 '메인-세일/발매'·'기타'로 흩어져 안 보인다(사용자 지적).
        #   {hero: {period: {대분류: {중분류: {pdp,buy,pdp_ly,buy_ly}}}}}
        _path_dtl, _path_dtl_fw = {}, {}
        try:
            for r in read_tab(sheets, _SALES_ID, "PMKT경로상세"):
                _dh = str(r.get("hero") or "").strip()
                _dp1 = str(r.get("path") or "").strip()
                _dp2 = str(r.get("path2") or "").strip() or "기타"
                _dper = str(r.get("period") or "").strip()
                if not _dh or not _dp1 or not _dper:
                    continue
                _isfw = str(r.get("season") or "").strip() == "26FW"
                _slot = _fw_slot(_dper) if _isfw else (_dper if _dper in _PERIODS else None)
                if not _slot:
                    continue
                _dt_tgt = (_path_dtl_fw if _isfw else _path_dtl)
                _dc = _dt_tgt.setdefault(_dh, {}).setdefault(_slot, {}).setdefault(_dp1, {}).setdefault(
                    _dp2, {"pdp": 0, "buy": 0, "pdp_ly": 0, "buy_ly": 0})
                _dc["pdp"] += round(_num(r.get("pdp_uv")))
                _dc["buy"] += round(_num(r.get("buy_uv")))
                _dc["pdp_ly"] += round(_num(r.get("pdp_uv_ly")))
                _dc["buy_ly"] += round(_num(r.get("buy_uv_ly")))
        except Exception as _edt:
            _HEALTH.append(f"PMKT경로상세 로드 실패({type(_edt).__name__}) — 경로 중분류 미표시")

        # (3d) 상품 퍼널(노출·클릭·CTR) — 상품퍼널기간 탭(노트북 (6)셀, 2026-08-01 신설).
        #   공식 온사이트 대시보드와 같은 재료(goods_funnel_daily). CTR=클릭/노출.
        #   히어로/STY 롤업은 PMKT기간과 같은 매핑을 쓴다(26SS=_hero_of · 26FW=_hero_of_fw).
        _GFK = ("imp", "clk", "gv", "gv_uv", "imp_ly", "clk_ly", "gv_ly", "gv_uv_ly")

        def _gf_cell(box, key):
            return box.setdefault(key, {k: 0 for k in _GFK})

        try:
            for r in read_tab(sheets, _SALES_ID, "상품퍼널기간"):
                _fper = str(r.get("period") or "").strip()
                _vals = {k: round(_num(r.get(k))) for k in _GFK}
                _fh = _hero_of(None, r.get("goods_no"))
                if _fh and _fper in _PERIODS:
                    _c = _gf_cell(hero_perf.setdefault(_fh, {"periods": {}, "season": _HERO_SEASON}).setdefault("gf", {}), _fper)
                    for k in _GFK:
                        _c[k] += _vals[k]
                _ffw = _hero_of_fw(None, r.get("goods_no"))
                _fslot = _fw_slot(_fper)
                if _ffw and _fslot:
                    _c2 = _gf_cell(hero_perf_fw.setdefault(_ffw, {"periods": {}, "season": "26FW"}).setdefault("gf", {}), _fslot)
                    for k in _GFK:
                        _c2[k] += _vals[k]
        except Exception as _egf:
            _HEALTH.append(f"상품퍼널기간 로드 실패({type(_egf).__name__}) — 노출·클릭·CTR 미표시")

        # (3e) 조회자 성·연령 — 상품성연령 탭(히어로 x 기간 x 성별 x 연령대).
        try:
            for r in read_tab(sheets, _SALES_ID, "상품성연령"):
                _dh2 = str(r.get("hero") or "").strip()
                _dper2 = str(r.get("period") or "").strip()
                if not _dh2 or not _dper2:
                    continue
                _isfw2 = str(r.get("season") or "").strip() == "26FW"
                _slot2 = _fw_slot(_dper2) if _isfw2 else (_dper2 if _dper2 in _PERIODS else None)
                if not _slot2:
                    continue
                _tgt2 = hero_perf_fw if _isfw2 else hero_perf
                _P2 = _tgt2.setdefault(_dh2, {"periods": {}, "season": ("26FW" if _isfw2 else _HERO_SEASON)})
                _row2 = _P2.setdefault("demo", {}).setdefault(_slot2, {})
                _k2 = (str(r.get("gender") or "미상").strip(), str(r.get("age_group") or "미상").strip())
                _cell2 = _row2.setdefault(_k2, {"imp": 0, "clk": 0, "uv": 0, "uv_ly": 0})
                _cell2["imp"] += round(_num(r.get("imp")))
                _cell2["clk"] += round(_num(r.get("clk")))
                _cell2["uv"] += round(_num(r.get("gv_uv")))
                _cell2["uv_ly"] += round(_num(r.get("gv_uv_ly")))
        except Exception as _edm:
            _HEALTH.append(f"상품성연령 로드 실패({type(_edm).__name__}) — 성·연령 구성 미표시")

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
                # 거래액 전주비 전용 — 실적 누판 기준(WEEK vs 직전WEEK). 화면 거래액과 같은 기준.
                "sales_gmv": _sales_wk["cur"].get(hero, 0), "sales_gmv_p": _sales_wk["prev"].get(hero, 0),
            }
            # 유입 경로 레인 전주비 — 경로별 최근주/직전주(일평균). 기간과 무관하므로 모든 기간 블록에 동일 주입.
            #   pdp_w/pdp_p 를 짝으로 넣는다(pdp는 기간 누계라 전주비 분자로 쓰면 안 됨).
            _hpw = _path_wk.get(hero, {})
            for _per_b in (P.get("paths") or {}).values():
                for _pname, _pcell in _per_b.items():
                    _wc = (_hpw.get(_pname, {}).get(_cur_k, {}) if _cur_k else {})
                    _wp = (_hpw.get(_pname, {}).get(_prev_k, {}) if _prev_k else {})
                    _pcell["pdp_w"] = round(_wc.get("pdp", 0) / _cd)
                    _pcell["pdp_p"] = round(_wp.get("pdp", 0) / _pd)
                    _pcell["buy_w"] = round(_wc.get("buy", 0) / _cd)
                    _pcell["buy_p"] = round(_wp.get("buy", 0) / _pd)
        # STY 드릴다운 배열을 각 히어로 P에 주입(유입순 상위, 잡음 제거 위해 pdp>0만)
        for hero, P in hero_perf.items():
            _stys = []
            _swh = _sty_wk.get(hero, {})
            for _b, _pers in hero_sty.get(hero, {}).items():
                _y = _pers.get("YTD", {})
                # 유입 또는 누판 거래액이 있으면 포함 — 거래액=누판 통일 후 STY합≈히어로 행이 되도록
                #   (PMKT 미등장·매출만 있는 STY도 노출, 유입 지표는 '-').
                if (_y.get("pdp", 0) or 0) <= 0 and (_y.get("sales_gmv", 0) or 0) <= 0:
                    continue
                # STY 전주비 — 히어로와 동일하게 최근 완료주 vs 직전주(일평균 정규화). PDP·전환·마케팅기여·유입기여.
                _swc = (_swh.get(_b, {}).get(_cur_k, {}) if _cur_k else {})
                _swp = (_swh.get(_b, {}).get(_prev_k, {}) if _prev_k else {})
                _stys.append({"style": _b,
                    "wow": {"pdp": round(_swc.get("pdp", 0) / _cd), "pdp_p": round(_swp.get("pdp", 0) / _pd),
                            "buy": round(_swc.get("buy", 0) / _cd), "buy_p": round(_swp.get("buy", 0) / _pd),
                            "gmv": round(_swc.get("gmv", 0) / _cd), "gmv_p": round(_swp.get("gmv", 0) / _pd),
                            "mkt_gmv": round(_swc.get("mkt_gmv", 0) / _cd), "mkt_gmv_p": round(_swp.get("mkt_gmv", 0) / _pd),
                            "mkt_pdp": round(_swc.get("mkt_pdp", 0) / _cd), "mkt_pdp_p": round(_swp.get("mkt_pdp", 0) / _pd),
                            "sales_gmv": _sales_wk_sty["cur"].get((hero, _b), 0),
                            "sales_gmv_p": _sales_wk_sty["prev"].get((hero, _b), 0)},
                    "periods": {
                    _pp: {"pdp": (_pers.get(_pp) or {}).get("pdp", 0), "buy": (_pers.get(_pp) or {}).get("buy", 0),
                          "gmv": (_pers.get(_pp) or {}).get("gmv", 0),
                          "pdp_ly": (_pers.get(_pp) or {}).get("pdp_ly", 0), "buy_ly": (_pers.get(_pp) or {}).get("buy_ly", 0),
                          "gmv_ly": (_pers.get(_pp) or {}).get("gmv_ly", 0),
                          "mkt_gmv": (_pers.get(_pp) or {}).get("mkt_gmv", 0), "mkt_pdp": (_pers.get(_pp) or {}).get("mkt_pdp", 0),
                          "mkt_gmv_ly": (_pers.get(_pp) or {}).get("mkt_gmv_ly", 0), "mkt_pdp_ly": (_pers.get(_pp) or {}).get("mkt_pdp_ly", 0),
                          "sales_gmv": (_pers.get(_pp) or {}).get("sales_gmv", 0), "sales_gmv_ly": (_pers.get(_pp) or {}).get("sales_gmv_ly", 0)}
                    for _pp in _PERIODS}})
            _stys.sort(key=lambda x: -x["periods"]["YTD"]["pdp"])
            P["stys"] = _stys

        # ★26FW 히어로 성과 조립 — 26SS와 같은 주차 키(_cur_k/_prev_k)·같은 일평균 정규화를 쓴다.
        #   periods는 hero_perf_fw(FWTD 누판 + PMKT 퍼널), stys는 hero_sty_pm_fw.
        for _hf, _PF in hero_perf_fw.items():
            _hwf = _hero_wk_fw.get(_hf, {})
            _cf = _hwf.get(_cur_k, {}) if _cur_k else {}
            _pf = _hwf.get(_prev_k, {}) if _prev_k else {}
            _PF["wow"] = {
                "cur_w": f"W{_cur_k[1]}" if _cur_k else "", "prev_w": f"W{_prev_k[1]}" if _prev_k else "",
                "cur_from": _wk_label.get(_cur_k, ""), "prev_from": _wk_label.get(_prev_k, ""),
                "pdp": round(_cf.get("pdp", 0) / _cd), "pdp_p": round(_pf.get("pdp", 0) / _pd),
                "buy": round(_cf.get("buy", 0) / _cd), "buy_p": round(_pf.get("buy", 0) / _pd),
                "gmv": round(_cf.get("gmv", 0) / _cd), "gmv_p": round(_pf.get("gmv", 0) / _pd),
                "mkt_gmv": round(_cf.get("mkt_gmv", 0) / _cd), "mkt_gmv_p": round(_pf.get("mkt_gmv", 0) / _pd),
                "mkt_pdp": round(_cf.get("mkt_pdp", 0) / _cd), "mkt_pdp_p": round(_pf.get("mkt_pdp", 0) / _pd),
                "sales_gmv": _sales_wk_fw["cur"].get(_hf, 0), "sales_gmv_p": _sales_wk_fw["prev"].get(_hf, 0),
            }
            _hpwf = _path_wk_fw.get(_hf, {})
            for _perbf in (_PF.get("paths") or {}).values():
                for _pnf, _pcell2 in _perbf.items():
                    _wcf = (_hpwf.get(_pnf, {}).get(_cur_k, {}) if _cur_k else {})
                    _wpf = (_hpwf.get(_pnf, {}).get(_prev_k, {}) if _prev_k else {})
                    _pcell2["pdp_w"] = round(_wcf.get("pdp", 0) / _cd)
                    _pcell2["pdp_p"] = round(_wpf.get("pdp", 0) / _pd)
                    _pcell2["buy_w"] = round(_wcf.get("buy", 0) / _cd)
                    _pcell2["buy_p"] = round(_wpf.get("buy", 0) / _pd)
            _stysf = []
            _swhf = _sty_wk_fw.get(_hf, {})
            for _bf, _persf in hero_sty_pm_fw.get(_hf, {}).items():
                _yf = _persf.get("YTD", {})
                if (_yf.get("pdp", 0) or 0) <= 0 and (_yf.get("sales_gmv", 0) or 0) <= 0:
                    continue
                _swcf = (_swhf.get(_bf, {}).get(_cur_k, {}) if _cur_k else {})
                _swpf = (_swhf.get(_bf, {}).get(_prev_k, {}) if _prev_k else {})
                _stysf.append({"style": _bf,
                    "wow": {"pdp": round(_swcf.get("pdp", 0) / _cd), "pdp_p": round(_swpf.get("pdp", 0) / _pd),
                            "buy": round(_swcf.get("buy", 0) / _cd), "buy_p": round(_swpf.get("buy", 0) / _pd),
                            "gmv": round(_swcf.get("gmv", 0) / _cd), "gmv_p": round(_swpf.get("gmv", 0) / _pd),
                            "mkt_gmv": round(_swcf.get("mkt_gmv", 0) / _cd), "mkt_gmv_p": round(_swpf.get("mkt_gmv", 0) / _pd),
                            "mkt_pdp": round(_swcf.get("mkt_pdp", 0) / _cd), "mkt_pdp_p": round(_swpf.get("mkt_pdp", 0) / _pd),
                            "sales_gmv": _sales_wk_sty_fw["cur"].get((_hf, _bf), 0),
                            "sales_gmv_p": _sales_wk_sty_fw["prev"].get((_hf, _bf), 0)},
                    "periods": {
                    _ppf: {"pdp": (_persf.get(_ppf) or {}).get("pdp", 0), "buy": (_persf.get(_ppf) or {}).get("buy", 0),
                           "gmv": (_persf.get(_ppf) or {}).get("gmv", 0),
                           "pdp_ly": (_persf.get(_ppf) or {}).get("pdp_ly", 0), "buy_ly": (_persf.get(_ppf) or {}).get("buy_ly", 0),
                           "gmv_ly": (_persf.get(_ppf) or {}).get("gmv_ly", 0),
                           "mkt_gmv": (_persf.get(_ppf) or {}).get("mkt_gmv", 0), "mkt_pdp": (_persf.get(_ppf) or {}).get("mkt_pdp", 0),
                           "mkt_gmv_ly": (_persf.get(_ppf) or {}).get("mkt_gmv_ly", 0), "mkt_pdp_ly": (_persf.get(_ppf) or {}).get("mkt_pdp_ly", 0),
                           "sales_gmv": (_persf.get(_ppf) or {}).get("sales_gmv", 0), "sales_gmv_ly": (_persf.get(_ppf) or {}).get("sales_gmv_ly", 0)}
                    for _ppf in _PERIODS}})
            _stysf.sort(key=lambda x: -x["periods"]["YTD"]["pdp"])
            _PF["stys"] = _stysf

        # ── ★주차 스냅샷(과거 주차 회고) 조립 — 2026-08-01 신설 ─────────────────────
        #   지금까지는 '최근 완료주 vs 직전주'만 있어서 "그 주에 유입이 올랐나"를 돌아볼 수 없었다.
        #   PMKT주차(goods x 주차, 2025-01-01~)와 PMKT경로주차(히어로 x 경로 x 주차, 2025-01-01~)를
        #   히어로별 주차 배열로 접어 넣는다. **26년 주차만 싣고**, 전년 동주차(W번호 일치)는 ly로 붙인다
        #   (= 화면 '26년 1월부터', YoY 비교는 25년 1월분이 짝으로 붙는 구조).
        _WK_FROM_Y = 2026
        _path_names = sorted({p for _m in (_path_wk, _path_wk_fw) for _h in _m.values() for p in _h})
        _PATH_NAMES = _path_names
        _pname_ix = {p: i for i, p in enumerate(_path_names)}

        def _mmdd(s):
            return str(s or "")[5:].replace("-", "/")

        def _weeks_for(hero, wk_map, pw_map):
            """히어로 하나의 주차 배열. [{y,w,f,t,d, pdp,buy,gmv, ly{...}, p:[[경로ix,pdp,buy,gmv,pdp_ly,buy_ly]]}]"""
            _hw = wk_map.get(hero, {})
            _hp = pw_map.get(hero, {})
            _keys = sorted({k for k in _hw if k[0] >= _WK_FROM_Y}
                           | {k for _pm in _hp.values() for k in _pm if k[0] >= _WK_FROM_Y})
            _out = []
            for (_y, _w) in _keys:
                _cur = _hw.get((_y, _w), {})
                _lyk = (_y - 1, _w)
                _ly = _hw.get(_lyk, {})
                # ★ISO 주차 버킷 함정: 12/29~31은 WEEKOFYEAR가 1이라 그 해 'W1'에 1월 초와 함께 섞인다
                #   (원천이 YEAR(d)+WEEKOFYEAR(d)로 이미 접혀 있어 여기서 못 쪼갠다).
                #   전년 W1이 8일 넘게 벌어져 있으면 섞인 버킷 → 전년비를 아예 비운다(부풀린 비교 방지).
                _lrng0 = _wk_range.get(_lyk) or _pw_label.get(_lyk) or ("", "")
                try:
                    if (datetime.date.fromisoformat(_lrng0[1]) - datetime.date.fromisoformat(_lrng0[0])).days + 1 > 9:
                        _ly = {}
                except (ValueError, TypeError):
                    pass
                _rng = _wk_range.get((_y, _w)) or _pw_label.get((_y, _w)) or ("", "")
                _lrng = _wk_range.get(_lyk) or _pw_label.get(_lyk) or ("", "")
                _span = _wk_span.get((_y, _w)) or _pw_span.get((_y, _w)) or 7
                _paths = []
                for _pn, _pm in _hp.items():
                    _pc = _pm.get((_y, _w))
                    _pl = _pm.get(_lyk) or {}
                    if not _pc:
                        continue
                    _paths.append([_pname_ix[_pn], _pc.get("pdp", 0), _pc.get("buy", 0), _pc.get("gmv", 0),
                                   _pl.get("pdp", 0), _pl.get("buy", 0)])
                _paths.sort(key=lambda x: -x[1])
                _out.append({
                    "y": _y, "w": _w, "f": _mmdd(_rng[0]), "t": _mmdd(_rng[1]), "d": _span,
                    "pdp": _cur.get("pdp", 0), "buy": _cur.get("buy", 0), "gmv": _cur.get("gmv", 0),
                    "ly": {"pdp": _ly.get("pdp", 0), "buy": _ly.get("buy", 0), "gmv": _ly.get("gmv", 0),
                           "f": _mmdd(_lrng[0]), "t": _mmdd(_lrng[1])},
                    "p": _paths,
                })
            return _out

        for _hh, _PP in hero_perf.items():
            _PP["wks"] = _weeks_for(_hh, _hero_wk, _path_wk)
            _PP["pdtl"] = _path_dtl.get(_hh, {})
        for _hh, _PP in hero_perf_fw.items():
            _PP["wks"] = _weeks_for(_hh, _hero_wk_fw, _path_wk_fw)
            _PP["pdtl"] = _path_dtl_fw.get(_hh, {})
        _wk_pts = sum(len(v.get("wks") or []) for v in list(hero_perf.values()) + list(hero_perf_fw.values()))
        print(f"주차 스냅샷: 히어로 {len(hero_perf) + len(hero_perf_fw)}종 · 주차포인트 {_wk_pts} · 경로 {len(_path_names)}종"
              f" · 경로상세 {len(_path_dtl) + len(_path_dtl_fw)}종")
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
    # ★26SS(hero_perf) + 26FW(hero_perf_fw) 두 시즌을 함께 싣는다 — 화면 시즌 토글이 이걸로 갈린다.
    #   26FW는 MSTRD `HERO STY` 매핑 기반이라 스타일/히어로가 추가되면 자동으로 목록에 붙는다.
    _perf_src = list(hero_perf.items()) + [(k, v) for k, v in (hero_perf_fw or {}).items()
                                           if v.get("periods")]
    # 성·연령 dict(튜플 키) → [성별, 연령, 노출, 클릭, 조회UV, 전년조회UV] 배열(조회UV 내림차순)
    for _v in list(hero_perf.values()) + list(hero_perf_fw.values()):
        _dm = _v.get("demo")
        if not _dm:
            continue
        _v["demo"] = {per: sorted(([g, a, c["imp"], c["clk"], c["uv"], c["uv_ly"]] for (g, a), c in cells.items()),
                                  key=lambda x: -x[4])
                      for per, cells in _dm.items()}
    hero_list = sorted(
        [{"name": k, "periods": v.get("periods", {}),
          "wow": v.get("wow", {}), "stys": v.get("stys", []),
          "paths": v.get("paths", {}),
          # ★주차 스냅샷(wks) · 경로 중분류(pdtl) · 상품 퍼널(gf) · 조회자 성연령(demo) — 2026-08-01 신설
          "wks": v.get("wks", []), "pdtl": v.get("pdtl", {}),
          "gf": v.get("gf", {}), "demo": v.get("demo", {}),
          "season": v.get("season", ""),
          "goal": _goals.get(k, {}).get("gmv", 0),
          "goal_roas": _goals.get(k, {}).get("roas", "")} for k, v in _perf_src],
        key=lambda x: -(x["periods"].get("YTD", {}).get("gmv", 0)))
    if not hero_list:
        _HEALTH.append("히어로 PMKT 성과 0건 — 트래커 구조 확인")
    _scnt = {}
    for _h in hero_list:
        _scnt[_h["season"]] = _scnt.get(_h["season"], 0) + 1
    print("IMC 히어로 시즌: " + " · ".join(f"{k} {v}종" for k, v in sorted(_scnt.items())))

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

    # ★hero PMKT 조용한 0 방지(2026-07-24) — PMKT기간/주차 읽기가 일시 실패하면(429/타임아웃) pdp_real·conv·
    #   마케팅기여·wow가 전 히어로 0이 된다. gmv는 매출탭 소스라 살아남아 '거래액은 정상인데 유입만 0'으로 보인다.
    #   IG/CRM 가드와 동일 철학: PMKT 신호(ΣYTD pdp_real)가 통째 0이면 앱 직전값을 보존한다
    #   (거래액 포함 1일 stale, 다음 정상 실행 때 자동 복구). 정상 읽힌 날은 신호>0이라 가드 미발동=신선값 주입.
    def _hsig(lst):
        return sum((h.get("periods", {}).get("YTD", {}) or {}).get("pdp_real", 0) for h in (lst or []))
    # ★신선도 게이트 — 실적시트 기준일이 섞인 날은 PMKT도 같은 시트라 믿을 수 없다. 직전값 유지.
    if hero_list and not (_FRESH_SALES and _FRESH_PMKT):
        _prev_heroes = _prev.get("hero") or []
        if _prev_heroes:
            hero_list = _prev_heroes
            _HEALTH.append(f"실적시트 기준일 불일치 → 히어로 PMKT 직전값 유지({len(_prev_heroes)}종)")
            print(f"[보존] 신선도 불일치 — 히어로 PMKT 기존값 유지({len(_prev_heroes)}종)")
    if hero_list and _hsig(hero_list) == 0:
        _prev_heroes = _prev.get("hero") or []
        if _hsig(_prev_heroes) > 0:
            hero_list = _prev_heroes
            _HEALTH.append(f"히어로 PMKT 0건 → 기존값 보존({len(_prev_heroes)}종)")
            print(f"[보존] 히어로 PMKT 읽기 0 — 앱 기존값 유지({len(_prev_heroes)}종)")

    perf = {"ig": {"오피셜": agg_off, "우먼": agg_wm}, "crm": crm, "budget": budget,
            "highlights": highlights, "hero": hero_list,
            # wks[].p 의 첫 값은 이 배열의 인덱스(경로명 반복 저장을 피하려고 인덱스로 넣는다)
            "path_names": _PATH_NAMES}
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
        spreadsheetId=(_src("plm_27ss") or sm27.spreadsheet_id), ranges=ranges).execute()
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

# ── 27SS 보드 단계 스케줄 주입 (★ MS_27SS_작업의뢰 기획시트 → STY_SCHED_27SS) ──
# STY별 stages/dates(14칸)에 '기준 YYYY-MM-DD'를 심어 26FW와 같은 원리로 D+ 지연을 띄운다.
# 스펙·안전규칙 = hero-master-app/docs/27ss-schedule-targets.md
try:
    from soo.hero_ops.sched_27ss import load_27ss_sched
    # 대상 = 앱 PLM_DATA(27SS 후보) 키와 교집합. PLM_DATA 자체는 아직 앱 상수라 여기서 읽어 씀.
    _m27 = re.search(r"const PLM_DATA = (\{.*?\n\});", html2, re.DOTALL)
    _cand = set(json.loads(_m27.group(1)).keys()) if _m27 else None
    sched27, warns27 = load_27ss_sched(sheets, _src("plm_27ss_req"), today=TODAY, only=_cand)
    if not sched27:
        raise ValueError("스케줄 0건 — 조용한 0 덮어쓰기 방지로 기존값 유지")
    blk = "const STY_SCHED_27SS = " + json.dumps(sched27, ensure_ascii=False, indent=2) + ";"
    html2, ns27 = re.subn(r"const STY_SCHED_27SS = \{.*?\n\};", blk, html2, count=1, flags=re.DOTALL)

    # ── 27SS PO 수량(오더시트 MD투입 타겟시즌=2027SS) ─────────────────────────
    #   ★27SS 보드는 stage8.poQuantities가 통째로 비어 있어 수량 뷰의 'PO 전송'이 항상 '—'였다.
    #   t=계획 발주수량(4채널 합) · plm=그중 **PLM PO 번호가 찍힌 발행분**(사용자 지시: PLM 기준이 정확).
    try:
        from soo.hero_ops.po_ingest import parse_po_qty as _ppq
        _po27 = _ppq(sheets, "2027SS")
        _po27_blk = {k: {"t": v["po"]["t"], "plm": (v.get("plm") or {}).get("t", 0)}
                     for k, v in _po27.items() if v["po"]["t"]}
        if _po27_blk:
            _b27 = "const PO_QTY_27SS = " + json.dumps(_po27_blk, ensure_ascii=False, indent=2) + ";"
            html2, _n27p = re.subn(r"const PO_QTY_27SS = \{.*?\n\};", _b27, html2, count=1, flags=re.DOTALL)
            print(f"27SS PO수량 주입: {len(_po27_blk)} 스타일 · 계획 {sum(v['t'] for v in _po27_blk.values()):,}"
                  f" · PLM 발행 {sum(v['plm'] for v in _po27_blk.values()):,} (교체 {_n27p})")
    except Exception as _ep27:
        print(f"[주의] 27SS PO수량 로드 실패 — 직전값 유지: {type(_ep27).__name__}: {_ep27}")
    assert ns27 == 1, f"STY_SCHED_27SS 교체 실패 (matched {ns27})"
    _dl = sum(1 for v in sched27.values() if "delayed" in v["stages"])
    print(f"27SS 스케줄: {len(sched27)} STY 주입 (지연 {_dl}) / 후보 {len(_cand or [])}")
    # 단계 지연 슬랙 알람 — 화면 D+ 배지와 같은 소스로 요약 발송(토큰 없으면 조용히 스킵).
    try:
        from soo.hero_ops import stage_alerts
        # 1차수량(5)의 완료 소스는 작업의뢰가 아니라 앱 입력이라, 시트만 보면 항상 '지연'으로 잡힌다.
        #   → 앱에 수량이 입력된 히어로의 STY는 5단계 알람에서 제외(오탐 방지). 화면 표기는 그대로.
        _plm27 = json.loads(_m27.group(1)) if _m27 else {}
        _q_ok = {c for c, d in _plm27.items() if qinputs.get(d.get("heroName"))}
        _s27 = {}
        for _c, _v in sched27.items():
            if _c in _q_ok and len(_v["stages"]) > 5 and _v["stages"][5] == "delayed":
                _v = {**_v, "stages": list(_v["stages"])}
                _v["stages"][5] = "pending"
            _s27[_c] = _v
        _alerts = stage_alerts.collect(_s27, TODAY, "27SS")
        _fw_board = {s["style"]: {"stages": s["stages"], "dates": s["dates"], "track": s.get("track", "")}
                     for h in heroes for s in h.get("stys", [])}
        _alerts += stage_alerts.collect(_fw_board, TODAY, "26FW")
        stage_alerts.send_if_any(_alerts, TODAY)
    except Exception as _ea:
        print(f"[주의] 단계 지연 알람 스킵: {type(_ea).__name__}: {_ea}")
    for w in warns27[:12]:
        print(f"   [원천주의] {w}")
    if len(warns27) > 12:
        print(f"   [원천주의] 외 {len(warns27) - 12}건")
except Exception as e:
    print(f"[주의] 27SS 스케줄 주입 실패 — 기존값 유지: {type(e).__name__}: {e}")

# ── 26FW 발매센터 데이터 주입 (const LAUNCH_26FW) ──
# 준비(상품기획 14단계 완료율=heroes) + 발매(★MSTRD_26FW 상품MAP 발매스케줄 품번→시리즈→발매일)
#   + 판매(IMC_PERF 현재 누판 YTD, 이름정규화 조인). 상태=발매일 vs TODAY 자동전환.
nlaunch = 0
try:
    _26FW_MAP_ID = _src("mstrd") or "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"   # ★MSTRD_26FW 상품MAP (mstrd 소스키)
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

    _fw_styles = (_FW_HERO_MAP or {}).get("styles") or {}   # MSTRD HERO STY {품번:{grade,hero,...}}
    # 히어로별 26FW 누계 목표수량(=목표 시트 일별 목표를 시즌 누계 창으로 합산) — 홈 26FW 달성율용
    _fw_goal_qty = {}
    for _b, _tv in (globals().get("FW_TARGETS") or {}).items():
        _h = (_fw_styles.get(_b) or {}).get("hero")
        if not _h:
            continue
        _fw_goal_qty[_h] = _fw_goal_qty.get(_h, 0) + ((_tv.get("tq") or {}).get("YTD") or {}).get("t", 0)
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
                #   ★HERO SUB는 대부분 발매 전이라 매출행이 없다 → 매핑된 STY 전건을 싣고
                #     실적 없는 건 pending으로 표시(그전엔 리스트에 아예 안 떠 'MAIN만 잡힌다'로 보였다).
                _st = []
                for _b, _pers in (globals().get("hero_sty_fw", {}) or {}).get(name, {}).items():
                    _st.append({"style": _b, "grade": (_fw_styles.get(_b) or {}).get("grade"),
                                "periods": {p.lower(): {"gmv": (_pers.get(p) or {}).get("gmv", 0),
                                                        "qty": (_pers.get(p) or {}).get("qty", 0)}
                                            for p in _PERIODS}})
                _seen_st = {x["style"] for x in _st}
                for _b, _m in sorted(_fw_styles.items()):
                    if _m.get("hero") != name or _b in _seen_st:
                        continue
                    _st.append({"style": _b, "grade": _m.get("grade"), "pending": 1,
                                "periods": {p.lower(): {"gmv": 0, "qty": 0} for p in _PERIODS}})
                # 실적순, 발매 전(pending)은 뒤로 — 같은 pending끼리는 HERO → HERO SUB 순
                _st.sort(key=lambda x: (x.get("pending", 0), -x["periods"]["ytd"]["gmv"],
                                        0 if x.get("grade") == "HERO" else 1, x["style"]))
                # 달성율 = 시즌 누계 실적수량 ÷ 누계 목표수량(둘 다 7/1~ 기준). 목표 없으면 None → 화면 미표시.
                _gq = _fw_goal_qty.get(name) or 0
                _aq = (_pp.get("ytd") or {}).get("qty") or 0
                sales = {"gmv": _g, "periods": _pp, "stys": _st,
                         "goal_qty": _gq or None,
                         "goal_pct": (_aq / _gq) if _gq else None,
                         # 전환율 = 구매UV/PDP조회UV (실적·퍼널 정의 통일)
                         "conv": round(_buy / _pdp * 100, 1) if _pdp else None,
                         # 마케팅기여 = 마케팅 유입(캠페인/기획전+외부) 거래액 / PMKT 직접경로 거래액
                         "mkt": round(_y.get("ad_gmv", 0) / _pmkt * 100) if _pmkt else None}
        # ★실적이 아직 없는 히어로(힛탠다드·헤비다운·리커버리 등 발매 전)도 STY 커버리지를 보이게 —
        #   MAIN·SUB 전건을 pending 목록으로. 프론트는 sales.stys 없으면 이걸 쓴다.
        _stys_all = None
        if not sales:
            _stys_all = sorted(
                ({"style": _b, "grade": _m.get("grade"), "pending": 1,
                  "periods": {_p.lower(): {"gmv": 0, "qty": 0} for _p in _PERIODS}}
                 for _b, _m in _fw_styles.items() if _m.get("hero") == name),
                key=lambda x: (0 if x["grade"] == "HERO" else 1, x["style"]))
        _fw_list.append({
            "name": name, "grade": grade, "status": status,
            **({"stys_all": _stys_all} if _stys_all else {}),
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
    # ★신선도 게이트 — 실적시트 기준일이 섞인 날엔 홈 26FW 컬럼의 매출만 직전값으로 되돌린다.
    #   (2026-07-31 사고: 게이트가 DASHBOARD·PMKT만 막고 여기는 놔둬서, MTD 탭은 갱신됐는데 FWTD 탭은
    #    전날치인 상태로 주입 → 같은 7/1~ 기간인데 '누계 < 당월'이 되는 모순이 화면에 떴다.)
    #   발매일·상태·STY 스케줄은 MSTRD/무탠 기준이라 매출과 무관 → 그대로 최신값 유지.
    if not _FRESH_SALES:
        try:
            _plm = re.search(r"const LAUNCH_26FW = (\{.*?\});", html2, re.DOTALL)
            _prev_sales = {h["name"]: h.get("sales") for h in json.loads(_plm.group(1)).get("heroes", [])} if _plm else {}
            _rs = 0
            for _h in launch_obj.get("heroes", []):
                _ps = _prev_sales.get(_h["name"])
                if _ps:
                    _h["sales"] = _ps
                    _rs += 1
            if _rs:
                print(f"[보존] 신선도 불일치 — 홈 26FW 매출 직전값 유지({_rs}종)")
                _HEALTH.append(f"실적시트 기준일 불일치 → 홈 26FW 매출 직전값 유지({_rs}종)")
        except Exception as _els:
            print(f"[주의] 홈 26FW 매출 직전값 보존 실패: {type(_els).__name__}: {_els}")
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

# ── PDP 일별 유입 트렌드 주입 → window.__PDP_DAILY (성과탭 상단, PR #18) ──
# 소스=PDP일별 탭(히어로별 일별 pdp_uv, team.sales.pdp_path_daily_summary_v · direct).
# series=실수치 / heroes·grade=26FW / actions=IMC.items(hero_related)에서 유도. 실패 시 샘플 유지.
npdp = 0
try:
    from soo.hero_ops.sales_rollup import read_tab as _read_tab, SALES_SHEET_ID as _SALES_DEF
    _pdp_sid = _src("dashboard") or _SALES_DEF
    # 노트북 산출 = date, goods_no, style_no, pdp_uv (goods 단위) → 여기서 26FW 히어로로 롤업.
    #   ★히어로 매핑을 생성기 한 곳(_hero_of_fw)에만 두려는 것(노트북엔 uid 목록만). 구 포맷(hero 컬럼)도 호환.
    _pdp_rows = _read_tab(sheets, _pdp_sid, "PDP일별")
    _pdp_h_of = globals().get("_hero_of_fw")
    _pdp_by, _pdp_dates = {}, set()
    for _r in _pdp_rows:
        _d = str(_r.get("date") or "")[:10]
        _h = str(_r.get("hero") or "").strip()
        if not _h and _pdp_h_of:
            _h = _pdp_h_of(_r.get("style_no"), _r.get("goods_no")) or ""
        try:
            _u = int(float(_r.get("pdp_uv")))
        except (TypeError, ValueError):
            continue
        if not _d or not _h or _u <= 0:
            continue
        _hd = _pdp_by.setdefault(_h, {})
        _hd[_d] = _hd.get(_d, 0) + _u
        _pdp_dates.add(_d)
    _pdp_dates = sorted(_pdp_dates)
    if "전체" not in _pdp_by and _pdp_by:                                 # 전체 = 히어로 합(구 포맷은 시트에 이미 있음)
        _pdp_by["전체"] = {d: sum(v.get(d, 0) for v in _pdp_by.values()) for d in _pdp_dates}
    if _pdp_dates and "전체" in _pdp_by:
        _tot = {h: sum(v.values()) for h, v in _pdp_by.items() if h != "전체"}
        _order = ["전체"] + sorted(_tot, key=lambda h: -_tot[h])          # 전체 먼저 + 총UV 내림차순
        _series = {h: [_pdp_by[h].get(d, 0) for d in _pdp_dates] for h in _order}
        _grade = {}                                                       # 26FW 등급(S/A/E)
        for h in _order[1:]:
            g = _FW_GRADE.get(h) or next((v for k, v in _FW_GRADE.items()
                                          if k.replace(" ", "") == h.replace(" ", "")), None)
            if g:
                _grade[h] = g
        # actions = IMC.items(윈도우 필터·hero_related 태깅됨) 전량 → PDP 채널 7종으로 묶음.
        # ★규칙=히어로 관련 IMC 액션은 전부 핀으로(캘린더와 1:1). type 기준(channel은 'SNS광고' 등 변형 있음).
        _CH = {"IG": "IG", "SNS": "IG", "PR": "PR", "CRM": "CRM",
               "발매": "입고알람", "입고알람": "입고알람",
               "캠페인": "프로모션", "기획전": "프로모션", "온라인": "프로모션", "온사이트": "프로모션",
               "에너지": "바이럴", "오프라인": "오프라인", "전사": "전사"}
        _h2n = {h.replace(" ", ""): h for h in _order[1:]}
        # 별칭(라이트 다운/커브드 데님 등) → 시리즈명. 시계열에 있는 히어로만.
        _pdp_al, _amb = {}, set()
        for _hn, _als in (globals().get("_hero_alias") or {}).items():
            _tgt = _h2n.get(_hn.replace(" ", ""))
            if not _tgt:
                continue
            for _al in _als:
                _k = _al.replace(" ", "")
                if _pdp_al.get(_k, _tgt) != _tgt:
                    _amb.add(_k)                                         # 2개 히어로가 다투는 별칭(예: '플리스')=버림
                _pdp_al[_k] = _tgt
        for _k in _amb:
            _pdp_al.pop(_k, None)
        _pdp_al.update(_h2n)                                             # 정식명은 항상 우선
        _pdp_keys = sorted(_pdp_al, key=len, reverse=True)               # 긴 별칭 우선(부분일치 오탐 방지)

        def _pdp_hero_of(_it):
            _blob = (_it.get("title", "") + " " + _it.get("sub", "")).replace(" ", "")
            return next((_pdp_al[k] for k in _pdp_keys if k in _blob), "")

        _acts = []
        for _it in _items:                                               # 이미 윈도우 필터됨
            if not _it.get("hero_related") and _it.get("type") != "전사":   # 전사 캠페인은 히어로 무관 항상
                continue
            _ch = _CH.get(_it.get("type")) or _CH.get(_it.get("channel"))
            _dd = str(_it.get("date") or "")[:10]
            if not _ch or _dd < _pdp_dates[0] or _dd > _pdp_dates[-1]:
                continue
            # owner 없으면 IG/CRM은 sub(오피셜·우먼 등 발행 주체)로 폴백
            _ow = (_it.get("owner") or "").strip()
            if not _ow and _it.get("type") in ("IG", "SNS", "CRM"):
                _ow = (_it.get("sub") or "").strip()[:12]
            _a = {"date": _dd, "ch": _ch, "title": _it["title"], "owner": _ow}
            _hv = _pdp_hero_of(_it)
            if _hv:
                _a["hero"] = _hv
            _acts.append(_a)
        _pdp_obj = {"as_of": TODAY.isoformat(), "sample": False, "dates": _pdp_dates,
                    "series": _series, "heroes": _order, "grade": _grade, "actions": _acts}
        html2, npdp = re.subn(r"window\.__PDP_DAILY = [^\n]*?;",
                              lambda _m: "window.__PDP_DAILY = " + json.dumps(_pdp_obj, ensure_ascii=False) + ";",
                              html2, count=1)
        print(f"PDP일별 주입: {len(_pdp_dates)}일 · 히어로 {len(_order) - 1} · actions {len(_acts)} (교체 {npdp})")
        if npdp != 1:
            _HEALTH.append("window.__PDP_DAILY 교체 실패(앱 플레이스홀더 확인)")
    else:
        _HEALTH.append("PDP일별 데이터 없음/전체 누락 — 트렌드 샘플 유지")
except Exception as e:
    print(f"[주의] PDP일별 주입 실패 — 트렌드 샘플 유지: {type(e).__name__}: {e}")

HTML.write_text(html2, encoding="utf-8")

print(f"교체 완료: {len(heroes)} 히어로(시리즈) · APP_TODAY→{TODAY.isoformat()}(교체 {nt}) · SALES_AS_OF(교체 {nsa}) · GEN_AT→{_GEN_KST.strftime('%Y-%m-%d %H:%M')}(교체 {nga}) · DASHBOARD(교체 {nd}) · 27SS진척(교체 {n27}) · LAUNCH_26FW(교체 {nlaunch}) · PDP일별(교체 {npdp}) · INBOUND_BOARD(교체 {ninb})")
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
