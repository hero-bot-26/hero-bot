"""히어로 실적 대시보드 데이터 집계.

Databricks가 시트에 써 둔 raw 탭(goods×channel×기간 매출 8탭 + 잔여재고 + 입고현황)을 읽어
HERO STY의 style→히어로 매핑으로 히어로별/스타일별로 롤업하고, 히어로목표(xlsx)를 merge,
파생지표(YoY·달성율·소진율·매총율 등)를 계산해 앱이 읽을 DASHBOARD 구조를 만든다.

소스 시트: 개발 중엔 전사 대시보드 시트(이미 동일 raw 탭 보유)로 검증, 운영 땐 SA 시트로 교체.
raw 탭 레이아웃: 1행=설명, 2행=헤더, 3행~=데이터.
  매출:   channel goods_no brand team gender_line category1 category2 md_name
          release_season sell_season style_no tag_gmv gmv qty total_discount revenue gross_take net_take goods_opt
  잔여재고: dt stock_type lgort brand_nm team goods_no style_no qty normal_price_amt wonga_amt barcode
  입고현황: plant_nm brand_nm team goods_no style_no inbound_qty normal_price_amt wonga_amt barcode
"""
from __future__ import annotations

import re
from collections import defaultdict

from soo.hero_ops.target_ingest import parse_targets

DEV_SHEET_ID = "1aAYXjJPFgWCJAmZabc_f-f-wF3z492cIeDE-aVlx-HY"   # 전사 대시보드(전환기 소스, 매출 raw 탭 보유)
SALES_SHEET_ID = "1iHH2qG8Uj5vmlC3aXkey96usktWODmguDPD_ToT2rfA"  # "히어로 실적 (자동)" — Databricks 노트북이 쓰는 전용 시트.
# ↑ Databricks 잡(hero_sales_to_sheet.py) 1회 Run 으로 10탭 채워지면, 생성기 build_dashboard 소스를 DEV_SHEET_ID→SALES_SHEET_ID 로 전환.
HERO_SHEET = "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"
STYLE_RE = re.compile(r"^M[A-Z0-9]{8}$")

PERIODS = ["YTD", "MTD", "WEEK", "DAY"]
PERIOD_TABS = {p: (p, "전년" + p) for p in PERIODS}      # (당기 탭, 전년 탭)
SALES_METRICS = ["tag_gmv", "gmv", "qty", "total_discount", "revenue", "gross_take", "net_take"]
STOCK_TYPES = ["온라인창고", "오프라인허브", "매장"]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _base(style) -> str:
    return str(style).strip().split("-")[0]


# 컬러 코드 → 한글명 (통합 UID는 goods_opt에 '01.블랙^L'식 컬러명, 컬러별 UID는 style suffix '-BK').
# 둘을 같은 컬러로 병합하려 코드→명 정규화. 미등록 코드는 코드 그대로 폴백.
COLOR_KO = {
    "BK": "블랙", "NA": "네이비", "WH": "화이트", "IV": "아이보리", "GY": "그레이",
    "DG": "다크그레이", "MG": "멜란지그레이", "LG": "라이트그레이", "CG": "차콜그레이",
    "BE": "베이지", "LB": "라이트베이지", "DB": "다크베이지", "CR": "크림", "EW": "에크루",
    "PK": "핑크", "BL": "블루", "LB2": "라이트블루", "GN": "그린", "KH": "카키", "OL": "올리브",
    "BR": "브라운", "RD": "레드", "OR": "오렌지", "YL": "옐로우", "PP": "퍼플", "UM": "물색",
    "MT": "민트", "SB": "스카이블루", "WI": "와인",
}
COLOR_KO_INV = {v: k for k, v in COLOR_KO.items()}   # 한글→코드 (goods_opt 한글명에 코드 부착용)


def color_display(code, korean, code2kor=None, kor2code=None) -> str:
    """컬러 표시명을 '한글(코드)'로 통일. 한글 없으면 코드만, 코드 없으면 한글만.
    code2kor/kor2code = 오더시트 '컬러구분' 크로스워크(없으면 COLOR_KO만). 같은 색은 동일 문자열로 수렴→병합."""
    code = (code or "").strip().upper()
    kor = (korean or "").replace(" ", "").strip()
    if not kor and code:
        kor = COLOR_KO.get(code) or (code2kor or {}).get(code, "")
    if not code and kor:
        code = COLOR_KO_INV.get(kor) or (kor2code or {}).get(kor, "")
    if kor and code:
        return f"{kor}({code})"
    return kor or code or "기타"


def _color(row, code2kor=None, kor2code=None) -> str:
    """행의 대표 컬러('한글(코드)'). 통합 UID=goods_opt 'NN.컬러^사이즈', 컬러별 UID=style suffix."""
    opt = str(row.get("goods_opt") or "")
    if "^" in opt:                                   # 통합 UID: '01.딥인디고^L'
        nm = re.sub(r"^\s*\d+[.\s]*", "", opt.split("^")[0]).replace(" ", "")
        return color_display("", nm, code2kor, kor2code) if nm else "기타"
    style = str(row.get("style_no") or "")
    if "-" in style:                                 # 컬러별 UID: 'MWFUR0C03-BK'
        code = style.rsplit("-", 1)[-1].strip()
        return color_display(code, "", code2kor, kor2code)
    return "기타"


