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
  위험  : 발매 D-3 이내인데 입고 흔적 없음 / 예약 출고 D-3 인데 그 컬러 입고 전 / 발매 D-1·당일인데 상세페이지 미등록 컬러 / 발매일 지났는데 판매중 전환 없음
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
DETAIL_RISK_DAYS = 1     # 발매 D-1·당일인데 상세페이지 미등록 컬러가 있으면 위험(사용자 지시 2026-10-23)
PRESHIP_RISK_DAYS = (3, 14)  # 예약 출고일 D-3 ~ 지난 뒤 14일까지 그 컬러가 입고 전이면 위험(사용자 지시 2026-10-29)
DATA_DAYS = 40           # 분배 원천은 최근 45일 — 이보다 오래된 발매의 분배는 '기록 없음'
INB_DATA_DAYS = 160      # 입고 원천(ASN_STYLE_SQL)은 240일 — 발매 −75일 창을 빼면 160일 전 발매까지 온전하다
MIN_ROWS = 200           # 시즌 하나당 실측 700행대 — 이보다 적으면 탭을 반쯤 읽은 것

HEADER = ["season", "plan", "style", "line", "item", "type", "hero", "name", "sku", "plan_qty",
          "inb_plan", "inb_manual", "asn_qty", "recv_qty", "asn_last", "recv_last",
          "online_at", "online_diff", "uids", "dist_qty", "dist_shops", "dist_total", "dist_last",
          "inb_state", "online_state", "dist_state", "risk", "issue", "sourcing", "row_note", "hero_series",
          "recv_first", "preorder", "preorder_src", "preorder_ship", "detail_state", "detail_done", "detail_total", "detail_missing",
          "launch_colors", "inb_missing", "shoot_plan", "shoot_done"]   # 촬샘 예정일·실 입고일(원문) — 캘린더 카드(11/11)

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
    # 촬샘(촬영샘플) 입고 — 예정일 = 묶음 '촬샘입고'·세부 '예정일'. 완료 = 그 **바로 뒤 3칸 안의 첫 '실 입고일'**.
    #   묶음 라벨이 시즌마다 다르다(27SS '촬영샘플' P열 · 26FW '실입고일' Q열, 사이에 '디자인실 요청')라 위치로 가른다.
    #   ★기존 COLS 'inb_manual'(None,'실 입고일' 첫 것)도 실은 이 칸을 읽는다 — 상품 실입고가 아니라 촬샘 실입고다(11/11 확인).
    sp_j = next((j for j in range(len(sub)) if g(j) == "촬샘입고" and sub[j].strip() == "예정일"), None)
    sd_j = next((j for j in range(sp_j + 1, min(sp_j + 4, len(sub))) if sub[j].strip() == "실 입고일"), None) if sp_j is not None else None
    if sp_j is None or sd_j is None:
        print(f"[plan] ⚠ {season} 촬샘 예정일/실 입고일 열 못 찾음(예정 {sp_j} · 실입고 {sd_j}) — 이 시즌 촬샘 알림 제외")
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
                    "sourcing": c("sourcing"), "note": note, "preorder": c("preorder"),
                    "shoot_ok": sd_j is not None,
                    "shoot_plan": work_req.norm_date(r[sp_j].strip() if sp_j is not None and sp_j < len(r) else ""),
                    # 완료 = 실 입고일 칸에 **뭐든** 적혀 있으면(날짜가 아닌 '완료' 같은 글자도 완료로 본다)
                    "shoot_done": (r[sd_j].strip() if sd_j is not None and sd_j < len(r) else "")})
    print(f"[plan] {season} 발매1 {len(out):,}행 (헤더 {hi + 1}행 · 열 {ix} · 촬샘 예정 {sp_j} 실입고 {sd_j})")
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


UID_COLOR: dict[str, str] = {}      # load_uids 가 채운다 — UID → 컬러코드(운영품번 '품번-컬러' 의 뒤)
UID_PLAN: dict[str, _dt.date] = {}  # load_uids 가 채운다 — UID → 아이템마스터 컬러별 발매일정(B열)


def load_uids(sheets) -> dict[str, set]:
    """아이템마스터 → {대표품번: {UID…}} (전 컬러). 부수로 UID_COLOR 를 채운다."""
    v = sheets.spreadsheets().values().get(spreadsheetId=MUTAN_SHEET, range=f"'{MUTAN_TAB}'!B12:E30000").execute().get("values", [])
    out: dict[str, set] = collections.defaultdict(set)
    from soo.hero_ops.imc_triggers import _mutan_date
    for r in v:
        uid = str(r[1]).strip() if len(r) > 1 else ""
        rep = str(r[3]).strip() if len(r) > 3 else ""
        op = str(r[2]).strip() if len(r) > 2 else ""
        if uid.isdigit():
            d = _mutan_date(r[0] if r else "")
            if d:
                UID_PLAN.setdefault(uid, d)
        if uid.isdigit():
            for k in {rep, op[:9]} - {""}:
                out[k].add(uid)
            if "-" in op:
                UID_COLOR.setdefault(uid, op.split("-", 1)[1])
    return out


