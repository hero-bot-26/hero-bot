# -*- coding: utf-8 -*-
"""입고-발매 대시보드 · 발매 파이프라인 — 시즌 작업의뢰 '발매1'(발매일) 기준 스타일별 입고 → 온라인 → 분배.

왜 있나 (2026-10-22, 사용자 지시)
  "발매일은 매 시즌 생성되는 작업의뢰 시트에서 알 수 있다" — 아이템마스터 발매일정은 "발매 완료 상품만
  업데이트"(시트 10행 문구)라 앞으로의 발매가 비어 있었다(예정 28건). 작업의뢰 '발매1' 은 26FW 744행(6/3~12/9),
  27SS 705행(12월~5월)으로 시즌 전체 계획을 들고 있다. 첫 화면을 이 목록 기준 **스타일 한 줄 파이프라인**으로
  바꾸고, "언제 발매되더라"용 발매 캘린더도 같은 행을 쓴다.

원천
  발매 계획 = 레지스트리 `launch_<시즌>` (source_registry DEFAULTS · 앱 '소스' 탭에서 교체). 시즌이 늘면 키만 추가.
    헤더는 두 줄(묶음 라벨 + 세부 라벨)을 **라벨로** 찾는다 — 26FW 는 8행·히어로/디자인실/bizest재고 열이 더 있고
    27SS 는 7행이라 같은 항목도 열이 밀려 있다([[CLAUDE 1-1]]). '예정일' 이 두 번(촬샘입고·상품입고) 나오므로 묶음 라벨로 가른다.
  입고 = asn_ingest.ASN_STYLE_SQL(무탠 전 상품 ASN 240일 · 품번×납품일, 중계 spec 'asn_style') — 시즌 전체를 WMS 기록으로 덮는다
         ★작업의뢰 '실 입고일'(수기)은 상태 판정에서 뺐다(사용자 지시 2026-10-22). 45일짜리 `_OPS_ASN` 을 쓰던 땐 6~8월 입고를
           그 수기 칸으로 메우고 있었다.
  온라인 = release_log 판매중(40) 전환 — 품번 → UID 는 아이템마스터 '무탠' 탭(E 대표품번 · C UID)
  분배 = 앱 시트 `_OPS_DIST`·`_OPS_STORES`(dist_board, 매장 기준 = 부문 대시보드 '오프라인' 탭)
  ★이 셋은 같은 CI 런에서 앞 스텝이 방금 쓴 탭을 읽는다 — 워크플로에서 asn_board·dist_board 뒤에 둘 것.

판정 (그레인 = 시즌 × 발매일 × 스타일)
  입고  : 입고 완료(WMS 업체 입고, 품번 직접·ASN 무관) > ASN 통보만 > 입고 전
  온라인: 발매일 −7일 이후 첫 판매중 전환(launch_board 와 같은 규칙). 오프라인전용은 판정 제외
  분배  : 목록 매장 중 받은 매장 수(컬러 합집합). 온라인전용은 판정 제외
  위험  : 발매 D-3 이내인데 입고 흔적 없음 / 발매일 지났는데 판매중 전환 없음
          ★'매장 0곳'은 위험으로 안 본다 — 모든 상품이 매장에 가지 않는데 작업의뢰엔 오프라인 계획 칸이 없어
            판매중인데 출고 0 인 스타일이 9~10월에만 86건(10/22). 상태(미분배)로만 보인다.
  창    : 입고·출고는 발매일 앞뒤 창 안의 것만(캐리오버 품번 오귀속 방지). 원천 보관 기간보다 오래된 발매는 '기록 없음'
  상태명: '대기' 대신 입고 전·출고 전(사용자 지시 — '대기'는 쓰지 않는다)

사용법
  python -m soo.hero_ops.launch_plan            # 드라이런
  python -m soo.hero_ops.launch_plan --apply    # 앱 시트 `_OPS_PLAN` 기입
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops import release_log, source_registry, work_req
from soo.hero_ops.imc_triggers import MUTAN_SHEET, MUTAN_TAB

HERO = Path(__file__).resolve().parents[2]
APP_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"
TAB = "_OPS_PLAN"
EARLY_DAYS = 7           # launch_board 와 같은 값 — 발매일 −7일 이후 첫 전환을 그 발매로 본다
INBOUND_RISK_DAYS = 3    # 발매 D-3 이내인데 입고 흔적이 없으면 위험
# 같은 품번이 여러 시즌에 걸친다(캐리오버) — 발매일 앞뒤 창 안의 입고·출고만 그 발매로 본다.
#   창이 없으면 27SS 12월 발매에 26FW 입고가 '확정'으로 붙었다(10/22 실측 27SS 12~1월 '확정' 18건).
ASN_WINDOW = (-75, 30)   # 납품예정일이 발매일 −75~+30일
DIST_FROM = -14          # 매장 출고는 발매일 −14일 이후
DATA_DAYS = 40           # 분배 원천은 최근 45일 — 이보다 오래된 발매의 분배는 '기록 없음'
INB_DATA_DAYS = 160      # 입고 원천(ASN_STYLE_SQL)은 240일 — 발매 −75일 창을 빼면 160일 전 발매까지 온전하다
MIN_ROWS = 200           # 시즌 하나당 실측 700행대 — 이보다 적으면 탭을 반쯤 읽은 것

HEADER = ["season", "plan", "style", "line", "item", "type", "hero", "name", "sku", "plan_qty",
          "inb_plan", "inb_manual", "asn_qty", "recv_qty", "asn_last", "recv_last",
          "online_at", "online_diff", "uids", "dist_qty", "dist_shops", "dist_total", "dist_last",
          "inb_state", "online_state", "dist_state", "risk", "issue", "sourcing", "row_note", "hero_series",
          "recv_first", "preorder", "preorder_src", "preorder_ship"]

# 헤더 (묶음 라벨, 세부 라벨) → 필드. 묶음이 None 이면 세부 라벨만 본다. 같은 라벨이 여럿이면 **첫 것**.
COLS = {
    "plan": (None, "발매일"), "style": (None, "스타일넘버"), "line": (None, "구분"), "item": (None, "아이템"),
    "type": (None, "컬러추가"), "hero": (None, "히어로"), "name": (None, "스타일명"), "sku": (None, "SKU"),
    "plan_qty": (None, "기획수량"), "inb_plan": ("상품입고", "예정일"), "inb_manual": (None, "실 입고일"),
    "issue": (None, "발매 이슈"), "sourcing": ("소싱담당", None), "preorder": (None, "예발여부"),
}
REQUIRED = ("plan", "style", "name")


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _kst_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) + _dt.timedelta(hours=9)


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def parse_launch(grid: list[list[str]], season: str) -> list[dict]:
    """'발매1' 그리드 → 행 목록. 헤더 행 = 세부 라벨에 '발매일'·'스타일넘버'가 같이 있는 행(위 15행 안)."""
    hi = next((i for i, r in enumerate(grid[:15]) if "발매일" in r and "스타일넘버" in r), None)
    if hi is None or hi == 0:
        raise ValueError(f"[{season}] 발매1 헤더(발매일·스타일넘버) 못 찾음")
    grp, sub = grid[hi - 1], grid[hi]
    g = lambda j: grp[j].strip() if j < len(grp) else ""
    ix: dict[str, int] = {}
    for f, (gl, sl) in COLS.items():
        for j in range(len(sub)):
            if (gl is None or g(j) == gl) and (sl is None or sub[j].strip() == sl):
                ix[f] = j
                break
    miss = [f for f in REQUIRED if f not in ix]
    if miss:
        raise ValueError(f"[{season}] 발매1 필수 열 없음 {miss} — 헤더: {sub[:30]}")
    out = []
    for r in grid[hi + 1:]:
        c = lambda f: (r[ix[f]].strip() if f in ix and ix[f] < len(r) else "")
        sty = c("style")
        if not sty or sty in ("필터용",) or len(sty) < 6:
            continue
        raw = c("plan")
        plan = work_req.norm_date(raw)
        note = ""
        if plan and plan.year < 2000:          # 27SS 에 '1900-01-03' 6행 — 숫자만 들어간 칸(엑셀 일련번호 3)
            plan, note = None, f"발매일 칸 값 이상({raw})"
        elif not plan:
            note = f"발매일 미정({raw})" if raw else "발매일 비어 있음"
        out.append({"season": season, "plan": plan, "style": sty, "line": c("line"), "item": c("item"),
                    "type": c("type"), "hero": c("hero"), "name": c("name"), "sku": c("sku"),
                    "plan_qty": int(_num(c("plan_qty"))), "inb_plan": work_req.norm_date(c("inb_plan")),
                    "inb_manual": work_req.norm_date(c("inb_manual")), "issue": c("issue"),
                    "sourcing": c("sourcing"), "note": note, "preorder": c("preorder")})
    print(f"[plan] {season} 발매1 {len(out):,}행 (헤더 {hi + 1}행 · 열 {ix})")
    return out


def _ymd(v) -> str:
    """시트 값(20261022 · 20261022.0 · '2026-10-22') → 'YYYY-MM-DD'."""
    s = str(v or "").split(".")[0].strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def _read_tab(sheets, tab: str) -> list[dict]:
    v = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{tab}'!A1:AZ",
                                           valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    if len(v) < 3:
        raise RuntimeError(f"{tab} 탭이 비었다 — 앞 스텝(asn_board·dist_board)부터 볼 것")
    h = [str(x).strip() for x in v[1]]
    return [{k: (r[i] if i < len(r) else "") for i, k in enumerate(h)} for r in v[2:]]


def load_uids(sheets) -> dict[str, set]:
    """아이템마스터 → {대표품번: {UID…}} (전 컬러)."""
    v = sheets.spreadsheets().values().get(spreadsheetId=MUTAN_SHEET, range=f"'{MUTAN_TAB}'!C12:E30000").execute().get("values", [])
    out: dict[str, set] = collections.defaultdict(set)
    for r in v:
        uid = str(r[0]).strip() if r else ""
        rep = str(r[2]).strip() if len(r) > 2 else ""
        op = str(r[1]).strip() if len(r) > 1 else ""
        if uid.isdigit():
            for k in {rep, op[:9]} - {""}:
                out[k].add(uid)
    return out


def build(sheets, drive, today: _dt.date) -> tuple[list[list], collections.Counter]:
    reg = source_registry.load_registry(sheets)
    plans: list[dict] = []
    for key in [k for k in source_registry.DEFAULTS if k.startswith("launch_")]:
        src = source_registry.resolve(key, reg)
        season = key.split("_", 1)[1].upper()
        grid = work_req.load_grid(sheets, drive, src["id"], src.get("tab") or "발매1")
        rows = parse_launch(grid, season)
        if len(rows) < MIN_ROWS:
            raise RuntimeError(f"[{season}] 발매1 {len(rows)}행 < {MIN_ROWS} — 탭을 반쯤 읽은 것으로 보고 쓰지 않는다")
        plans += rows

    # 입고 — 전 상품 ASN 품번×납품일 집계(240일, 중계)
    from soo.hero_ops import asn_ingest, dbx_relay
    dbx_relay.configure(sheets, "asn_style", max_age_h=30)
    try:
        arows = dbx_relay.rows(asn_ingest.ASN_STYLE_SQL, print)
    finally:
        dbx_relay._cfg["sheets"] = None
    if len(arows) < 1000:
        raise RuntimeError(f"품번별 ASN {len(arows)}행 < 1000 — 부분 결과 의심")
    asn = collections.defaultdict(list)          # 품번 → [(납품예정일, 통보량)]
    wms = collections.defaultdict(list)          # 품번 → [(입고확정일, 확정량)] — WMS 업체 입고를 품번으로 바로(ASN 무관)
    for kind, sty_, d_, q_ in arows:
        e = work_req.norm_date(_ymd(d_))
        if e:
            (wms if kind == "wms" else asn)[str(sty_ or "").strip()].append((e, _num(q_)))
    # 분배 — 품번-컬러 요약을 품번으로 접는다(받은 매장은 컬러 합집합)
    stores = {str(s["shop_no"]): s for s in _read_tab(sheets, "_OPS_STORES")}
    in_list = {k for k, s in stores.items() if s.get("in_list") == "Y"}
    dist = collections.defaultdict(list)         # 품번 → [(최근 출고일, 출고, 받은 목록 매장들)] (컬러별)
    for d in _read_tab(sheets, "_OPS_DIST"):
        sh = {p[0] for p in (x.split(":") for x in str(d.get("shops", "")).split(","))
              if len(p) >= 2 and _num(p[1]) > 0 and p[0] in in_list}
        dist[str(d.get("style", "")).strip()].append(
            (work_req.norm_date(_ymd(d.get("last_dt"))), _num(d.get("done_qty")), sh))
    log = release_log.fetch(sheets)
    pre_log = release_log.fetch_preorder(sheets)
    uids = load_uids(sheets)
    # 히어로 이름 — 화면 딱지 '히어로 · 라이트다운'(사용자 지시 2026-10-23 "MAIN 말고 히어로라고").
    #   작업의뢰 '히어로' 칸(MAIN/SUB)은 26FW 에만 있어 HERO STY 매핑으로 보강한다(27SS 에도 붙게).
    from soo.hero_ops.inbound_board import build_pumbon2hero
    try:
        p2h = build_pumbon2hero(sheets)
    except Exception as e:                 # 딱지만 빠질 뿐 파이프라인은 계속
        print(f"[plan] ⚠ 히어로 매핑 실패 — 딱지 없이 진행: {type(e).__name__}: {e}")
        p2h = {}

    cnt = collections.Counter()
    grid = []
    for p in sorted(plans, key=lambda x: (x["plan"] or _dt.date(2100, 1, 1), x["style"])):
        sty, plan = p["style"], p["plan"]
        offline_only, online_only = "오프라인전용" in p["type"], "온라인전용" in p["type"]
        u = sorted(uids.get(sty, ()))
        # ★예약발매(사용자 지시 2026-10-23) = **비제스트 '출고예정 구분 → 예약 배송' 설정 이력**(release_log.PREORDER_SQL).
        #   그 스타일 UID(컬러) 중 하나라도 발매일 −30~+14일 안에 예약 배송으로 설정됐으면 예약발매, 예약 출고일도 함께.
        #   작업의뢰 '예발여부'는 보조 근거(src 표기만). ★'판매 시작이 입고보다 먼저'로 추정하던 규칙은 버렸다 —
        #   WMS 확정일 지연·컬러추가(기존 컬러 재고)·신발(WMS 밖 입고) 때문에 시스템 설정과 어긋났다.
        mark = "예발" in p["preorder"]
        ships = []
        if plan:
            lo_p, hi_p = plan - _dt.timedelta(days=30), plan + _dt.timedelta(days=14)
            ships = sorted({sd for x in u for (ts, sd) in pre_log.get(x, ()) if lo_p <= ts.date() <= hi_p and sd})
            sysp = any(lo_p <= ts.date() <= hi_p for x in u for (ts, _) in pre_log.get(x, ()))
        else:
            sysp = False
        pre = "Y" if sysp else ""
        pre_src = ("비제스트+작업의뢰" if mark else "비제스트") if sysp else ("작업의뢰만(비제스트 설정 없음)" if mark else "")
        pre_ship = (ships[0][4:6] + "/" + ships[0][6:8]) if ships else ""
        old = bool(plan) and plan < today - _dt.timedelta(days=DATA_DAYS)
        old_inb = bool(plan) and plan < today - _dt.timedelta(days=INB_DATA_DAYS)
        lo_a = plan + _dt.timedelta(days=ASN_WINDOW[0]) if plan else None
        hi_a = plan + _dt.timedelta(days=ASN_WINDOW[1]) if plan else None
        # ★예약발매는 판매를 먼저 켜고 물건이 나중에 온다 — 발매 +30일 창이면 예약 출고일이 늦은 건(10/21 발매·11/24 출고)이
        #   입고가 들어와도 영영 '입고 전'으로 남는다(사용자 지적 2026-10-23). 예약 출고일 +30일까지 늘린다. 딱지는 그대로.
        if plan and ships:
            last_ship = max(_dt.datetime.strptime(x, "%Y%m%d").date() for x in ships)
            hi_a = max(hi_a, last_ship + _dt.timedelta(days=ASN_WINDOW[1]))
        xs = [x for x in asn.get(sty, ()) if plan and lo_a <= x[0] <= hi_a]
        ws = [x for x in wms.get(sty, ()) if plan and lo_a <= x[0] <= hi_a]
        a = {"q": sum(x[1] for x in xs), "r": sum(x[1] for x in ws),
             "last": max((x[0].isoformat() for x in xs), default=""),
             "rlast": max((x[0].isoformat() for x in ws), default=""),
             "rfirst": min((x[0] for x in ws), default=None),
             "afirst": min((x[0] for x in xs), default=None)}
        if a["r"] > 0:
            inb = "입고 완료"
        elif a["q"] > 0:
            inb = "ASN 통보"
        elif old_inb:
            inb = "기록 없음"
        else:
            inb = "입고 전"
        # 온라인 — 발매일 −7 이후 첫 판매중 전환
        at = None
        if plan:
            lo = plan - _dt.timedelta(days=EARLY_DAYS)
            ts = sorted(t for x in u for t in log.get(x, ()) if t.date() >= lo)
            at = ts[0] if ts else None
        if offline_only:
            on = "제외"
        elif at:
            on = "판매중"
        elif not u:
            on = "UID 없음"
        elif plan and plan < today:
            on = "미전환"
        else:
            on = "예정"
        ys = [y for y in dist.get(sty, ()) if plan and y[0] and y[0] >= plan + _dt.timedelta(days=DIST_FROM)]
        dd = {"q": sum(y[1] for y in ys), "shops": set().union(*[y[2] for y in ys]) if ys else set(),
              "last": max((y[0].isoformat() for y in ys), default="")}
        n_sh = len(dd["shops"])
        if online_only:
            ds = "제외"
        elif n_sh >= max(1, int(len(in_list) * 0.9)):
            ds = "완료"
        elif n_sh > 0:
            ds = "진행"
        elif old:
            ds = "기록 없음"
        elif plan and plan <= today:
            ds = "미분배"
        else:
            ds = "출고 전"
        risk = []
        if plan:
            dleft = (plan - today).days
            if 0 <= dleft <= INBOUND_RISK_DAYS and inb == "입고 전":
                risk.append(f"입고 흔적 없음 D-{dleft}")
            if dleft < 0 and on in ("미전환",):
                risk.append(f"발매일 지났는데 미전환 D+{-dleft}")
        if pre_src:
            cnt["preorder:" + pre_src] += 1
        for r_ in risk:
            cnt["risk:" + r_.split(" D")[0]] += 1
        cnt[p["season"]] += 1
        grid.append([p["season"], plan.isoformat() if plan else "", sty, p["line"], p["item"], p["type"], p["hero"],
                     p["name"], p["sku"], p["plan_qty"],
                     p["inb_plan"].isoformat() if p["inb_plan"] else "", p["inb_manual"].isoformat() if p["inb_manual"] else "",
                     int(a["q"]), int(a["r"]), a["last"], a["rlast"],
                     at.strftime("%Y-%m-%d %H:%M") if at else "", str((at.date() - plan).days) if at and plan else "",
                     len(u), int(dd["q"]), n_sh, len(in_list), dd["last"],
                     inb, on, ds, " · ".join(risk), p["issue"][:200], p["sourcing"], p["note"],
                     p2h.get(sty, ""), a["rfirst"].isoformat() if a["rfirst"] else "", pre, pre_src, pre_ship])
    return grid, cnt


# ── 발매일 변경 감지(사용자 지시 2026-10-23 "발매일이 변경되면 알람") ─────────────────────────────
#   매 실행 (시즌, 품번) → 발매일 목록 스냅샷을 `_OPS_PLAN_SNAP` 에 두고, 직전 스냅샷과 비교한 차이를 `_OPS_PLAN_CHG` 원장에 남긴다.
#   한 품번이 여러 발매 행(컬러추가·재발매)을 가지므로 날짜 **집합**으로 비교: 빠진 날 ↔ 생긴 날을 순서대로 짝지어 '변경'.
#   짝이 안 맞는 추가·삭제는 기록하지 않는다(사용자 지시 — 기존 일정이 바뀐 것만 알림). 스냅샷에 없던 시즌도 무시.
#   ★반쯤 읽힌 시트가 '대량 변경'으로 보이지 않게 — 변경이 품번의 10%(최소 30건)를 넘으면 기록·스냅샷 갱신 없이 경고만.
#   알림 = 수영님 DM(notify.DEFAULT_TARGET), KST 08~20시에만 — 밤에 감지된 건 원장에 쌓였다가 아침 첫 실행에 나간다.
SNAP_TAB = "_OPS_PLAN_SNAP"
CHG_TAB = "_OPS_PLAN_CHG"
CHG_HEADER = ["detected_at", "season", "style", "name", "kind", "old", "new", "notified"]
# ★수신자 — 지금은 수영님 DM 한 명(사용자 지시 2026-10-23 "일단 나한테, 나중에 받는 사람을 엄청 늘려야"). 늘릴 땐 여기에
#   Slack ID 를 더하면 된다(담당자별로 자기 건만 보내려면 _notify_pending 에서 품번 → 담당자 매핑으로 묶을 것 — asn_notify 참조).
CHG_RECIPIENTS = ["U09BU1F85TR"]


def _snapshot(grid: list[list]) -> dict[str, dict]:
    ix = {h: i for i, h in enumerate(HEADER)}
    out: dict[str, dict] = {}
    for r in grid:
        k = f"{r[ix['season']]}|{r[ix['style']]}"
        e = out.setdefault(k, {"dates": [], "name": r[ix["name"]]})
        if r[ix["plan"]]:
            e["dates"].append(r[ix["plan"]])
    for e in out.values():
        e["dates"].sort()
    return out


def _ensure(sheets, tab: str, header: list) -> list[list]:
    meta = sheets.spreadsheets().get(spreadsheetId=APP_SHEET_ID, fields="sheets.properties(title)").execute()
    if not any(x["properties"]["title"] == tab for x in meta["sheets"]):
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"addSheet": {"properties": {
            "title": tab, "hidden": True, "gridProperties": {"rowCount": 3000, "columnCount": len(header)}}}}]}).execute()
        sheets.spreadsheets().values().update(spreadsheetId=APP_SHEET_ID, range=f"'{tab}'!A1", valueInputOption="RAW",
                                              body={"values": [header]}).execute()
        return []
    return sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{tab}'!A2:{_col(len(header))}").execute().get("values", [])


def track_changes(sheets, grid: list[list], now: _dt.datetime, notify: bool = True) -> list[list]:
    """스냅샷 비교 → 변경 원장 append + 스냅샷 교체 + (시간대면) 미발송분 DM. 새로 기록한 변경 행을 돌려준다."""
    cur = _snapshot(grid)
    prev_rows = _ensure(sheets, SNAP_TAB, ["key", "name", "dates"])
    prev = {r[0]: {"name": r[1] if len(r) > 1 else "", "dates": [d for d in (r[2] if len(r) > 2 else "").split(",") if d]}
            for r in prev_rows if r}
    stamp = now.strftime("%Y-%m-%d %H:%M")
    events: list[list] = []
    if prev:
        prev_seasons = {k.split("|", 1)[0] for k in prev}
        for k in sorted(set(cur) | set(prev)):
            season, sty = k.split("|", 1)
            if season not in prev_seasons:
                continue
            a = list(prev.get(k, {}).get("dates", []))
            b = list(cur.get(k, {}).get("dates", []))
            rem = [d for d in a if d not in b or a.count(d) > b.count(d)]
            add = [d for d in b if d not in a or b.count(d) > a.count(d)]
            rem, add = sorted(set(rem)), sorted(set(add))
            name = (cur.get(k) or prev.get(k) or {}).get("name", "")
            # ★'변경'만 기록한다(사용자 지시 2026-10-23 "원래 적혀 있던 일정이 바뀌었을 때만") — 새 발매 행 추가(컬러추가 등)·
            #   행 삭제·미정→날짜 확정은 알림 대상이 아니다. 빠진 날 ↔ 생긴 날이 짝을 이룰 때만 = 기존 일정이 옮겨진 것.
            for o_, n_ in zip(rem, add):
                events.append([stamp, season, sty, name, "변경", o_, n_, ""])
        limit = max(30, int(len(cur) * 0.1))
        if len(events) > limit:
            print(f"[plan] ⚠ 발매일 변화 {len(events)}건 > 한도 {limit} — 시트를 반쯤 읽은 것으로 보고 기록·스냅샷 갱신을 건너뛴다")
            return []
    else:
        print(f"[plan] 발매일 스냅샷 첫 생성 — 변경 비교는 다음 실행부터 ({len(cur):,}품번)")
    if events:
        _ensure(sheets, CHG_TAB, CHG_HEADER)
        sheets.spreadsheets().values().append(   # ★OVERWRITE — INSERT_ROWS 는 다른 탭 참조를 민다([[CLAUDE 1-6]])
            spreadsheetId=APP_SHEET_ID, range=f"'{CHG_TAB}'!A1", valueInputOption="RAW",
            insertDataOption="OVERWRITE", body={"values": events}).execute()
        print(f"[plan] 발매일 변화 {len(events)}건 기록 — " + " · ".join(f"{e[2]} {e[4]} {e[5] or '-'}→{e[6] or '-'}" for e in events[:8]))
    snap = [["key", "name", "dates"]] + [[k, v["name"], ",".join(v["dates"])] for k, v in sorted(cur.items())]
    sheets.spreadsheets().values().clear(spreadsheetId=APP_SHEET_ID, range=f"'{SNAP_TAB}'!A1:C", body={}).execute()
    meta = sheets.spreadsheets().get(spreadsheetId=APP_SHEET_ID, fields="sheets.properties(title,sheetId,gridProperties)").execute()
    sp = next(x["properties"] for x in meta["sheets"] if x["properties"]["title"] == SNAP_TAB)
    if sp["gridProperties"]["rowCount"] < len(snap) + 100:
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": sp["sheetId"], "gridProperties": {"rowCount": len(snap) + 500}},
            "fields": "gridProperties.rowCount"}}]}).execute()
    sheets.spreadsheets().values().update(spreadsheetId=APP_SHEET_ID, range=f"'{SNAP_TAB}'!A1", valueInputOption="RAW",
                                          body={"values": snap}).execute()
    if notify and 8 <= now.hour < 20:
        _notify_pending(sheets, now)
    return events


def format_changes(rows: list[list], now: _dt.datetime, test: bool = False) -> str:
    """원장 행(CHG_HEADER 순서) → 슬랙 본문. 시험 발송·실발송이 같은 함수를 쓴다."""
    todo = [(i, r) for i, r in enumerate(rows)]
    md = lambda x: f"{int(x[5:7])}/{int(x[8:10])}" if x else "-"
    lines = []
    for _, r in todo[:30]:
        old, new = (r[5] if len(r) > 5 else ""), (r[6] if len(r) > 6 else "")
        verb = {"변경": f"{md(old)} → {md(new)}", "추가": f"{md(new)} 발매 추가", "삭제": f"{md(old)} 발매 빠짐"}.get(r[4], r[4])
        lines.append(f"• [{r[1]}] {r[2]} {r[3][:30]} — {verb}")
    msg = ((f"[시험] " if test else "") + f"발매일 변경 {len(todo)}건 (작업의뢰 발매시트 · {now:%m/%d %H:%M} 감지)\n" + "\n".join(lines)
           + ("" if len(todo) <= 30 else f"\n… 외 {len(todo) - 30}건")
           + "\n→ https://hero-master-app.vercel.app/ops?tab=cal")
    return msg


def send_changes(msg: str) -> int:
    """CHG_RECIPIENTS 전원에게 DM. 성공 수를 돌려준다(토큰 없으면 0 — 로컬 시험)."""
    import os
    tok = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not tok:
        print("[plan] SLACK_BOT_TOKEN 없음 — 발매일 변경 알림 스킵")
        return 0
    from soo import persona
    ok = 0
    for target in CHG_RECIPIENTS:
        try:
            if persona.send_slack(msg, bot_token=tok, target=target, persona=persona.RANKING_BOT):
                ok += 1
        except Exception as e:
            print(f"[plan] 발매일 변경 알림 실패({target}): {type(e).__name__}: {e}")
    print(f"[plan] 발매일 변경 알림 {ok}/{len(CHG_RECIPIENTS)}명")
    return ok


def _notify_pending(sheets, now: _dt.datetime) -> None:
    _ensure(sheets, CHG_TAB, CHG_HEADER)            # 변경이 한 번도 없었으면 원장 탭이 아직 없다
    rows = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{CHG_TAB}'!A2:H").execute().get("values", [])
    todo = [(i, r) for i, r in enumerate(rows) if len(r) >= 5 and not (r[7] if len(r) > 7 else "").strip()]
    if not todo:
        return
    msg = format_changes([r for _, r in todo], now)
    if send_changes(msg) == 0:           # ★기록은 발송 성공 시에만([[CLAUDE 1-3]]) — 실패분은 다음 실행에 다시 나간다
        print(f"[plan] 발매일 변경 알림 미발송 {len(todo)}건 — 원장에 '보냄' 표시 안 함")
        return
    sent = now.strftime("%Y-%m-%d %H:%M")
    data = [{"range": f"'{CHG_TAB}'!H{i + 2}", "values": [[sent]]} for i, _ in todo]
    sheets.spreadsheets().values().batchUpdate(spreadsheetId=APP_SHEET_ID,
                                               body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"[plan] 발매일 변경 알림 {len(todo)}건 발송")


def write(sheets, grid: list[list], label: str) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=APP_SHEET_ID, fields="sheets.properties(title,sheetId,gridProperties)").execute()
    hit = next((s["properties"] for s in meta["sheets"] if s["properties"]["title"] == TAB), None)
    need = len(grid) + 500
    if hit is None:
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"addSheet": {"properties": {
            "title": TAB, "hidden": True, "gridProperties": {"rowCount": need, "columnCount": len(HEADER), "frozenRowCount": 2}}}}]}).execute()
    elif hit["gridProperties"]["rowCount"] < len(grid) + 2 or hit["gridProperties"]["columnCount"] < len(HEADER):
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": hit["sheetId"], "gridProperties": {"rowCount": need, "columnCount": len(HEADER)}},
            "fields": "gridProperties.rowCount,gridProperties.columnCount"}}]}).execute()
    body = [[label] + [""] * (len(HEADER) - 1), HEADER] + grid
    sheets.spreadsheets().values().clear(spreadsheetId=APP_SHEET_ID, range=f"'{TAB}'!A1:{_col(len(HEADER))}", body={}).execute()
    sheets.spreadsheets().values().update(spreadsheetId=APP_SHEET_ID, range=f"'{TAB}'!A1",
                                          valueInputOption="RAW", body={"values": body}).execute()
    back = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{TAB}'!C3:C").execute().get("values", [])
    if len(back) != len(grid):
        raise RuntimeError(f"[{TAB}] 되읽기 {len(back)}행 ≠ 기입 {len(grid)}행")
    print(f"[{TAB}] 기입 {len(grid):,}행 · 되읽기 일치")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    svc = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))
    now = _kst_now()
    grid, cnt = build(svc["sheets"], svc["drive"], now.date())
    print("[plan] " + " · ".join(f"{k} {v:,}" for k, v in sorted(cnt.items())))
    if a.apply:
        # 변경 감지는 화면 기입 **뒤에** — 실패하면 예외로 스텝을 실패시켜 워크플로 실패 알림이 오게 한다(조용히 삼키지 않는다).
        write(svc["sheets"], grid, f"발매 파이프라인 · 발매일=시즌 작업의뢰 '발매1' · 입고=WMS 업체 입고(품번) · 예약발매=비제스트 예약 배송 이력 "
                                   f"· 온라인=판매중 전환 · 분배=매장 출고(부문 대시보드 매장 기준) · 생성 {now:%Y-%m-%d %H:%M} KST")
        track_changes(svc["sheets"], grid, now)
    else:
        print("(드라이런 — --apply 로 기입)")


if __name__ == "__main__":
    main()