# ── HERO STY → style→hero(시리즈) 매핑 ──────────────────────────────────────
def build_style_to_hero(sheets, hero_book=HERO_SHEET, hero_range="'HERO STY'!A7:M400"):
    res = sheets.spreadsheets().values().get(
        spreadsheetId=hero_book, range=hero_range,
        valueRenderOption="UNFORMATTED_VALUE").execute()
    style_to_hero, hero_meta, order = {}, {}, []
    for r in res.get("values", []):
        def c(i):
            return str(r[i]).strip() if i < len(r) and r[i] is not None else ""
        if c(1) not in ("HERO", "HERO SUB"):
            continue
        style = c(2) or c(0)
        if not STYLE_RE.match(style):
            continue
        series = c(3)
        if not series:
            continue
        style_to_hero[_base(style)] = series
        if series not in hero_meta:
            hero_meta[series] = {"name": series, "team": c(6), "item": c(7), "season": c(9)}
            order.append(series)
    return style_to_hero, hero_meta, order


# ── 시트 탭 읽기 (헤더=2행, 데이터=3행~) → list[dict] ──────────────────────
# ── 시트 신선도(as-of) 검사 ────────────────────────────────────────────────
# 각 raw 탭 1행 라벨 끝에 집계 종료일이 박혀 있다(예: '매출 MTD 20260701~20260728').
# DBX 잡(3h)이 시트를 쓰는 도중 앱 CI가 읽으면 탭마다 기준일이 섞여 26FW 누계가 하루 밀리는 등
# '조용히 틀린 값'이 앱에 실린다(2026-07-29 사고). 그래서 주입 전에 종료일을 확인한다.
#   값 = as_of(전일) 기준 오프셋 일수. 0 = 전일까지, -7 = 전일-7일(직전WEEK).
FRESH_TABS = {
    "YTD": 0, "MTD": 0, "WEEK": 0, "DAY": 0, "FWTD": 0, "직전WEEK": -7,
    "PDP퍼널": 0, "PMKT기간": 0, "PMKT주차": 0, "PMKT경로기간": 0, "PMKT경로주차": 0, "PDP일별": 0,
}
_ASOF_RE = re.compile(r"(\d{8})(?!.*\d{8})", re.S)   # 라벨의 마지막 8자리 날짜


def check_freshness(sheets, sheet_id, as_of, tabs=None):
    """as_of(YYYYMMDD, 보통 전일) 기준으로 각 탭 라벨의 종료일을 검사.
    returns (fresh: bool, detail: list[str]) — detail은 어긋난 탭만 '탭 20260727(기대 20260728)' 형식.
    라벨을 못 읽거나 날짜가 없으면 판단 보류(신선한 것으로 간주) — 새 탭·포맷 변경에 과민반응 방지."""
    import datetime as _dt
    want = {t: o for t, o in (tabs or FRESH_TABS).items()}
    try:
        res = sheets.spreadsheets().values().batchGet(
            spreadsheetId=sheet_id, ranges=[f"'{t}'!A1" for t in want]).execute()
    except Exception as e:                       # 라벨 조회 실패 = 판단 불가 → 통과(기존 가드들이 받는다)
        print(f"[freshness] 라벨 조회 실패 — 검사 스킵: {type(e).__name__}")
        return True, []
    base = _dt.datetime.strptime(as_of, "%Y%m%d")
    bad = []
    for tab, r in zip(want, res.get("valueRanges", [])):
        vals = r.get("values") or []
        label = str(vals[0][0]) if (vals and vals[0]) else ""
        m = _ASOF_RE.search(label)
        if not m:
            continue                             # 날짜 없는 탭(잔여재고 등)·읽기 실패 → 보류
        want_end = (base + _dt.timedelta(days=want[tab])).strftime("%Y%m%d")
        if m.group(1) != want_end:
            bad.append(f"{tab} {m.group(1)}(기대 {want_end})")
    return (not bad), bad


def read_tab(sheets, sheet_id, tab, max_row=200000):
    res = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A2:AB{max_row}",
        valueRenderOption="UNFORMATTED_VALUE").execute()
    vals = res.get("values", [])
    if not vals:
        return []
    header = [str(h).strip() for h in vals[0]]
    out = []
    for row in vals[1:]:
        out.append({header[i]: (row[i] if i < len(row) else None) for i in range(len(header))})
    return out


def _blank_channels():
    return {ch: {m: 0.0 for m in SALES_METRICS} for ch in ("total", "online", "offline")}


def _add(dst, ch, row):
    for m in SALES_METRICS:
        v = _f(row.get(m))
        dst["total"][m] += v
        dst[ch][m] += v