def build(sheets, drive, today: _dt.date) -> tuple[list[list], collections.Counter, list[dict]]:
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
    for kind, sty_, col_, d_, q_ in arows:
        e = work_req.norm_date(_ymd(d_))
        if e:
            (wms if kind == "wms" else asn)[str(sty_ or "").strip()].append((e, _num(q_), str(col_ or "").strip()))
    # 분배 — 품번-컬러 요약을 품번으로 접는다(받은 매장은 컬러 합집합)
    stores = {str(s["shop_no"]): s for s in _read_tab(sheets, "_OPS_STORES")}
    in_list = {k for k, s in stores.items() if s.get("in_list") == "Y"}
    dist = collections.defaultdict(list)         # 품번 → [(최근 출고일, 출고, 받은 목록 매장들)] (컬러별)
    for d in _read_tab(sheets, "_OPS_DIST"):
        sh = {p[0] for p in (x.split(":") for x in str(d.get("shops", "")).split(","))
              if len(p) >= 2 and _num(p[1]) > 0 and p[0] in in_list}
        dist[str(d.get("style", "")).strip()].append(
            (work_req.norm_date(_ymd(d.get("last_dt"))), _num(d.get("done_qty")), sh, str(d.get("color", "")).strip()))
    log = release_log.fetch(sheets)
    pre_log = release_log.fetch_preorder(sheets)
    no_detail, pending, integrated = release_log.fetch_detail(sheets)   # 상세 빈 UID · 대기 UID · 통합 UID 품번
    uids = load_uids(sheets)
    # 작업의뢰 '스타일명' 이 빈 행은 비제스트 상품명으로 채운다(사용자 지시 2026-10-26). 실패해도 이름만 빈 채로 계속.
    try:
        bz_names = release_log.fetch_names(sheets)
    except Exception as e:
        print(f"[plan] ⚠ 비제스트 상품명 실패 — 빈 이름 그대로 진행: {type(e).__name__}: {e}")
        bz_names = {}
    filled = collections.Counter()
    for p in plans:
        if not p["name"]:
            p["name"] = bz_names.get(p["style"], "")
            filled["비제스트로 채움" if p["name"] else "못 채움"] += 1
    print("[plan] 빈 스타일명 " + (" · ".join(f"{k} {v}" for k, v in sorted(filled.items())) or "0"))
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
        # ★이번 발매 컬러(사용자 지적 2026-10-23 MWFTL8A05 — 10/21 은 새 컬러 WH 발매인데 기존 컬러 9/1 입고가 붙었다).
        #   이미 판매중: 발매일 −7일 이전엔 판매중 전환이 없고 −7~+30일에 전환된 UID. 아직 발매 전: 등록/판매 대기 UID.
        #   못 가르면(같은 UID 재발매 등) 전 컬러. 입고·상세·분배는 이 컬러만 본다.
        launch_u = []
        # ①아이템마스터 컬러별 발매일(B열)이 이 발매일 ±7일인 컬러 — 기획MD팀이 컬러마다 적는 값이라 가장 정확하다
        #   (사용자 지적 2026-10-23 MEFSC0A26: 10/7 행에 11/4 발매 컬러 ET·NN 이 섞여 '상세 미등록'으로 보였다).
        if plan:
            launch_u = [x for x in u if x in UID_PLAN and abs((UID_PLAN[x] - plan).days) <= EARLY_DAYS]
        # ②없으면 비제스트로 추정 — 대기 컬러는 **아직 안 지난 발매**에만 넣는다(지난 발매엔 다음 발매 대기분이 섞인다)
        if plan and not launch_u:
            for x in u:
                tr = log.get(x, [])
                before = any(t.date() < plan - _dt.timedelta(days=EARLY_DAYS) for t in tr)
                near = any(plan - _dt.timedelta(days=EARLY_DAYS) <= t.date() <= plan + _dt.timedelta(days=30) for t in tr)
                # 통합 UID 품번은 컬러별 UID 가 늘 대기 상태라 '대기 = 이번 발매' 로 보면 안 된다
                if (near and not before) or (x in pending and sty not in integrated and plan >= today):
                    launch_u.append(x)
        launch_u = launch_u or u
        lcols = {UID_COLOR.get(x) for x in launch_u}
        lcols = None if (None in lcols or launch_u == u) else lcols      # 컬러를 모르거나 전 컬러면 품번 전체
        # ★예약발매(사용자 지시 2026-10-23) = **비제스트 '출고예정 구분 → 예약 배송' 설정 이력**(release_log.PREORDER_SQL).
        #   그 스타일 UID(컬러) 중 하나라도 발매일 −30~+14일 안에 예약 배송으로 설정됐으면 예약발매, 예약 출고일도 함께.
        #   작업의뢰 '예발여부'는 보조 근거(src 표기만). ★'판매 시작이 입고보다 먼저'로 추정하던 규칙은 버렸다 —
        #   WMS 확정일 지연·컬러추가(기존 컬러 재고)·신발(WMS 밖 입고) 때문에 시스템 설정과 어긋났다.
        mark = "예발" in p["preorder"]
        ships = []
        pre_by_color = {}
        if plan:
            lo_p, hi_p = plan - _dt.timedelta(days=30), plan + _dt.timedelta(days=14)
            ships = sorted({sd for x in u for (ts, sd) in pre_log.get(x, ()) if lo_p <= ts.date() <= hi_p and sd})
            # ★컬러별로 — 스타일 단위로만 보이면 '9/1 입고인데 왜 예약?'(기존 컬러 입고 · 새 컬러만 예약)이 된다(사용자 지적 MWFTL8A05)
            pre_by_color = {}
            for x in launch_u:                                              # 이번 발매 컬러
                hits = [sd for (ts, sd) in pre_log.get(x, ()) if lo_p <= ts.date() <= hi_p and sd]
                if hits:
                    pre_by_color[UID_COLOR.get(x, '통합')] = hits[-1]          # 그 컬러의 마지막 예약 설정 = 현재 예약 출고일
            # ★예약발매 딱지(Y)는 **이번 발매 컬러 전부**가 예약일 때만(사용자 확정 2026-10-23 'A안').
            #   MWFTL8A05 10/21 행 = WH 1컬러 발매(DR·EC·BK 는 8/12 발매)라 WH 예약 → 그 발매는 예약발매가 맞다.
            #   (한때 '스타일 전 컬러' 기준으로 바꿨다가 되돌림.) 통합 UID 품번은 온라인 상품 = 통합 UID 라 그것만 센다.
            base = [x for x in u if x not in UID_COLOR] if sty in integrated else launch_u
            base = base or launch_u
            n_pre = sum(1 for x in base if any(lo_p <= ts.date() <= hi_p for (ts, _) in pre_log.get(x, ())))
            sysp = any(lo_p <= ts.date() <= hi_p for x in u for (ts, _) in pre_log.get(x, ()))
        else:
            sysp, n_pre, base = False, 0, u
        # 일부만 예약이면 'P' — 화면은 딱지 대신 'WH 예약 배송 11/02' 한 줄.
        pre = ("Y" if base and n_pre >= len(base) else "P") if sysp else ""
        pre_src = ("비제스트+작업의뢰" if mark else "비제스트") if sysp else ("작업의뢰만(비제스트 설정 없음)" if mark else "")
        pre_ship = " · ".join(f"{c} {d[4:6]}/{d[6:8]}" for c, d in sorted(pre_by_color.items())) if plan and ships else ""
        old = bool(plan) and plan < today - _dt.timedelta(days=DATA_DAYS)
        old_inb = bool(plan) and plan < today - _dt.timedelta(days=INB_DATA_DAYS)
        lo_a = plan + _dt.timedelta(days=ASN_WINDOW[0]) if plan else None
        # ★작업의뢰 상품입고 예정일이 창보다 앞서면 그 2주 전까지 넓힌다 — 발매 117일 전에 들어온 컬러추가
        #   (MEEFP9A07 BE 6/5 입고 · 9/30 발매)가 '입고 전'으로 보였다(사용자 지적 2026-10-23). 컬러 단위라 섞일 위험은 작다.
        if plan and p["inb_plan"] and p["inb_plan"] - _dt.timedelta(days=14) < lo_a:
            lo_a = p["inb_plan"] - _dt.timedelta(days=14)
        hi_a = plan + _dt.timedelta(days=ASN_WINDOW[1]) if plan else None
        # ★예약발매는 판매를 먼저 켜고 물건이 나중에 온다 — 발매 +30일 창이면 예약 출고일이 늦은 건(10/21 발매·11/24 출고)이
        #   입고가 들어와도 영영 '입고 전'으로 남는다(사용자 지적 2026-10-23). 예약 출고일 +30일까지 늘린다. 딱지는 그대로.
        if plan and ships:
            last_ship = max(_dt.datetime.strptime(x, "%Y%m%d").date() for x in ships)
            hi_a = max(hi_a, last_ship + _dt.timedelta(days=ASN_WINDOW[1]))
        xs = [x for x in asn.get(sty, ()) if plan and lo_a <= x[0] <= hi_a and (lcols is None or x[2] in lcols)]
        ws = [x for x in wms.get(sty, ()) if plan and lo_a <= x[0] <= hi_a and (lcols is None or x[2] in lcols)]
        a = {"q": sum(x[1] for x in xs), "r": sum(x[1] for x in ws),
             "last": max((x[0].isoformat() for x in xs), default=""),
             "rlast": max((x[0].isoformat() for x in ws), default=""),
             "rfirst": min((x[0] for x in ws), default=None),
             "afirst": min((x[0] for x in xs), default=None)}
        inb_missing = ""
        if lcols:
            got = {x[2] for x in ws}
            miss = sorted(c for c in lcols if c not in got)
            inb_missing = " · ".join(miss)
        else:
            miss = []
        if a["r"] > 0 and miss:
            inb = "일부 입고"
        elif a["r"] > 0:
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
        ys = [y for y in dist.get(sty, ()) if plan and y[0] and y[0] >= plan + _dt.timedelta(days=DIST_FROM)
              and (lcols is None or y[3] in lcols)]
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
        # 상세페이지 — 스타일 UID(컬러) 중 비제스트 상세 본문이 등록된 수. UID 가 아예 없으면 상품 자체가 미등록.
        if sty in integrated:              # 온라인 = 통합 UID 하나 — 그 상세 본문이 기준
            d_done, d_total = (1 if integrated[sty] else 0), 1
            d_missing = "" if integrated[sty] else "통합 UID"
        else:
            d_done, d_total = sum(1 for x in launch_u if x not in no_detail), len(launch_u)
            d_missing = " · ".join(sorted(UID_COLOR.get(x, '통합') for x in launch_u if x in no_detail))
        if offline_only:
            dst = "제외"
        elif not u and sty not in integrated:
            dst = "상품 미등록"
        elif d_done >= d_total:
            dst = "완료"
        elif d_done == 0:
            dst = "작업 전"
        else:
            dst = "진행"
        risk = []
        if plan:
            dleft = (plan - today).days
            if 0 <= dleft <= DETAIL_RISK_DAYS and dst in ("작업 전", "진행", "상품 미등록"):
                risk.append(f"상세페이지 미등록({d_missing or '상품 코드 없음'}) D-{dleft}")
            # ★예약발매(이번 발매 컬러 전부 예약)는 물건이 나중에 오는 게 정상 — 입고 위험에서 뺀다(사용자 지시 2026-10-23)
            if 0 <= dleft <= INBOUND_RISK_DAYS and inb == "입고 전" and pre != "Y":
                risk.append(f"입고 흔적 없음 D-{dleft}")
            if dleft < 0 and on in ("미전환",):
                risk.append(f"발매일 지났는데 미전환 D+{-dleft}")
        # ★예약발매는 위 입고 조건에서 빠지니 예약 출고일 기준으로 따로 본다 — 출고 D-3 인데 그 컬러가 아직 안 들어왔으면 위험.
        #   컬러를 못 가르면(통합·lcols 없음) 스타일 입고 상태로. Y·P 모두(사용자 지시 2026-10-29).
        late = []
        for c, d in sorted(pre_by_color.items()):
            sleft = (_dt.datetime.strptime(d, "%Y%m%d").date() - today).days
            if not (-PRESHIP_RISK_DAYS[1] <= sleft <= PRESHIP_RISK_DAYS[0]):
                continue
            notin = (c in miss) if (lcols and c != '통합') else inb not in ("입고 완료",)
            if notin:
                late.append((sleft, c))
        if late:
            sl = min(x[0] for x in late)
            risk.append(f"예약 출고 전 미입고({' · '.join(c for _, c in late)}) 출고 " + (f"D-{sl}" if sl >= 0 else f"D+{-sl}"))
        if pre_src:
            cnt["preorder:" + pre_src] += 1
        for r_ in risk:
            cnt["risk:" + r_.split("(")[0].split(" D")[0]] += 1
        cnt[p["season"]] += 1
        grid.append([p["season"], plan.isoformat() if plan else "", sty, p["line"], p["item"], p["type"], p["hero"],
                     p["name"], p["sku"], p["plan_qty"],
                     p["inb_plan"].isoformat() if p["inb_plan"] else "", p["inb_manual"].isoformat() if p["inb_manual"] else "",
                     int(a["q"]), int(a["r"]), a["last"], a["rlast"],
                     at.strftime("%Y-%m-%d %H:%M") if at else "", str((at.date() - plan).days) if at and plan else "",
                     len(u), int(dd["q"]), n_sh, len(in_list), dd["last"],
                     inb, on, ds, " · ".join(risk), p["issue"][:200], p["sourcing"], p["note"],
                     p2h.get(sty, ""), a["rfirst"].isoformat() if a["rfirst"] else "", pre, pre_src, pre_ship,
                     dst, d_done, d_total, d_missing, " · ".join(sorted(lcols)) if lcols else "", inb_missing,
                     p["shoot_plan"].isoformat() if p.get("shoot_plan") else "", p.get("shoot_done", "")])
    return grid, cnt, plans


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
        lines.append(f":small_orange_diamond: [{r[1]}] {r[2]} {r[3][:30]} — {verb}")
    # ★문구(사용자 지시 2026-10-23) — 건수·'작업의뢰 발매시트 · 감지' 같은 딱딱한 머리말과 날 URL 은 빼고,
    #   한 줄 안내 + 담당자 문의 안내 + 슬랙 링크 글자('발매 캘린더 보러가기')로.
    # 슬랙 이모지(사용자 요청 2026-10-23) — 슬랙 알림 한정. 대시보드 화면은 무신사 톤이라 이모지 금지([[CLAUDE 3-10]]).
    msg = (("[시험] " if test else "") + ":spiral_calendar_pad: 발매일이 변경되었어요!\n" + "\n".join(lines)
           + ("" if len(todo) <= 30 else f"\n… 외 {len(todo) - 30}건")
           + "\n:speech_balloon: 자세한 사유는 담당자에게 문의 부탁드려요!"
           + "\n:point_right: <https://hero-master-app.vercel.app/ops?tab=cal|발매 캘린더 보러가기>")
    return msg


