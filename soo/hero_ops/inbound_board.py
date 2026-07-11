"""26FW 입고 보드 데이터 빌더.

플랜(입고예정)  = 무탠본부_오더시트 '생산관리' 탭 (SKU=품번-컬러 단위).
실적(실입고)    = 우선 시트 실입고일/량(AO/AP). DBX WMS(입고일자별) 탭이 생기면 그걸로 대체/보강.

히어로 범위 = MSTRD_26FW 상품MAP '발매스케줄'(품번→시리즈) 15 히어로.
매핑 = 생산관리 신품번(K/M) 또는 품번-컬러 base → 품번 → 히어로.

산출 구조(소스 무관):
INBOUND_BOARD = {
  season, as_of, source, cutoff,
  heroes: [{name, grade, launch, status, plan_total, actual_total, sku_count, skus: [
     {sku, style, name, color, planned:[{date,qty,ordered}], actual:[{date,qty}],
      plan_total, actual_total, ordered_total, status, next_date}
  ]}],
  days: [{date, plan_qty, actual_qty, sku_count}]   # 날짜순 집계(캘린더용)
}
"""
import datetime, re
from collections import defaultdict, OrderedDict

PROD_SID = "13R4gcJ7cDlReY-vwjXZf0kMZ7tC4kr2-S7PC9uziVUQ"   # 무탠본부_오더시트
PROD_TAB = "생산관리"
MAP_SID = "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"      # MSTRD_26FW 상품MAP
SCHED_TAB = "발매스케줄"

GRADE = {'라이트다운':'S','힛탠다드':'S','커브드팬츠':'S',
 '웜 팬츠':'A','빅토리아 울':'A','그리드/메시 플리스':'A','에센셜 플리스':'A','리커버리':'A',
 '헤비다운':'E','슬랙스':'E','데님팬츠':'E','스웨트팬츠':'E','벨트':'E','양말':'E','심리스 브라':'E'}
ALIAS = {'그리드/알파 플리스':'그리드/메시 플리스'}
HERO_ORDER = ['라이트다운','힛탠다드','커브드팬츠','웜 팬츠','빅토리아 울','그리드/메시 플리스',
 '에센셜 플리스','리커버리','헤비다운','데님팬츠','슬랙스','스웨트팬츠','벨트','양말','심리스 브라']

# 생산관리 열(0-based)
C_SKU, C_STYLE_K, C_NAME, C_STYLE_M = 8, 10, 11, 12
C_COLOR, C_ORDQTY = 25, 26
C_PLAN_DATE, C_PLAN_QTY, C_ACT_DATE, C_ACT_QTY = 36, 37, 40, 41

# 26FW 시즌 관련 입고만: 이 날짜 이후 예정/실입고만 포함(상시 히어로 과거 이력 배제)
CUTOFF = datetime.date(2026, 6, 1)