# ── 매출 집계: 히어로/스타일/컬러 × 기간 × 채널 ──────────────────────────────
def aggregate(sheets, sheet_id, goods_to_hero, code2kor=None, kor2code=None, style_to_hero=None,
              period_tabs=None, goods_to_style=None):
    # period_tabs: {기간: (당기탭, 전년탭)}. 26FW는 누계를 FWTD(7/1~)로 읽으려고 오버라이드한다.
    # hero -> {periods:{P:{cur:channels, prev:channels}}, stys:{base:{name, periods, colors:{opt:periods}}}}
    # style_to_hero 주면 매출 히어로 귀속을 행별 신품번(base)→hero 로 해석(성과 탭과 100% 동일 총액).
    heroes = defaultdict(lambda: {
        "periods": {p: {"cur": _blank_channels(), "prev": _blank_channels()} for p in PERIODS},
        "stys": defaultdict(lambda: {
            "name": "", "team": "", "category1": "", "md_name": "",
            "periods": {p: {"cur": _blank_channels(), "prev": _blank_channels()} for p in PERIODS},
            "colors": defaultdict(lambda: {p: {"cur": _blank_channels(), "prev": _blank_channels()} for p in PERIODS}),
        }),
    })
    stats = {"rows": 0, "mapped": 0, "unmapped_goods": set()}

    for period, (cur_tab, prev_tab) in (period_tabs or PERIOD_TABS).items():
        for when, tab in (("cur", cur_tab), ("prev", prev_tab)):
            for row in read_tab(sheets, sheet_id, tab):
                stats["rows"] += 1
                try:
                    gid = int(row.get("goods_no"))
                except (TypeError, ValueError):
                    continue
                base = _base(row.get("style_no") or "")
                if goods_to_style:
                    # ★STY 키를 MSTRD 품번으로 통일 — 매출시트가 리뉴얼 이전품번을 쓰는 경우
                    #   (FMASC101 = MEASC0Z70 라이트웨이트 크루 삭스 1팩) 같은 상품이 두 줄로 쪼개진다.
                    _canon = goods_to_style.get(str(gid)) or goods_to_style.get(gid)
                    if _canon:
                        base = _base(_canon)
                if style_to_hero is not None:
                    hero = style_to_hero.get(base)     # 성과 탭과 동일: 행별 신품번→hero
                    if not hero:                        # 신품번 빈칸/누락 goods → uid 폴백
                        hero = (goods_to_hero.get(gid) or {}).get("hero")
                else:
                    hero = (goods_to_hero.get(gid) or {}).get("hero")
                if not hero:
                    stats["unmapped_goods"].add(gid)
                    continue
                stats["mapped"] += 1
                ch = "online" if str(row.get("channel")).strip().lower() == "online" else "offline"
                H = heroes[hero]
                _add(H["periods"][period][when], ch, row)
                S = H["stys"][base]
                if when == "cur" and period == "YTD":   # 메타는 한 번만 (품명은 매출탭에 없음 → 생성기서 보강)
                    S["team"] = S["team"] or str(row.get("team") or "")
                    S["category1"] = S["category1"] or str(row.get("category1") or "")
                    S["md_name"] = S["md_name"] or str(row.get("md_name") or "")
                _add(S["periods"][period][when], ch, row)
                _add(S["colors"][_color(row, code2kor, kor2code)][period][when], ch, row)
    return heroes, stats


# ── 잔여재고 / 입고 집계 (히어로·스타일별) ──────────────────────────────────
def aggregate_stock(sheets, sheet_id, goods_to_hero, code2kor=None, kor2code=None):
    hero_stock = defaultdict(lambda: {"qty": 0.0, "amt_normal": 0.0, "amt_wonga": 0.0,
                                      "by_type": {t: 0.0 for t in STOCK_TYPES}})
    sty_stock = defaultdict(lambda: {"qty": 0.0, "amt_normal": 0.0, "amt_wonga": 0.0})
    color_stock = defaultdict(lambda: {"qty": 0.0, "amt_normal": 0.0, "amt_wonga": 0.0})  # (hero, base, color)
    for row in read_tab(sheets, sheet_id, "잔여재고"):
        try:
            gid = int(row.get("goods_no"))
        except (TypeError, ValueError):
            continue
        hero = (goods_to_hero.get(gid) or {}).get("hero")
        if not hero:
            continue
        base = _base(row.get("style_no") or "")
        q, an, aw = _f(row.get("qty")), _f(row.get("normal_price_amt")), _f(row.get("wonga_amt"))
        H = hero_stock[hero]
        H["qty"] += q; H["amt_normal"] += an; H["amt_wonga"] += aw
        st = str(row.get("stock_type") or "").strip()
        if st in H["by_type"]:
            H["by_type"][st] += q
        S = sty_stock[(hero, base)]
        S["qty"] += q; S["amt_normal"] += an; S["amt_wonga"] += aw
        # 컬러별: 잔여재고 style_no는 '-BK' suffix 보유 → _color()가 매출과 동일 컬러명 산출
        C = color_stock[(hero, base, _color(row, code2kor, kor2code))]
        C["qty"] += q; C["amt_normal"] += an; C["amt_wonga"] += aw
    return hero_stock, sty_stock, color_stock


# 시즌 기준 입고 — 사용자 확정(2026-07-30): **FW입고 = 그 해 6/1~ · SS입고 = 전년 12/1~**.
#   기존 '입고현황' 탭은 2025-11-01 고정 시작 누적이라 시즌 구분이 없었다(캐리오버 스타일은
#   전 시즌 입고분까지 합산). 일자별 탭('입고일자별', dt×품번-컬러)을 시즌 창으로 잘라 쓴다.
#   ★일자별 탭엔 goods_no·금액이 없다 → 귀속은 품번(base)→hero, 값은 수량만.
def season_inbound_since(season: str) -> str | None:
    """'26FW' → '20260601' · '26SS' → '20251201' (YYYYMMDD). 해석 불가면 None."""
    m = re.match(r"(\d{2})\s*(SS|FW)", str(season or "").strip(), re.I)
    if not m:
        return None
    yy, kind = 2000 + int(m.group(1)), m.group(2).upper()
    return f"{yy}0601" if kind == "FW" else f"{yy - 1}1201"