def send_changes(msg: str, label: str = "발매일 변경 알림", targets: list[str] | None = None) -> int:
    """targets(기본 CHG_RECIPIENTS) 전원에게 DM. 성공 수를 돌려준다(토큰 없으면 0 — 로컬 시험).

    ★보내는 봇 = **MUSINSA STANDARD MDP 봇**(사용자 지시 2026-10-23 — 히어로 봇과 분리). 토큰 = Secret SLACK_MDP_BOT_TOKEN
      (로컬 = hero_bot/secrets.yaml `mdp_bot_token`). 히어로 봇 토큰엔 chat:write.customize 가 없어 이름만 바꾸는 건 불가였다.
      히어로 봇 토큰으로 폴백하지 않는다 — 조용히 다른 이름으로 나가느니 안 보내고 원장에 남겨 다음 실행에 다시 보낸다.
    """
    import os
    tok = os.environ.get("SLACK_MDP_BOT_TOKEN", "").strip()
    if not tok:
        print(f"[plan] SLACK_MDP_BOT_TOKEN 없음 — {label} 스킵(원장엔 미발송으로 남는다)")
        return 0
    from soo import persona
    mdp = persona.Persona(**{**persona.RANKING_BOT.__dict__, "slack_username": None, "slack_icon_emoji": None, "slack_icon_url": None})
    ok = 0
    targets = CHG_RECIPIENTS if targets is None else targets
    for target in targets:
        try:
            if persona.send_slack(msg, bot_token=tok, target=target, persona=mdp):
                ok += 1
        except Exception as e:
            print(f"[plan] {label} 실패({target}): {type(e).__name__}: {e}")
    print(f"[plan] {label} {ok}/{len(targets)}명")
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