def _pdate(s):
    """'26-07-31' / '2026-07-31' / '26. 7. 8' → date. 실패 시 None."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{2,4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def _num(s):
    s = str(s or "").replace(",", "").strip()
    if not s or s in ("-", "#REF!", "#N/A"):
        return 0
    try:
        return int(round(float(s)))
    except ValueError:
        return 0


def _g(row, j):
    return str(row[j]).strip() if len(row) > j else ""


def build_pumbon2hero(sheets):
    """발매스케줄 → {품번: 히어로}. 15 히어로 전체(시즌 무관, 캐리오버 포함)."""
    res = sheets.spreadsheets().values().get(
        spreadsheetId=MAP_SID, range=f"'{SCHED_TAB}'",
        valueRenderOption="FORMATTED_VALUE").execute()
    rows = res.get("values", [])
    p2h = {}
    for r in rows[9:]:                       # 헤더 R9, 데이터 R10~
        ser = ALIAS.get(_g(r, 4), _g(r, 4))
        if ser not in GRADE:
            continue
        p = _g(r, 3)
        if p and p != "발주전":
            p2h[p] = ser
    return p2h


def _read_prod_columns(sheets):
    """생산관리 필요한 열만 batchGet(28k행 부하 감소). 행 인덱스로 정렬."""
    ranges = [f"'{PROD_TAB}'!I7:I", f"'{PROD_TAB}'!K7:M",      # I=8 / K:M=10~12
              f"'{PROD_TAB}'!Z7:AA", f"'{PROD_TAB}'!AK7:AL",   # Z:AA=25~26 / AK:AL=36~37
              f"'{PROD_TAB}'!AO7:AP"]                          # AO:AP=40~41
    res = sheets.spreadsheets().values().batchGet(
        spreadsheetId=PROD_SID, ranges=ranges,
        valueRenderOption="FORMATTED_VALUE").execute()
    vr = res.get("valueRanges", [])
    cols = [v.get("values", []) for v in vr]
    n = max((len(c) for c in cols), default=0)

    def cell(ci, ri, off=0):
        block = cols[ci]
        return str(block[ri][off]).strip() if ri < len(block) and off < len(block[ri]) else ""

    rows = []
    for ri in range(n):
        rows.append({
            "sku": cell(0, ri),                       # I 품번-컬러
            "style_k": cell(1, ri, 0),                # K 신품번
            "name": cell(1, ri, 1),                   # L 품명
            "style_m": cell(1, ri, 2),                # M 신품번
            "color": cell(2, ri, 0),                  # Z 색상 국문
            "ordqty": cell(2, ri, 1),                 # AA 발주량
            "plan_date": cell(3, ri, 0),              # AK 입고예정일
            "plan_qty": cell(3, ri, 1),               # AL 입고예정량
            "act_date": cell(4, ri, 0),               # AO 실입고일
            "act_qty": cell(4, ri, 1),                # AP 실입고량
        })
    return rows


SALES_SHEET_ID = "1iHH2qG8Uj5vmlC3aXkey96usktWODmguDPD_ToT2rfA"   # 히어로 실적 (자동) — DBX 잡 산출


def load_dbx_actuals(sheets, sheet_id=SALES_SHEET_ID, tab="입고일자별"):
    """DBX '입고일자별' 탭(R1 라벨·R2 헤더·R3~ 데이터) → {품번-컬러: [{date,qty}]}.
    BARCODE 파싱 sku_code가 시트 품번-컬러와 동일 키. dt=yyyyMMdd→ISO. 실패 시 None(=시트 실적 폴백)."""
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{tab}'!A2:G200000",
            valueRenderOption="UNFORMATTED_VALUE").execute()
    except Exception as e:
        print(f"[입고일자별] 읽기 실패 → 시트 실적 폴백: {type(e).__name__}: {e}")
        return None
    vals = res.get("values", [])
    if not vals:
        return None
    hdr = [str(c).strip() for c in vals[0]]
    idx = {h: i for i, h in enumerate(hdr)}
    if "sku_code" not in idx or "dt" not in idx or "inbound_qty" not in idx:
        print(f"[입고일자별] 헤더 예상과 다름: {hdr} → 폴백")
        return None
    out = defaultdict(list)
    for row in vals[1:]:
        def gv(k):
            i = idx[k]
            return row[i] if i < len(row) else ""
        sku = str(gv("sku_code")).strip()
        dt = str(gv("dt")).strip()
        dt = dt.split(".")[0]                       # 20260601.0 방어
        qty = _num(gv("inbound_qty"))
        if sku and len(dt) == 8 and qty:
            out[sku].append({"date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}", "qty": qty})
    # 같은 날짜 병합
    for sku, arr in out.items():
        m = {}
        for a in arr:
            m[a["date"]] = m.get(a["date"], 0) + a["qty"]
        out[sku] = [{"date": d, "qty": q} for d, q in sorted(m.items())]
    return dict(out)


def build_inbound_board(sheets, as_of=None, launch_meta=None, dbx_actuals=None):
    as_of = as_of or datetime.date.today()
    p2h = build_pumbon2hero(sheets)
    prod = _read_prod_columns(sheets)

    # 히어로 → sku코드 → 집계
    hero_sku = defaultdict(lambda: defaultdict(lambda: {
        "sku": "", "style": "", "name": "", "color": "",
        "planned": [], "actual": [], "ordered_total": 0}))

    for r in prod:
        style = r["style_k"] or r["style_m"]
        sku = r["sku"]
        base = sku.rsplit("-", 1)[0] if "-" in sku else sku
        hero = p2h.get(r["style_k"]) or p2h.get(r["style_m"]) or p2h.get(base)
        if not hero:
            continue
        pd, ad = _pdate(r["plan_date"]), _pdate(r["act_date"])
        # 26FW 시즌 관련만: 예정일 또는 실입고일이 컷오프 이후
        if not ((pd and pd >= CUTOFF) or (ad and ad >= CUTOFF)):
            continue
        if not sku:
            continue
        cell = hero_sku[hero][sku]
        cell["sku"], cell["style"], cell["color"] = sku, style, r["color"]
        cell["name"] = cell["name"] or r["name"]
        cell["ordered_total"] += _num(r["ordqty"])
        pq = _num(r["plan_qty"])
        if pd and pq:
            cell["planned"].append({"date": pd.isoformat(), "qty": pq})
        aq = _num(r["act_qty"])
        if ad and aq:
            cell["actual"].append({"date": ad.isoformat(), "qty": aq})

    lm = launch_meta or {}
    heroes_out = []
    day_plan = defaultdict(lambda: {"plan_qty": 0, "actual_qty": 0, "skus": set()})
    day_act = defaultdict(lambda: {"actual_qty": 0})

    for hero in HERO_ORDER:
        skus = hero_sku.get(hero, {})
        _cut = CUTOFF.isoformat()
        dbx_keys = set(dbx_actuals) if dbx_actuals is not None else set()
        # ★벨트류 폴백: 색이 없는 상품은 시트 SKU=품번-사이즈(예 MECBE0Z50-59)인데
        #   WMS STL_NO은 품번(MECBE0Z50, 사이즈는 GDS_OPT) → col8 미스. 이때 style(신품번)로
        #   폴백 매칭 + 사이즈행 병합(중복합산 방지). 의류는 col8이 이미 매칭돼 폴백 안 탐.
        merged = OrderedDict()
        for code, c in skus.items():
            style = c["style"]
            if dbx_actuals is not None and code not in dbx_keys and style and style in dbx_keys:
                out_key, dbx_key = style, style           # 벨트 폴백 → style로 병합
            else:
                out_key, dbx_key = code, code
            if out_key not in merged:
                merged[out_key] = {"sku": out_key, "style": style, "name": c["name"],
                                   "color": c["color"], "planned": [], "actual_sheet": [],
                                   "ordered_total": 0, "dbx_key": dbx_key, "n": 0}
            m = merged[out_key]
            m["planned"] += c["planned"]; m["actual_sheet"] += c["actual"]
            m["ordered_total"] += c["ordered_total"]; m["n"] += 1
            if not m["name"]:
                m["name"] = c["name"]
        sku_list = []
        h_plan = h_act = 0
        for code, c in merged.items():
            planned = sorted(c["planned"], key=lambda x: x["date"])
            plan_total = sum(p["qty"] for p in planned)
            # 실적 소스: DBX(WMS) 있으면 그걸로(컷오프 이후만), 없으면 시트 AO/AP
            if dbx_actuals is not None:
                actual = sorted([a for a in dbx_actuals.get(c["dbx_key"], []) if a["date"] >= _cut],
                                key=lambda x: x["date"])
            else:
                actual = sorted(c["actual_sheet"], key=lambda x: x["date"])
            act_total = sum(a["qty"] for a in actual)
            _color = ("전 사이즈" if c["n"] > 1 else c["color"])
            next_date = None
            for p in planned:
                if act_total < plan_total or not actual:
                    next_date = p["date"]
                    break
            # 상태
            if plan_total and act_total >= plan_total:
                status = "완료"
            elif act_total > 0:
                status = "입고중"
            elif planned and _pdate(planned[0]["date"]) and _pdate(planned[0]["date"]) < as_of:
                status = "지연"
            else:
                status = "예정"
            sku_list.append({
                "sku": code, "style": c["style"], "name": c["name"], "color": _color,
                "planned": planned, "actual": actual,
                "plan_total": plan_total, "actual_total": act_total,
                "ordered_total": c["ordered_total"], "status": status,
                "next_date": next_date or (planned[0]["date"] if planned else None)})
            h_plan += plan_total
            h_act += act_total
            for p in planned:
                day_plan[p["date"]]["plan_qty"] += p["qty"]
                day_plan[p["date"]]["skus"].add(code)
            for a in actual:
                day_plan[a["date"]]["actual_qty"] += a["qty"]
                day_plan[a["date"]]["skus"].add(code)
        # 정렬: 상태(예정/입고중/지연 먼저) → next_date
        srank = {"지연": 0, "입고중": 1, "예정": 2, "완료": 3}
        sku_list.sort(key=lambda s: (srank.get(s["status"], 9), s["next_date"] or "9999"))
        meta = lm.get(hero, {})
        heroes_out.append({
            "name": hero, "grade": GRADE[hero],
            "launch": meta.get("launch"), "hero_status": meta.get("status"),
            "plan_total": h_plan, "actual_total": h_act, "sku_count": len(sku_list),
            "skus": sku_list})

    days = [{"date": d, "plan_qty": v["plan_qty"], "actual_qty": v["actual_qty"],
             "sku_count": len(v["skus"])}
            for d, v in sorted(day_plan.items())]

    return {
        "season": "26FW", "as_of": as_of.isoformat(),
        "source": "생산관리 탭(입고예정 AK/AL) + " + ("DBX WMS 실입고(입고일자별)" if dbx_actuals is not None else "시트 실입고(AO/AP)"),
        "cutoff": CUTOFF.isoformat(),
        "heroes": heroes_out, "days": days}


if __name__ == "__main__":
    import json, sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from soo.auth import get_credentials, build_services
    ROOT = Path(__file__).resolve().parents[2]
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    lm = {}
    lf = ROOT / "hero_launch_26fw.json"
    if lf.exists():
        lm = json.load(open(lf, encoding="utf-8")).get("heroes", {})
    dbx = load_dbx_actuals(svc["sheets"])
    print(f"DBX 실적 SKU 수: {len(dbx) if dbx else 0}")
    board = build_inbound_board(svc["sheets"], launch_meta=lm, dbx_actuals=dbx)
    out = ROOT / "hero_inbound_26fw.json"
    json.dump(board, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"저장: {out}")
    print(f"\nas_of {board['as_of']}  cutoff {board['cutoff']}  날짜버킷 {len(board['days'])}")
    for h in board["heroes"]:
        print(f"  [{h['grade']}] {h['name']:14s} SKU{h['sku_count']:3d} "
              f"예정 {h['plan_total']:>8,} 실입고 {h['actual_total']:>8,} 발매 {h['launch'] or '-'}")
    # 상태 분포
    from collections import Counter
    st = Counter(s["status"] for h in board["heroes"] for s in h["skus"])
    print("\n상태 분포:", dict(st))
    # 샘플 SKU
    print("\n샘플(데님팬츠 입고중/완료):")
    for h in board["heroes"]:
        if h["name"] == "데님팬츠":
            for s in h["skus"][:6]:
                print(f"  {s['sku']:14s} {s['color']:8s} 예정{s['plan_total']:>6,} 실{s['actual_total']:>6,} "
                      f"{s['status']:4s} next {s['next_date']} plan{s['planned']} act{s['actual']}")