def aggregate_inbound_season(sheets, sheet_id, style_to_hero, since,
                             code2kor=None, kor2code=None, style_alias=None):
    """'입고일자별'(dt, sku_code=품번-컬러, inbound_qty) → dt >= since 인 실입고를 히어로/STY/컬러로 롤업.
    returns (hero_in, sty_in, color_in) — 각각 {"qty": n} (금액은 원천에 없음)."""
    hero_in = defaultdict(lambda: {"qty": 0.0})
    sty_in = defaultdict(lambda: {"qty": 0.0})
    color_in = defaultdict(lambda: {"qty": 0.0})
    s2h = {_base(k): v for k, v in (style_to_hero or {}).items()}
    alias = {_base(k): _base(v) for k, v in (style_alias or {}).items()}
    try:
        rows = read_tab(sheets, sheet_id, "입고일자별")
    except Exception as e:
        print(f"[inbound] '입고일자별' 탭 읽기 실패 — 시즌 입고 미반영: {e}")
        return None, None, None
    n = 0
    for row in rows:
        if str(row.get("dt") or "") < str(since):
            continue
        sku = str(row.get("sku_code") or "").strip()
        if not sku:
            continue
        base = alias.get(_base(sku), _base(sku))
        hero = s2h.get(base)
        if not hero:
            continue
        q = _f(row.get("inbound_qty"))
        if not q:
            continue
        n += 1
        hero_in[hero]["qty"] += q
        sty_in[(hero, base)]["qty"] += q
        col = sku.split("-", 1)[1] if "-" in sku else ""
        if col:
            kor = (code2kor or {}).get(col)
            color_in[(hero, base, f"{kor}({col})" if kor else col)]["qty"] += q
    print(f"[inbound] 시즌 입고({since}~): 히어로 {len(hero_in)} · 행 {n}")
    return hero_in, sty_in, color_in


def aggregate_inbound(sheets, sheet_id, goods_to_hero, code2kor=None, kor2code=None):
    hero_in = defaultdict(lambda: {"qty": 0.0, "amt_normal": 0.0, "amt_wonga": 0.0})
    sty_in = defaultdict(lambda: {"qty": 0.0, "amt_normal": 0.0, "amt_wonga": 0.0})
    color_in = defaultdict(lambda: {"qty": 0.0, "amt_normal": 0.0, "amt_wonga": 0.0})  # (hero, base, color)
    for row in read_tab(sheets, sheet_id, "입고현황"):
        try:
            gid = int(row.get("goods_no"))
        except (TypeError, ValueError):
            continue
        hero = (goods_to_hero.get(gid) or {}).get("hero")
        if not hero:
            continue
        base = _base(row.get("style_no") or "")
        q, an, aw = _f(row.get("inbound_qty")), _f(row.get("normal_price_amt")), _f(row.get("wonga_amt"))
        for D in (hero_in[hero], sty_in[(hero, base)]):
            D["qty"] += q; D["amt_normal"] += an; D["amt_wonga"] += aw
        # 컬러별: style_no '-컬러' suffix로 매출과 동일 컬러명 산출(95%). 통합UID(suffix無)는 '기타'→실컬러 미매칭(스킵).
        col = _color(row, code2kor, kor2code)
        if col and col != "기타":
            C = color_in[(hero, base, col)]
            C["qty"] += q; C["amt_normal"] += an; C["amt_wonga"] += aw
    return hero_in, sty_in, color_in


# ── PDP 유입→구매전환 퍼널 집계 (히어로·스타일별 × 기간) ──────────────────────
def aggregate_funnel(sheets, sheet_id, goods_to_hero, period_src=None):
    """'PDP퍼널' 탭(goods_no별 유입pdp_uv·구매purchase_uv) → 히어로/스타일 롤업.
    탭 없으면 빈 dict(퍼널 데이터 미반영, 앱은 '데이터 없음'). 전환율은 앱에서 buy/pdp.
    period_src = 슬롯→원천 period 오버라이드. 26FW는 누계가 시즌 누계라 {'YTD':'FWTD'}로 읽는다
    (매출 period_tabs와 같은 기간을 봐야 유입·전환이 누계와 어긋나지 않는다)."""
    hero_fn = defaultdict(lambda: {p: {"pdp": 0.0, "buy": 0.0} for p in PERIODS})
    sty_fn = defaultdict(lambda: {p: {"pdp": 0.0, "buy": 0.0} for p in PERIODS})
    src2slot = {(period_src or {}).get(p, p): p for p in PERIODS}   # 원천 period → 슬롯
    try:
        rows = read_tab(sheets, sheet_id, "PDP퍼널")
    except Exception as e:
        print(f"[funnel] 'PDP퍼널' 탭 읽기 스킵: {e}")
        return {}, {}
    seen_src = set()
    for row in rows:
        p = src2slot.get(str(row.get("period") or "").strip())
        if p is None:
            continue
        seen_src.add(p)
        try:
            gid = int(row.get("goods_no"))
        except (TypeError, ValueError):
            continue
        hero = (goods_to_hero.get(gid) or {}).get("hero")
        if not hero:
            continue
        base = _base(row.get("style_no") or "")
        pdp, buy = _f(row.get("pdp_uv")), _f(row.get("purchase_uv"))
        hero_fn[hero][p]["pdp"] += pdp; hero_fn[hero][p]["buy"] += buy
        sty_fn[(hero, base)][p]["pdp"] += pdp; sty_fn[(hero, base)][p]["buy"] += buy
    # ★오버라이드한 원천 period가 탭에 아직 없으면(잡 미완) 조용히 0을 보여주지 말고 통째로 끈다.
    for slot, src in (period_src or {}).items():
        if slot not in seen_src:
            print(f"[funnel] 원천 period '{src}'(슬롯 {slot}) 행 없음 — 퍼널 미반영(앱 '-')")
            return {}, {}
    return hero_fn, sty_fn