# ── 촬샘 입고 알림(사용자 지시 2026-11-11) ────────────────────────────────────────────────────
#   ①예정일 변경 — 발매일 변경과 같은 방식(스냅샷 날짜 집합 비교, 빠진 날↔생긴 날 짝만 '변경'). 담당 소싱을 같이 적는다.
#     ('누가 바꿨는지'는 칸 단위 편집자를 API 로 못 읽어 담당 소싱으로 대신 — 사용자 확정)
#   ②미입고 — 예정일이 하루 이상 지났는데 '실 입고일' 칸이 비어 있으면 한 번. 키 = (시즌, 품번, 예정일) 원장 dedup.
#     '어제'만 보지 않고 원장에 없는 지난 건 전부를 본다 — 주말·CI 빠진 날도 다음 실행에 잡힌다([[CLAUDE 1-3]]).
#     ★첫 실행엔 이미 밀린 건을 '기준선'으로만 적고 보내지 않는다(11/11 실측 27SS 21건).
#   ③사전(독촉) — 예정일 D-7 이내(오늘~+7일)인데 실 입고일 빈칸이면 한 번(사용자 지시 2026-11-13 "소싱이 늦어져도 본인도
#     모르거나 노티가 없다 — D-1주에 담당 소싱·담당 기획MD 에게만 '제대로 들어오는 거 맞나요?'"). 11/11 의 'D-3 사전 알림 안 함'을
#     사용자가 이 목적(일정 확인 독촉)으로 바꿨다. 키 = (시즌, 품번, 예정일) — 예정일이 바뀌면 새 날짜로 다시 한 번.
#     수신 = 그 품번의 소싱(PLM sc_nm ∪ 발매1 '소싱담당' — 둘이 다른 품번이 있어 합집합) + 기획MD(PLM md_nm), 사람별 자기 건만.
#     ★SHOOT_REMIND_LIVE=False(배포 전)면 수영님에게만 '→ 받을 사람' 을 붙여 보내고 원장에 'TEST …' 로 적는다.
#       LIVE 로 바꾸면 'TEST' 건 중 아직 예정일 전·미입고인 것은 실담당자에게 다시 나간다(테스트 발송이 원장을 소비하지 않게 [[CLAUDE 1-3]]).
#   알림(①②) = CHG_RECIPIENTS DM · MDP 봇 · KST 08~20시(발매일 변경과 같음). 배포 때 관련 팀 전원으로 넓힐 예정(사용자 11/13).
SHOOT_SNAP_TAB = "_OPS_SHOOT_SNAP"
SHOOT_TAB = "_OPS_SHOOT_CHG"
SHOOT_HEADER = ["detected_at", "season", "style", "name", "kind", "old", "new", "sourcing", "notified", "md"]
SHOOT_GRACE_DAYS = 1      # 예정일 + 1일부터 미입고
SHOOT_REMIND_DAYS = 7     # 예정일 D-7 이내 진입 시 사전 독촉(사용자 지시 2026-11-13)
SHOOT_REMIND_LIVE = False # ★배포 전 = 수영님에게만. True = 담당 소싱·기획MD 각자에게


