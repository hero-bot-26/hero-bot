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
def aggregate(sheets, sheet_id, goods_to_hero, code2kor=None, kor2code=None):
    # hero -> {periods:{P:{cur:channels, prev:channels}}, stys:{base:{name, periods, colors:{opt:periods}}}}
    heroes = defaultdict(lambda: {
        "periods": {p: {"cur": _blank_channels(), "prev": _blank_channels()} for p in PERIODS},
        "stys": defaultdict(lambda: {
            "name": "", "team": "", "category1": "", "md_name": "",
            "periods": {p: {"cur": _blank_channels(), "prev": _blank_channels()} for p in PERIODS},
            "colors": defaultdict(lambda: {p: {"cur": _blank_channels(), "prev": _blank_channels()} for p in PERIODS}),
        }),
    })
    stats = {"rows": 0, "mapped": 0, "unmapped_goods": set()}

    for period, (cur_tab, prev_tab) in PERIOD_TABS.items():
        for when, tab in (("cur", cur_tab), ("prev", prev_tab)):
            for row in read_tab(sheets, sheet_id, tab):
                stats["rows"] += 1
                try:
                    gid = int(row.get("goods_no"))
                except (TypeError, ValueError):
                    continue
                hero = (goods_to_hero.get(gid) or {}).get("hero")
                if not hero:
                    stats["unmapped_goods"].add(gid)
                    continue
                stats["mapped"] += 1
                base = _base(row.get("style_no") or "")
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
def aggregate_funnel(sheets, sheet_id, goods_to_hero):
    """'PDP퍼널' 탭(goods_no별 유입pdp_uv·구매purchase_uv) → 히어로/스타일 롤업.
    탭 없으면 빈 dict(퍼널 데이터 미반영, 앱은 '데이터 없음'). 전환율은 앱에서 buy/pdp."""
    hero_fn = defaultdict(lambda: {p: {"pdp": 0.0, "buy": 0.0} for p in PERIODS})
    sty_fn = defaultdict(lambda: {p: {"pdp": 0.0, "buy": 0.0} for p in PERIODS})
    try:
        rows = read_tab(sheets, sheet_id, "PDP퍼널")
    except Exception as e:
        print(f"[funnel] 'PDP퍼널' 탭 읽기 스킵: {e}")
        return {}, {}
    for row in rows:
        p = str(row.get("period") or "").strip()
        if p not in PERIODS:
            continue
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


def build_dashboard(sheets, drive, sheet_id, as_of):
    """앱이 읽을 DASHBOARD dict (raw 합계; 비율은 JS에서 계산)."""
    from soo.hero_ops.hero_goods_map import build_maps
    from soo.hero_ops.order_ingest import load_color_maps, parse_orders
    g2h, s2h = build_maps(sheets)
    # 컬러 크로스워크(코드↔한글) — 컬러명 '한글(코드)' 통일 + 오더 매칭용
    try:
        code2kor, kor2code = load_color_maps(sheets)
        color_prep, style_prep = parse_orders(sheets, code2kor, kor2code)
    except Exception as e:                      # 오더시트 접근 실패해도 대시보드는 생성
        print(f"[order_ingest] 스킵: {e}")
        code2kor, kor2code, color_prep, style_prep = {}, {}, {}, {}
    heroes, stats = aggregate(sheets, sheet_id, g2h, code2kor, kor2code)
    hero_stock, sty_stock, color_stock = aggregate_stock(sheets, sheet_id, g2h, code2kor, kor2code)
    hero_in, sty_in, color_inbound = aggregate_inbound(sheets, sheet_id, g2h, code2kor, kor2code)
    hero_funnel, sty_funnel = aggregate_funnel(sheets, sheet_id, g2h)   # PDP 유입→구매전환
    targets = parse_targets(sheets, as_of)   # 기간별(YTD/MTD/WEEK/DAY) 누적 목표

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
        for k in _CH:
            HT["prep"][k] += t["prep"][k] or 0

    def _tgt(d):
        # 목표/준비 모두 0이면 None (목표 미설정 히어로)
        has = any(d["tq"]["YTD"][k] for k in _CH) or any(d["prep"][k] for k in _CH)
        if not has:
            return None
        return {
            "tq": {p: {k: round(d["tq"][p][k]) or None for k in _CH} for p in PERIODS},
            "prep": {k: round(d["prep"][k]) or None for k in _CH},
        }

    def _stock(d):
        if not d.get("qty"):
            return None
        out = {"qty": round(d["qty"]), "amt_normal": round(d["amt_normal"]), "amt_wonga": round(d["amt_wonga"])}
        if "by_type" in d:
            out["by_type"] = {t: round(d["by_type"][t]) for t in STOCK_TYPES}
        return out

    def _inb(d):
        return {"qty": round(d["qty"]), "amt_normal": round(d["amt_normal"]),
                "amt_wonga": round(d["amt_wonga"])} if d["qty"] else None

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
        sprep = style_prep.get(base) or 0
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
                "periods": _per_full(S["periods"]),
                "stock": _stock(sty_stock.get((hero, base))) if (hero, base) in sty_stock else None,
                "inbound": _inb(sty_in.get((hero, base))) if (hero, base) in sty_in else None,
                "target": _tgt_or_none(sty_target.get(base)),
                "funnel": _funnel(sty_funnel.get((hero, base))),
                "colors": [_color_obj(col, cp, hero, base) for col, cp in cols],
            })
        out_heroes.append({
            "name": hero,
            "season": hero_season.get(hero, "26SS"),
            "periods": _per_full(H["periods"]),
            "target": _tgt(hero_target[hero]) if hero in hero_target else None,
            "stock": _stock(hero_stock[hero]) if hero in hero_stock else None,
            "inbound": _inb(hero_in[hero]) if hero in hero_in else None,
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