# ── DASHBOARD 조립 (앱용 JSON 구조) ─────────────────────────────────────────
# 압축: 지표는 SALES_METRICS 순서의 배열. 채널 t/o/f, cur=c/prev=p.
_GI, _QI = SALES_METRICS.index("gmv"), SALES_METRICS.index("qty")


def _arr(m):
    return [round(m[k]) for k in SALES_METRICS]


def _per_full(per):
    """히어로·스타일용: {기간:{c:{t,o,f}, p:{t,o,f}}} (지표배열)."""
    out = {}
    for p in PERIODS:
        cur, prev = per[p]["cur"], per[p]["prev"]
        e = {"c": {"t": _arr(cur["total"]), "o": _arr(cur["online"]), "f": _arr(cur["offline"])}}
        if prev["total"]["gmv"] or prev["total"]["qty"]:
            e["p"] = {"t": _arr(prev["total"]), "o": _arr(prev["online"]), "f": _arr(prev["offline"])}
        out[p] = e
    return out


def _per_color(per):
    """컬러용(경량): {기간:[gmv, qty, prev_gmv, revenue, net_take]} (당기 total + YoY/매총율용)."""
    out = {}
    for p in PERIODS:
        cur, prev = per[p]["cur"]["total"], per[p]["prev"]["total"]
        if not (cur["gmv"] or cur["qty"]):
            continue
        out[p] = [round(cur["gmv"]), round(cur["qty"]), round(prev["gmv"]),
                  round(cur["revenue"]), round(cur["net_take"])]
    return out


def _nonzero(per):
    return any(per[p]["cur"]["total"]["gmv"] or per[p]["prev"]["total"]["gmv"] for p in PERIODS)


def _ytd_gmv(per):
    return per["YTD"]["cur"]["total"]["gmv"]


def _goods_map_from_style(sheets, sheet_id, style2hero, season="26SS", goods_override=None):
    """매출/재고/입고 탭의 (goods_no, style_no) 쌍 + style→hero(★상품MAP 799 basis) → goods_no→{hero,season}.
    build_maps(큐레이션 파싱) 대체 — IMC 성과 탭과 동일 히어로 정의로 홈 실적 통일(사용자 지시 2026-07-08:
    옛 전사시트 '26년 히어로 실적 대시보드' 폐기, 누판 799uid 단일 기준)."""
    g2h = {}
    for tab in ("YTD", "MTD", "WEEK", "DAY", "잔여재고", "입고현황"):
        try:
            rows = read_tab(sheets, sheet_id, tab)
        except Exception:
            continue
        for row in rows:
            try:
                gid = int(row.get("goods_no"))
            except (TypeError, ValueError):
                continue
            if gid in g2h:
                continue
            hero = style2hero.get(_base(row.get("style_no") or ""))
            if hero:
                g2h[gid] = {"hero": hero, "season": season}
    if goods_override:                    # uid 명시 매핑(시트39 uid + 사용자 PIN) 우선 반영
        for gid, hero in goods_override.items():
            if hero:
                g2h[int(gid)] = {"hero": hero, "season": season}
    return g2h