def _read_opt(sheets, tab: str, ncol: int) -> list[list] | None:
    """탭이 없으면 None(드라이런은 탭을 만들지 않는다)."""
    meta = sheets.spreadsheets().get(spreadsheetId=APP_SHEET_ID, fields="sheets.properties(title)").execute()
    if not any(x["properties"]["title"] == tab for x in meta["sheets"]):
        return None
    return sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{tab}'!A2:{_col(ncol)}").execute().get("values", [])


def _shoot_owners(p: dict, owners: dict[str, dict]) -> tuple[str, str]:
    """(소싱, 기획MD) 이름 문자열 ', ' 구분. 소싱 = PLM sc_nm ∪ 발매1 '소싱담당'(11/13 실측 13건 중 8건이 서로 달랐다)."""
    from soo.hero_ops.asn_notify import _names
    o = owners.get(p["style"], {})
    sc = []
    for n in _names(o.get("sc")) + _names(p.get("sourcing")):
        if n not in sc:
            sc.append(n)
    return ", ".join(sc), ", ".join(_names(o.get("md")))


def shoot_events(plans: list[dict], snap_rows: list[list] | None, ledger: list[list] | None,
                 now: _dt.datetime, owners: dict[str, dict] | None = None) -> tuple[list[list], list[list], dict]:
    """(새 원장 행, 새 스냅샷 행, 통계). 원장 행 notified 칸: '' = 보낼 것 · '기준선' = 첫 실행 밀린 건."""
    today, stamp = now.date(), now.strftime("%Y-%m-%d %H:%M")
    cur: dict[str, dict] = {}
    for p in plans:
        if not p.get("shoot_ok"):
            continue
        e = cur.setdefault(f"{p['season']}|{p['style']}", {"dates": set(), "name": p["name"], "src": "", "done": 0})
        if p["shoot_plan"]:
            e["dates"].add(p["shoot_plan"].isoformat())
        e["src"] = e["src"] or p["sourcing"]
        e["done"] += bool(p["shoot_done"])
    events: list[list] = []
    st = {"styles": len(cur), "done_now": sum(v["done"] for v in cur.values())}
    # ① 예정일 변경
    if snap_rows:
        prev = {r[0]: [d for d in (r[2] if len(r) > 2 else "").split(",") if d] for r in snap_rows if r}
        st["done_prev"] = sum(int(r[3]) for r in snap_rows if len(r) > 3 and str(r[3]).isdigit())
        prev_seasons = {k.split("|", 1)[0] for k in prev}
        chg = []
        for k in sorted(set(cur) & set(prev)):
            season, sty = k.split("|", 1)
            if season not in prev_seasons:
                continue
            a, b = set(prev[k]), cur[k]["dates"]
            for o_, n_ in zip(sorted(a - b), sorted(b - a)):
                chg.append([stamp, season, sty, cur[k]["name"], "변경", o_, n_, cur[k]["src"], ""])
        limit = max(30, int(len(cur) * 0.1))
        if len(chg) > limit:
            print(f"[shoot] ⚠ 촬샘 예정일 변화 {len(chg)}건 > 한도 {limit} — 반쯤 읽은 것으로 보고 변경 기록 건너뜀")
            chg = []
        events += chg
    # ② 미입고 — 완료 칸이 통째로 비면(반쯤 읽기·열 이동) 무더기 오알림이 난다 → 직전 대비 완료 수가 20% 넘게 줄면 건너뜀
    if snap_rows and st.get("done_prev", 0) >= 20 and st["done_now"] < st["done_prev"] * 0.8:
        print(f"[shoot] ⚠ 실 입고일 기입 {st['done_prev']}→{st['done_now']} 급감 — 미입고 판정 건너뜀")
    else:
        seen = {(r[1], r[2], r[5]) for r in (ledger or []) if len(r) > 5 and r[4] == "미입고"}
        first = ledger is None
        miss = {}
        for p in plans:
            sp = p.get("shoot_plan")
            if not p.get("shoot_ok") or not sp or p["shoot_done"] or (today - sp).days < SHOOT_GRACE_DAYS:
                continue
            k = (p["season"], p["style"], sp.isoformat())
            if k not in seen:
                miss.setdefault(k, [stamp, *k[:2], p["name"], "미입고", k[2], "", p["sourcing"], "기준선" if first else ""])
        events += sorted(miss.values(), key=lambda r: (r[5], r[2]))
        # ③ 사전(D-7 이내) — 같은 급감 가드 안에 둔다(완료 칸이 통째로 비면 무더기 독촉이 나간다)
        seen_r = {(r[1], r[2], r[5]) for r in (ledger or []) if len(r) > 5 and r[4] == "사전"}
        pre = {}
        for p in plans:
            sp = p.get("shoot_plan")
            if not p.get("shoot_ok") or not sp or p["shoot_done"] or not 0 <= (sp - today).days <= SHOOT_REMIND_DAYS:
                continue
            k = (p["season"], p["style"], sp.isoformat())
            if k not in seen_r and k not in pre:
                sc, md = _shoot_owners(p, owners or {})
                pre[k] = [stamp, *k[:2], p["name"], "사전", k[2], "", sc, "", md]
        events += sorted(pre.values(), key=lambda r: (r[5], r[2]))
    snap = [[k, v["name"], ",".join(sorted(v["dates"])), v["done"]] for k, v in sorted(cur.items())]
    return events, snap, st