def build_dashboard(sheets, drive, sheet_id, as_of, style2hero=None, goods2hero=None,
                    period_tabs=None, force_season=None, with_funnel=True, funnel_periods=None,
                    style_meta=None, include_all_styles=False, goods_to_style=None,
                    with_targets=True, targets_map=None, prep_map=None, inbound_season=None):
    # period_tabs    = 기간→탭 오버라이드(26FW 누계=FWTD)
    # force_season   = 히어로 시즌 배지를 이 값으로 고정(26FW 블록)
    # with_funnel    = PDP퍼널 탭 조인 여부
    # funnel_periods = 퍼널 슬롯→원천 period 오버라이드(26FW: {'YTD':'FWTD'}). 매출 기간과 맞춘다.
    # with_targets   = 목표(달성율)·준비물량(소진율) 부착 여부.
    #   ★26FW는 False — 목표 소스('히어로목표(거래량)' 탭)가 26SS 시즌 목표(1/1~ 일별)이고 준비물량도
    #   '현재 타겟시즌'(7월=26SS) 발주라, 26FW 누계(7/1~)와 비교하면 무조건 미달로 보인다(사용자 지적 2026-07-30).
    # targets_map    = 목표를 외부에서 주입({base: {tq, prep}}). 26FW는 `target_26fw` 파서 결과를 넣는다.
    # prep_map       = 준비물량을 외부에서 주입({base: {t,o,f}}). 26FW는 목표 시트 '준비수량'.
    # inbound_season = 시즌 라벨('26FW'/'26SS'). 주면 입고를 그 시즌 창으로 집계(FW 6/1~ · SS 전년 12/1~).
    #   미지정이면 종전대로 '입고현황'(2025-11-01~ 누적).
    # style_meta     = {품번: {grade, hero, name}} (MSTRD HERO STY). STY 행에 HERO/HERO SUB 등급을 붙인다.
    # include_all_styles = 매출 0인 STY도 노출(★HERO SUB는 대부분 발매 전이라 매출이 없어 리스트에서 통째로
    #   빠져 'MAIN만 잡힌다'로 보였다. 발매 전 STY를 pending 행으로 함께 실어 커버리지를 보이게 한다).
    """앱이 읽을 DASHBOARD dict (raw 합계; 비율은 JS에서 계산).
    style2hero 주면 그 매핑(base 신품번→hero)으로 통일(성과 탭과 동일 총액).
    goods2hero(uid→hero) 주면 신품번 빈칸/누락 goods를 uid로 보강(시트39 26SS 정합)."""
    from soo.hero_ops.hero_goods_map import build_maps
    from soo.hero_ops.order_ingest import load_color_maps, parse_orders, current_season
    if style2hero:
        g2h = _goods_map_from_style(sheets, sheet_id, style2hero, goods_override=goods2hero)
        s2h = {_base(k): v for k, v in style2hero.items()}
        print(f"[dashboard] 799 basis 매핑: goods_no→hero {len(g2h)} · style→hero {len(s2h)}")
    else:
        g2h, s2h = build_maps(sheets)
    cur_season = current_season(as_of)          # 오늘 기준 현재 시즌(2~7월 SS / 8~1월 FW)
    # ★블록 시즌 = force_season 우선(2026-08-01 사고). 달이 바뀌어 current_season이 26FW로 넘어가는 순간
    #   26SS 블록의 히어로 시즌 배지(14종)와 준비물량 발주 시즌이 통째로 26FW로 뒤집혔다.
    #   블록이 어느 시즌인지는 호출자가 알고 있으므로(매핑 파일이 시즌별) 날짜가 아니라 그 값을 따른다.
    blk_season = force_season or cur_season
    # 컬러 크로스워크(코드↔한글) — 컬러명 '한글(코드)' 통일 + 오더 매칭용
    # ★준비물량은 오더시트(무탠본부) 발주수량을 블록 시즌만 집계 → 타시즌·미래발주 제외.
    try:
        code2kor, kor2code = load_color_maps(sheets)
        color_prep, style_prep = parse_orders(sheets, code2kor, kor2code, season=blk_season)
        if not with_targets:      # 26FW: 오더시트 준비물량은 현재 타겟시즌(7월=26SS) 기준이라 어긋난다
            color_prep, style_prep = {}, {}
        if prep_map is not None:  # 26FW: 목표 시트의 '준비수량'을 소진율 분모로 쓴다
            style_prep = dict(prep_map)
    except Exception as e:                      # 오더시트 접근 실패해도 대시보드는 생성
        print(f"[order_ingest] 스킵: {e}")
        code2kor, kor2code, color_prep, style_prep = {}, {}, {}, {}
    heroes, stats = aggregate(sheets, sheet_id, g2h, code2kor, kor2code,
                              style_to_hero=(s2h if style2hero else None),
                              period_tabs=period_tabs, goods_to_style=goods_to_style)
    hero_stock, sty_stock, color_stock = aggregate_stock(sheets, sheet_id, g2h, code2kor, kor2code)
    hero_in, sty_in, color_inbound = aggregate_inbound(sheets, sheet_id, g2h, code2kor, kor2code)
    inb_from = None
    if inbound_season:
        # 시즌 창 입고(FW 6/1~ / SS 전년 12/1~) — 실패하면 누적 입고 유지(조용한 0 방지)
        _since = season_inbound_since(inbound_season)
        _hi, _si, _ci = aggregate_inbound_season(sheets, sheet_id, style2hero, _since,
                                                 code2kor, kor2code) if _since else (None, None, None)
        if _hi is not None:
            hero_in, sty_in, color_inbound = _hi, _si, _ci
            inb_from = f"{_since[:4]}-{_since[4:6]}-{_since[6:]}"
        else:
            print(f"[inbound] 시즌 창 집계 실패 — '입고현황' 누적값 유지({inbound_season})")
    if with_funnel:
        hero_funnel, sty_funnel = aggregate_funnel(sheets, sheet_id, g2h, period_src=funnel_periods)
    else:
        hero_funnel, sty_funnel = {}, {}   # 퍼널 끔 → 빈값(앱은 '-')
    # 목표 = 주입값 우선(26FW) → 없으면 26SS 소스('히어로목표(거래량)') → with_targets=False면 미설정
    targets = targets_map if targets_map is not None else (parse_targets(sheets, as_of) if with_targets else {})

    # 히어로명 → 시즌 (g2h 값에서)
    hero_season = {}
    for v in g2h.values():
        hero_season.setdefault(v["hero"], v["season"])

    # 히어로별 목표 (신품번 → 히어로 합산). tq=기간별 목표판매량, prep=준비물량(시즌)
    _CH = ("t", "o", "f")

    def _blank_target():
        return {"tq": {p: {k: 0.0 for k in _CH} for p in PERIODS},
                "prep": {k: 0.0 for k in _CH}}

    hero_target = defaultdict(_blank_target)
    sty_target = {}                       # 신품번base → target (per-style, 코드 맞을 때만 sty에 부착)
    for style, t in targets.items():
        sty_target[style] = t
        hero = s2h.get(style)
        if not hero:
            continue
        HT = hero_target[hero]
        for p in PERIODS:
            for k in _CH:
                HT["tq"][p][k] += t["tq"][p][k] or 0
    # 준비물량(prep) = 오더시트 현재 타겟시즌 발주수량 기준(목표 탭 prep 대체).
    #   → 시즌 누적 합산 버그 해소 + 목표 탭에 없는 캐리오버 히어로(슬랙스/양말 등)도 채워짐.
    for base, sp in style_prep.items():
        hero = s2h.get(base)
        if not hero:
            continue
        HT = hero_target[hero]
        for k in _CH:
            HT["prep"][k] += sp.get(k, 0) or 0
    # 현재 타겟시즌 발주가 있는 히어로 = 현재 시즌 멤버(캐리오버 26FW→26SS 배지 보정용)
    order_heroes = {s2h[b] for b in style_prep if b in s2h}

    def _tgt(d):
        # 목표/준비 모두 0이면 None (목표 미설정 히어로)
        has = any(d["tq"]["YTD"][k] for k in _CH) or any(d["prep"][k] for k in _CH)
        if not has:
            return None
        return {
            "tq": {p: {k: round(d["tq"][p][k]) or None for k in _CH} for p in PERIODS},
            "prep": {k: round(d["prep"][k]) or None for k in _CH},
        }

    def _sty_tgt(base):
        # 스타일 목표 = 거래량(target_ingest) + 준비물량(오더시트 현재 타겟시즌).
        t = sty_target.get(base)
        sp = style_prep.get(base)
        has_tq = bool(t) and any(t["tq"]["YTD"][k] for k in _CH)
        has_prep = bool(sp) and any(sp.get(k) for k in _CH)
        if not (has_tq or has_prep):
            return None
        return {
            "tq": {p: {k: ((round(t["tq"][p][k]) or None) if t else None) for k in _CH} for p in PERIODS},
            "prep": {k: ((round(sp[k]) or None) if sp else None) for k in _CH},
        }

    def _stock(d):
        if not d.get("qty"):
            return None
        out = {"qty": round(d["qty"]), "amt_normal": round(d["amt_normal"]), "amt_wonga": round(d["amt_wonga"])}
        if "by_type" in d:
            out["by_type"] = {t: round(d["by_type"][t]) for t in STOCK_TYPES}
        return out

    def _inb(d):
        # ★시즌 입고('입고일자별')는 원천에 금액이 없어 수량만 온다 → 금액 키는 있을 때만 싣는다.
        if not (d or {}).get("qty"):
            return None
        o = {"qty": round(d["qty"])}
        for _k in ("amt_normal", "amt_wonga"):
            if d.get(_k):
                o[_k] = round(d[_k])
        return o

    def _funnel(fn):
        # {기간:[유입pdp_uv, 구매purchase_uv]} (둘 다 0인 기간 생략). 전환율은 앱에서 buy/pdp.
        if not fn:
            return None
        out = {}
        for p in PERIODS:
            d = fn.get(p) or {}
            pdp, buy = round(d.get("pdp", 0)), round(d.get("buy", 0))
            if pdp or buy:
                out[p] = [pdp, buy]
        return out or None

    def _color_obj(col, cp, hero, base):
        v = _per_color(cp)                          # {기간:[gmv,qty,prev_gmv,rev,net]}
        # 컬러 준비물량(=오더 발주수량) + 안분 목표(스타일목표×발주비중)
        cprep = color_prep.get((base, col))
        sprep = (style_prep.get(base) or {}).get("t") or 0
        st = sty_target.get(base)
        weight = (cprep / sprep) if (cprep and sprep) else None
        for p, arr in v.items():
            tgt = 0.0
            if weight and st:
                tq = (st.get("tq") or {}).get(p) or {}
                tgt = (tq.get("t") or 0) * weight
            arr.append(round(tgt))                  # 6번째 = 컬러 안분목표(기간, total채널)
        o = {"color": col, "v": v}
        if cprep:
            o["prep"] = round(cprep)                # 컬러 준비물량 — 소진율 분모
        cs = color_stock.get((hero, base, col))
        s = _stock(cs) if cs else None
        if s:
            o["stock"] = s
        ci = color_inbound.get((hero, base, col))
        ib = _inb(ci) if ci else None
        if ib:
            o["inbound"] = ib
        return o

    # 매핑된 STY를 히어로별로 — 매출 0인 STY(발매 전)도 리스트에 싣기 위한 소스
    smeta = {_base(k): v for k, v in (style_meta or {}).items()}
    sty_by_hero = defaultdict(list)
    for _b, _m in smeta.items():
        sty_by_hero[_m.get("hero")].append(_b)

    out_heroes = []
    order = sorted(heroes, key=lambda h: -_ytd_gmv(heroes[h]["periods"]))
    for hero in order:
        H = heroes[hero]
        # 스타일 (매출 있는 것만, YTD GMV desc)
        sty_items = [(b, S) for b, S in H["stys"].items() if _nonzero(S["periods"])]
        sty_items.sort(key=lambda bs: -_ytd_gmv(bs[1]["periods"]))
        stys = []
        for base, S in sty_items:
            cols = [(col, cp) for col, cp in S["colors"].items() if _nonzero(cp)]
            cols.sort(key=lambda cc: -_ytd_gmv(cc[1]))
            stys.append({
                "style": base, "team": S["team"], "category": S["category1"], "md": S["md_name"],
                "grade": (smeta.get(base) or {}).get("grade"),
                "periods": _per_full(S["periods"]),
                "stock": _stock(sty_stock.get((hero, base))) if (hero, base) in sty_stock else None,
                "inbound": _inb(sty_in.get((hero, base))) if (hero, base) in sty_in else None,
                "target": _sty_tgt(base),
                "funnel": _funnel(sty_funnel.get((hero, base))),
                "colors": [_color_obj(col, cp, hero, base) for col, cp in cols],
            })
        if include_all_styles:
            # 매출 0(발매 전) STY — HERO SUB 커버리지가 보이게 뒤에 붙인다. periods 비움 → 앱은 '발매 전'.
            _have = {s["style"] for s in stys}
            for base in sorted(sty_by_hero.get(hero, [])):
                if base in _have:
                    continue
                stys.append({
                    "style": base, "team": None, "category": None, "md": None,
                    "grade": (smeta.get(base) or {}).get("grade"), "pending": 1,
                    "periods": {}, "stock": _stock(sty_stock.get((hero, base))) if (hero, base) in sty_stock else None,
                    "inbound": _inb(sty_in.get((hero, base))) if (hero, base) in sty_in else None,
                    "target": _sty_tgt(base), "funnel": None, "colors": [],
                })
        out_heroes.append({
            "name": hero,
            "season": force_season or (blk_season if hero in order_heroes else hero_season.get(hero, blk_season)),
            "periods": _per_full(H["periods"]),
            "target": _tgt(hero_target[hero]) if hero in hero_target else None,
            "stock": _stock(hero_stock[hero]) if hero in hero_stock else None,
            "inbound": _inb(hero_in[hero]) if hero in hero_in else None,
            **({"inbound_from": inb_from} if inb_from else {}),
            "funnel": _funnel(hero_funnel.get(hero)),
            "stys": stys,
        })
    return {
        "as_of": as_of,
        "periods": PERIODS,
        "channels": ["total", "online", "offline"],
        "metrics": SALES_METRICS,
        "heroes": out_heroes,
        "_stats": {"rows": stats["rows"], "mapped": stats["mapped"],
                   "unmapped_goods": len(stats["unmapped_goods"])},
    }


def _tgt_or_none(t):
    """스타일(신품번)용 목표 — parse_targets 구조 그대로(0은 None화)."""
    if not t:
        return None
    _CH = ("t", "o", "f")
    has = any(t["tq"]["YTD"][k] for k in _CH) or any(t["prep"][k] for k in _CH)
    if not has:
        return None
    return {
        "tq": {p: {k: (t["tq"][p][k] or None) for k in _CH} for p in PERIODS},
        "prep": {k: (t["prep"][k] or None) for k in _CH},
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from soo.auth import get_credentials, build_services
    ROOT = Path(__file__).resolve().parents[2]
    _svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sheets, drive = _svc["sheets"], _svc["drive"]

    from soo.hero_ops.hero_goods_map import build_maps
    g2h, s2h = build_maps(sheets)
    print(f"매핑: goods_no→hero {len(g2h)} / 신품번→hero {len(s2h)}")
    heroes, stats = aggregate(sheets, SALES_SHEET_ID, g2h)
    print(f"매출 행 {stats['rows']} (매핑 {stats['mapped']}, 미매핑 goods {len(stats['unmapped_goods'])}종)")

    def 억(v):
        return f"{v/1e8:.2f}억"
    order = sorted(heroes, key=lambda h: -heroes[h]["periods"]["YTD"]["cur"]["total"]["gmv"])
    print(f"\n{'히어로':18} {'YTD GMV(T)':>11} {'(On)':>9} {'(Off)':>9} {'YTD수량':>9} {'전년YTD':>9}")
    for hero in order:
        y = heroes[hero]["periods"]["YTD"]
        c, p = y["cur"], y["prev"]
        print(f"{hero[:18]:18} {억(c['total']['gmv']):>11} {억(c['online']['gmv']):>9} "
              f"{억(c['offline']['gmv']):>9} {c['total']['qty']:>9.0f} {억(p['total']['gmv']):>9}")
    # 교차검증 (전사 시트 R12: 워셔블 46.42억/99559, 커브드 34.16억/69949, 윈드 56.66억)
    print("\n[교차검증 vs 전사 R12]")
    for name, ref in [("워셔블 수피마", "46.42억/99559"), ("커브드 팬츠", "34.16억/69949"),
                      ("윈드 브레이커", "56.66억"), ("심리스 브라", "1.84억/6494")]:
        if name in heroes:
            t = heroes[name]["periods"]["YTD"]["cur"]["total"]
            print(f"  {name:14} 집계 {억(t['gmv'])}/{t['qty']:.0f}  (전사 {ref})")

    # DASHBOARD 조립 테스트
    import json
    dash = build_dashboard(sheets, drive, SALES_SHEET_ID, "2026-06-10")
    js = json.dumps(dash, ensure_ascii=False)
    print(f"\n[DASHBOARD] 히어로 {len(dash['heroes'])}개 · JSON {len(js)/1024:.0f}KB · metrics={dash['metrics']}")
    for h in dash["heroes"][:4]:
        ty = h["periods"]["YTD"]["c"]["t"]      # [지표배열]
        gmv, qty = ty[_GI], ty[_QI]
        tg = ((h.get("target") or {}).get("tq") or {}).get("YTD", {}).get("t")
        ach = f"{qty/tg*100:.0f}%" if tg else "-"
        print(f"  {h['name'][:14]:14}[{h['season']}] YTD {억(gmv)}/{qty} 목표{tg} 달성{ach} "
              f"재고{(h.get('stock') or {}).get('qty','-')} 입고{(h.get('inbound') or {}).get('qty','-')} "
              f"스타일{len(h['stys'])} 1번째컬러{len(h['stys'][0]['colors']) if h['stys'] else 0}")