def format_shoot(rows: list[list], kind: str, test: bool = False) -> str:
    md = lambda x: f"{int(x[5:7])}/{int(x[8:10])}" if x else "-"
    src = lambda r: f" (소싱 {r[7]})" if len(r) > 7 and r[7] else ""
    lines = []
    for r in rows[:30]:
        if kind == "사전":
            d = (_dt.date.fromisoformat(r[5]) - _kst_now().date()).days
            who = f" (→ 소싱 {r[7] or '-'} / 기획MD {(r[9] if len(r) > 9 else '') or '-'})" if test else ""
            lines.append(f":small_orange_diamond: [{r[1]}] {r[2]} {r[3][:30]} — 예정 {md(r[5])} (D-{d}){who}")
            continue
        what = f"{md(r[5])} → {md(r[6])}" if kind == "변경" else f"예정 {md(r[5])}"
        lines.append(f":small_orange_diamond: [{r[1]}] {r[2]} {r[3][:30]} — {what}{src(r)}")
    more = "" if len(rows) <= 30 else f"\n… 외 {len(rows) - 30}건"
    if kind == "변경":
        head, tail = ":camera: 촬샘 입고일자가 변경되었어요!", ":speech_balloon: 자세한 사유는 담당 소싱에게 문의 부탁드려요!"
    elif kind == "사전":
        head = ":camera: 촬샘 입고 D-7 예정인 스타일이에요!"
        tail = (":point_right: 일정대로 들어오는 게 맞나요? 변경이 필요하면 작업의뢰 시트 촬샘 예정일 업데이트 및 "
                "관련 분들께 노티 부탁드려요!")
    else:
        head = ":warning: 촬샘 입고 예정일이 지났는데 아직 입고 완료(실 입고일) 표시가 없어요"
        tail = ":point_right: 스케줄대로 들어왔는지 확인 부탁드려요!"
    return ("[시험] " if test else "") + head + "\n" + "\n".join(lines) + more + "\n" + tail


def track_shoot(sheets, plans: list[dict], now: _dt.datetime, apply: bool) -> list[list]:
    snap_rows = _read_opt(sheets, SHOOT_SNAP_TAB, 4)
    ledger = _read_opt(sheets, SHOOT_TAB, len(SHOOT_HEADER))
    from soo.hero_ops import asn_notify
    owners = asn_notify.load_owners(sheets)          # PLM `데이터` + `_OPS_CAT` 원장 보충(ASN 알림과 같은 경로)
    events, snap, st = shoot_events(plans, snap_rows, ledger, now, owners)
    ck = collections.Counter((r[4], r[8] or "보낼 것") for r in events)
    print(f"[shoot] 품번 {st['styles']:,} · 실입고 기입 {st['done_now']}(직전 {st.get('done_prev', '-')}) · "
          + (" · ".join(f"{k[0]}/{k[1]} {v}" for k, v in sorted(ck.items())) or "새 건 없음")
          + ("" if snap_rows else " · 스냅샷 첫 생성(변경 비교는 다음 실행부터)"))
    for r in events[:10]:
        print("   ", r[1], r[2], r[3][:20], r[4], r[5], "→" if r[6] else "", r[6], r[7], r[8])
    if not apply:
        for kind in ("변경", "미입고", "사전"):
            todo = [r for r in events if r[4] == kind and not r[8]]
            if todo:
                print(f"--- 미리보기({kind}) ---\n" + format_shoot(todo, kind, test=True))
        return events
    _ensure(sheets, SHOOT_TAB, SHOOT_HEADER)      # ★건이 없어도 만든다 — 원장 탭 유무가 '첫 실행(기준선)' 판정이다
    _fix_header(sheets)
    if events:
        sheets.spreadsheets().values().append(   # ★OVERWRITE([[CLAUDE 1-6]])
            spreadsheetId=APP_SHEET_ID, range=f"'{SHOOT_TAB}'!A1", valueInputOption="RAW",
            insertDataOption="OVERWRITE", body={"values": events}).execute()
    _ensure(sheets, SHOOT_SNAP_TAB, ["key", "name", "dates", "done"])
    sheets.spreadsheets().values().clear(spreadsheetId=APP_SHEET_ID, range=f"'{SHOOT_SNAP_TAB}'!A1:D", body={}).execute()
    meta = sheets.spreadsheets().get(spreadsheetId=APP_SHEET_ID, fields="sheets.properties(title,sheetId,gridProperties)").execute()
    sp = next(x["properties"] for x in meta["sheets"] if x["properties"]["title"] == SHOOT_SNAP_TAB)
    if sp["gridProperties"]["rowCount"] < len(snap) + 100:
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": sp["sheetId"], "gridProperties": {"rowCount": len(snap) + 500}},
            "fields": "gridProperties.rowCount"}}]}).execute()
    sheets.spreadsheets().values().update(spreadsheetId=APP_SHEET_ID, range=f"'{SHOOT_SNAP_TAB}'!A1", valueInputOption="RAW",
                                          body={"values": [["key", "name", "dates", "done"]] + snap}).execute()
    if 8 <= now.hour < 20:
        open_keys = {(p["season"], p["style"], p["shoot_plan"].isoformat()) for p in plans
                     if p.get("shoot_ok") and p.get("shoot_plan") and not p["shoot_done"]}
        _notify_shoot(sheets, now, open_keys)
    return events


def _fix_header(sheets) -> None:
    """11/11 에 만든 원장은 9열 — 사전 알림용 'md' 열(J)을 머리말에 붙인다(행 구조 불변, 열만 뒤에 [[CLAUDE 1-6]])."""
    meta = sheets.spreadsheets().get(spreadsheetId=APP_SHEET_ID, fields="sheets.properties(title,sheetId,gridProperties)").execute()
    sp = next(x["properties"] for x in meta["sheets"] if x["properties"]["title"] == SHOOT_TAB)
    if sp["gridProperties"]["columnCount"] < len(SHOOT_HEADER):
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": sp["sheetId"], "gridProperties": {"columnCount": len(SHOOT_HEADER)}},
            "fields": "gridProperties.columnCount"}}]}).execute()
    h = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID,
                                           range=f"'{SHOOT_TAB}'!A1:{_col(len(SHOOT_HEADER))}1").execute().get("values", [[]])
    if (h[0] if h else []) != SHOOT_HEADER:
        sheets.spreadsheets().values().update(spreadsheetId=APP_SHEET_ID, range=f"'{SHOOT_TAB}'!A1", valueInputOption="RAW",
                                              body={"values": [SHOOT_HEADER]}).execute()


def _notify_shoot(sheets, now: _dt.datetime, open_keys: set | None = None) -> None:
    _ensure(sheets, SHOOT_TAB, SHOOT_HEADER)
    rows = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID,
                                              range=f"'{SHOOT_TAB}'!A2:{_col(len(SHOOT_HEADER))}").execute().get("values", [])
    col = _col(SHOOT_HEADER.index("notified") + 1)    # I — 'md' 열(J)이 붙어도 notified 는 I
    _notify_remind(sheets, now, rows, col, open_keys or set())
    for kind in ("변경", "미입고"):          # '사전' 은 _notify_remind(사람별)
        todo = [(i, r) for i, r in enumerate(rows) if len(r) >= 6 and r[4] == kind and not (r[8] if len(r) > 8 else "").strip()]
        if not todo:
            continue
        if send_changes(format_shoot([r for _, r in todo], kind), label=f"촬샘 {kind} 알림") == 0:
            print(f"[shoot] 촬샘 {kind} 알림 미발송 {len(todo)}건 — 원장에 '보냄' 표시 안 함")   # 기록은 성공 시에만
            continue
        sent = now.strftime("%Y-%m-%d %H:%M")
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"valueInputOption": "RAW", "data": [
            {"range": f"'{SHOOT_TAB}'!{col}{i + 2}", "values": [[sent]]} for i, _ in todo]}).execute()
        print(f"[shoot] 촬샘 {kind} 알림 {len(todo)}건 발송")


def _notify_remind(sheets, now: _dt.datetime, rows: list[list], col: str, open_keys: set) -> None:
    """사전(D-7) 독촉 발송. 보낼 행 = kind '사전' · 아직 미입고·같은 예정일(open_keys) · notified 빈칸(LIVE 땐 'TEST…'도).
    TEST = CHG_RECIPIENTS 에 전부 한 통('→ 받을 사람' 표기) · LIVE = 소싱·기획MD 이름 → `담당자매핑` Slack ID 로 사람별 자기 건만.
    Slack ID 없는 이름이 낀 건은 CHG_RECIPIENTS 로도 모아 보낸다(조용히 빠지지 않게)."""
    stamp = now.strftime("%Y-%m-%d %H:%M")
    today = now.date()

    def due(r):
        n = (r[8] if len(r) > 8 else "").strip()
        if not (not n or (SHOOT_REMIND_LIVE and n.startswith("TEST"))):
            return False
        try:
            left = (_dt.date.fromisoformat(r[5]) - today).days
        except ValueError:
            return False
        return left >= 0 and (r[1], r[2], r[5]) in open_keys

    todo = [(i, r) for i, r in enumerate(rows) if len(r) >= 6 and r[4] == "사전" and due(r)]
    if not todo:
        return
    sent_rows: set[int] = set()
    if not SHOOT_REMIND_LIVE:
        if send_changes(format_shoot([r for _, r in todo], "사전", test=True), label="촬샘 사전(D-7) 알림[TEST]"):
            sent_rows = {i for i, _ in todo}
        label = "TEST"
    else:
        from soo.hero_ops import asn_notify, triggers as T
        from soo.hero_ops.asn_notify import _names
        T.load_owner_map(sheets, APP_SHEET_ID)
        by: dict[str, list] = collections.defaultdict(list)
        unmapped: list = []
        for i, r in todo:
            names = _names(r[7]) + _names(r[9] if len(r) > 9 else "")
            sids = {asn_notify._sid_of(n) for n in names} - {""}
            for sid in sids:
                by[sid].append((i, r))
            if not names or any(not asn_notify._sid_of(n) for n in names):
                unmapped.append((i, r))
        for sid, items in by.items():
            if send_changes(format_shoot([r for _, r in items], "사전"), label="촬샘 사전(D-7) 알림", targets=[sid]):
                sent_rows |= {i for i, _ in items}
        if unmapped:
            print(f"[shoot] 사전 알림 Slack ID 없는 담당이 낀 {len(unmapped)}건 → 전략팀 DM")
            send_changes(format_shoot([r for _, r in unmapped], "사전", test=True).replace("[시험] ", "[담당자 미매핑] "),
                         label="촬샘 사전 미매핑")
        label = "LIVE"
    if not sent_rows:
        print(f"[shoot] 촬샘 사전 알림 미발송 {len(todo)}건 — 원장에 '보냄' 표시 안 함")
        return
    sheets.spreadsheets().values().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"valueInputOption": "RAW", "data": [
        {"range": f"'{SHOOT_TAB}'!{col}{i + 2}", "values": [[f"{label} {stamp}"]]} for i in sorted(sent_rows)]}).execute()
    print(f"[shoot] 촬샘 사전 알림 {len(sent_rows)}/{len(todo)}건 발송({label})")


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
    grid, cnt, plans = build(svc["sheets"], svc["drive"], now.date())
    print("[plan] " + " · ".join(f"{k} {v:,}" for k, v in sorted(cnt.items())))
    if a.apply:
        # 변경 감지는 화면 기입 **뒤에** — 실패하면 예외로 스텝을 실패시켜 워크플로 실패 알림이 오게 한다(조용히 삼키지 않는다).
        write(svc["sheets"], grid, f"발매 파이프라인 · 발매일=시즌 작업의뢰 '발매1' · 입고=WMS 업체 입고(품번) · 예약발매=비제스트 예약 배송 이력 "
                                   f"· 온라인=판매중 전환 · 분배=매장 출고(부문 대시보드 매장 기준) · 생성 {now:%Y-%m-%d %H:%M} KST")
        track_changes(svc["sheets"], grid, now)
        track_shoot(svc["sheets"], plans, now, apply=True)
    else:
        track_shoot(svc["sheets"], plans, now, apply=False)
        print("(드라이런 — --apply 로 기입)")


if __name__ == "__main__":
    main()
