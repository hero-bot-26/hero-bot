# -*- coding: utf-8 -*-
"""입고-발매 대시보드 · 온라인 발매 — 무탠 전 상품의 계획 발매일 vs 실제 판매중 전환.

왜 있나 (2026-10-22, 사용자 지시)
  ASN 화면을 히어로 앱에서 떼어 '입고-발매 대시보드'로 키우면서 범위를 **무탠다드 전 상품**으로 넓혔다.
  입고(ASN→WMS) 다음 단계인 "온라인에 실제로 걸렸나"를 같은 화면에서 본다.

원천
  발매일 = ★발매 캘린더와 같은 작업의뢰 '발매1'(`_OPS_PLAN`, 사용자 지시 2026-10-29). 작업의뢰에 없는 품번만 아래 아이템마스터 날짜.
  계획 = 무탠본부 아이템마스터 '무탠' 탭(기획MD팀 관리, imc_triggers 와 같은 진실소스)
         B=발매일정 C=UID D=운영품번(신품번) E=대표품번 F=컬러 G=상품명 H=발매시즌 I=팀 J=아이템 M=성별 O=MD
  실제 = release_log.RELEASE_SQL(goods_hist_management 판매중 40 전환, 무탠 전 상품 240일) — DBX 중계 spec 'release'
         (08:20·10:20 KST 적재. 그 뒤에 켜진 상품은 다음 날 아침에 잡힌다 → 당일 계획은 '확인 대기').

판정 (2026-10-22 실측: 최근 120일 계획 지난 1,674건 중 1,556건이 ±7일 안에 전환, 대부분 0·−1일)
  IMC 캘린더(release_log.actual_release)는 ±7일 창만 보지만 여기선 **늦은 발매**를 잡아야 한다 —
  퍼그 부츠 MEFFB9D80 은 계획 9/30 · 실제 10/21(+21일)이라 ±7 창이면 '미전환'으로 보였다.
  → 계획 −7일 이후 첫 전환을 실제 발매로 본다(그 앞은 캐리오버 옛 전환·미리 켜 본 기록).
     · 차이 ≤ +2일          발매완료
     · 차이 > +2일          지연발매(+N일)
     · 창 안 전환 없음, 그 이전 전환만 있음  사전전환(계획보다 N일 먼저 켜진 기록 — 확인 필요)
     · 전환 기록 없음       계획일 지남 = 미전환(D+N) · 오늘 = 확인 대기 · 미래 = 예정
     · UID 없음(#N/A 등)    미등록(상품 코드가 아직 안 만들어짐)

사용법
  python -m soo.hero_ops.launch_board            # 드라이런(기본) — 요약만
  python -m soo.hero_ops.launch_board --apply    # 앱 시트 `_OPS_LAUNCH` 기입
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops import release_log
from soo.hero_ops.imc_triggers import MUTAN_SHEET, MUTAN_TAB, _mutan_date

HERO = Path(__file__).resolve().parents[2]
APP_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"
TAB = "_OPS_LAUNCH"

PAST_DAYS = 120          # 계획일 창 — 지난 것은 120일, 앞으로는 180일
FUTURE_DAYS = 180
EARLY_DAYS = 7           # 계획보다 이만큼 앞선 전환까지는 '그 발매'로 본다(키즈 전날 전환 등)
LATE_OK_DAYS = 2         # 이보다 늦으면 지연발매
SAME_LAUNCH_DAYS = 45    # 작업의뢰 발매일과 이만큼 안이면 같은 발매(옮겨진 일정). 실측 10/29: 옮김은 ±30일 안, 90일+ 는 다른 시즌 재발매
MIN_ROWS = 300           # 실측 ~1,800행 — 이보다 적으면 원천이 반쯤 읽힌 것으로 보고 쓰지 않는다

HEADER = ["plan", "sku", "style", "color", "name", "season", "team", "item", "gender", "md",
          "uid", "state", "actual_at", "diff_days", "note", "im_plan", "plan_src"]
PLAN_TAB = "_OPS_PLAN"   # 발매 캘린더(launch_plan) — 작업의뢰 '발매1' 발매일. 매시 CI 에서 한 시간 전 기입분을 읽는다


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


LAST_COL = _col(len(HEADER))


def _kst_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) + _dt.timedelta(hours=9)


def load_plan(sheets) -> list[dict]:
    """아이템마스터 → 계획 발매 행(운영품번 × 계획일 단위)."""
    vals = sheets.spreadsheets().values().get(
        spreadsheetId=MUTAN_SHEET, range=f"'{MUTAN_TAB}'!B12:O30000").execute().get("values", [])
    out, seen, dup = [], set(), 0
    for r in vals:
        def c(i): return str(r[i]).strip() if i < len(r) and r[i] is not None else ""
        plan = _mutan_date(c(0))
        if not plan:
            continue
        sku = c(2) or c(3)
        key = (sku, plan)
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        uid = c(1)
        out.append({"plan": plan, "sku": sku, "style": c(3) or sku[:9], "color": c(4),
                    "name": c(5), "season": c(6), "team": c(7), "item": c(8), "gender": c(11),
                    "md": c(13), "uid": uid if uid.isdigit() else ""})
    print(f"[plan] 아이템마스터 {len(vals):,}행 → 발매일 있는 행 {len(out):,} (같은 품번·날짜 중복 {dup} 접음)")
    return out


def load_req_plan(sheets) -> dict[str, list[tuple[_dt.date, set | None]]]:
    """`_OPS_PLAN` → 품번 → [(작업의뢰 발매일, 이번 발매 컬러 집합 | None=전 컬러)]."""
    v = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{PLAN_TAB}'!A1:AZ").execute().get("values", [])
    hi = next((i for i, r in enumerate(v[:5]) if "plan" in r and "style" in r), None)
    if hi is None:
        raise RuntimeError(f"[{PLAN_TAB}] 머리줄(plan·style)을 못 찾음 — 발매 캘린더 탭 구조가 바뀌었다")
    h = v[hi]
    ip, ist, ic = h.index("plan"), h.index("style"), h.index("launch_colors") if "launch_colors" in h else None
    out: dict[str, list] = collections.defaultdict(list)
    for r in v[hi + 1:]:
        def c(i): return str(r[i]).strip() if i is not None and i < len(r) else ""
        try:
            d = _dt.date.fromisoformat(c(ip)[:10])
        except ValueError:
            continue
        if d.year < 2020:                       # 27SS '1900-01-03' 같은 값 이상
            continue
        cs = {x.strip() for x in c(ic).split("·") if x.strip()} or None
        out[c(ist)].append((d, cs))
    print(f"[{PLAN_TAB}] 작업의뢰 발매일 {sum(map(len, out.values())):,}행 · {len(out):,}품번")
    return out


def pick_plan(p: dict, req: dict) -> tuple[_dt.date, str]:
    """★발매일은 발매 캘린더(작업의뢰)와 같은 날로 본다(사용자 지시 2026-10-29).
    아이템마스터 B열은 발매 직전·직후에야 채워지고 옮겨진 일정을 안 따라온다(우먼즈 퍼 10/21 → 작업의뢰 10/28).
    같은 품번에 발매 행이 여럿(컬러추가·재발매)이면 그 컬러가 든 발매 → 그중 아이템마스터 날짜에 가장 가까운 것.
    작업의뢰에 없거나 ±SAME_LAUNCH_DAYS 안에 없는(다른 시즌 발매) 품번만 아이템마스터 날짜."""
    cands = req.get(p["style"])
    if not cands:
        return p["plan"], "아이템마스터"
    color = p["sku"][9:11] if len(p["sku"]) >= 11 else p["color"]
    near = [(d, cs) for d, cs in cands if abs((d - p["plan"]).days) <= SAME_LAUNCH_DAYS]
    if not near:       # 작업의뢰엔 다음 시즌 재발매만 있다(26SS 발매 → 27SS 같은 품번) — 그 발매로 끌고 가지 않는다
        return p["plan"], "아이템마스터"
    hit = [d for d, cs in near if cs is None or color in cs] or [d for d, _ in near]
    return min(hit, key=lambda d: (abs((d - p["plan"]).days), d)), "작업의뢰"


def judge(p: dict, log: dict, today: _dt.date) -> tuple[str, str, str, str]:
    """→ (state, actual_at, diff_days, note)"""
    plan = p["plan"]
    if not p["uid"]:
        return ("미등록", "", "", "아이템마스터에 UID 없음")
    ts = log.get(p["uid"], [])
    lo = plan - _dt.timedelta(days=EARLY_DAYS)
    after = [t for t in ts if t.date() >= lo]
    if after:
        a = after[0]
        d = (a.date() - plan).days
        st = "지연발매" if d > LATE_OK_DAYS else "발매완료"
        return (st, a.strftime("%Y-%m-%d %H:%M"), str(d), "")
    if ts:
        a = ts[-1]
        return ("사전전환", a.strftime("%Y-%m-%d %H:%M"), str((a.date() - plan).days),
                "계획보다 앞선 판매중 전환만 있음 — 캐리오버 옛 UID 이거나 미리 켜 둔 것")
    if plan > today:
        return ("예정", "", "", "")
    if plan == today:
        return ("확인대기", "", "", "판매중 전환 로그는 08:20·10:20 적재 — 오늘 전환분은 내일 아침 반영")
    return ("미전환", "", str((today - plan).days), "계획일 지났는데 판매중 전환 기록 없음")


def build(sheets, today: _dt.date) -> tuple[list[list], collections.Counter]:
    log = release_log.fetch(sheets)
    req = load_req_plan(sheets)
    cnt = collections.Counter()
    plans, seen = [], set()
    for p in load_plan(sheets):
        im = p["plan"]
        p["plan"], p["src"] = pick_plan(p, req)
        p["im_plan"] = im
        cnt["src:" + p["src"] + (" (날짜 다름)" if p["plan"] != im else "")] += 1
        if (p["sku"], p["plan"]) in seen:      # 아이템마스터 두 날짜가 같은 작업의뢰 발매로 모인 것
            cnt["src:접음"] += 1
            continue
        seen.add((p["sku"], p["plan"]))
        if today - _dt.timedelta(days=PAST_DAYS) <= p["plan"] <= today + _dt.timedelta(days=FUTURE_DAYS):
            plans.append(p)
    grid = []
    for p in sorted(plans, key=lambda x: (x["plan"], x["sku"]), reverse=True):
        st, at, d, note = judge(p, log, today)
        cnt[st] += 1
        grid.append([p["plan"].isoformat(), p["sku"], p["style"], p["color"], p["name"], p["season"],
                     p["team"], p["item"], p["gender"], p["md"], p["uid"], st, at, d, note,
                     p["im_plan"].isoformat(), p["src"]])
    return grid, cnt


def ensure_tab(sheets, rows: int) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=APP_SHEET_ID,
                                     fields="sheets.properties(title,sheetId,gridProperties)").execute()
    hit = next((s["properties"] for s in meta["sheets"] if s["properties"]["title"] == TAB), None)
    need = rows + 500          # 여유 — 행이 늘어도 다음 실행이 다시 맞춘다
    if hit is None:
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"addSheet": {
            "properties": {"title": TAB, "hidden": True,
                           "gridProperties": {"rowCount": need, "columnCount": len(HEADER), "frozenRowCount": 2}}}}]}).execute()
        print(f"[{TAB}] 탭 생성")
    elif hit["gridProperties"]["rowCount"] < rows + 2:
        # ★하드코딩한 그리드는 넘친다([[CLAUDE 1-2]]) — 쓰기 전에 키운다
        sheets.spreadsheets().batchUpdate(spreadsheetId=APP_SHEET_ID, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": hit["sheetId"], "gridProperties": {"rowCount": need}},
            "fields": "gridProperties.rowCount"}}]}).execute()


def write(sheets, grid: list[list], as_of: str) -> None:
    label = (f"온라인 발매 · 계획=무탠본부 아이템마스터 · 실제=판매중(40) 전환 로그(중계 08:20·10:20) "
             f"· 계획일 −{PAST_DAYS}~+{FUTURE_DAYS}일 · 생성 {as_of} KST")
    ensure_tab(sheets, len(grid) + 2)
    body = [[label] + [""] * (len(HEADER) - 1), HEADER] + grid
    sheets.spreadsheets().values().clear(spreadsheetId=APP_SHEET_ID, range=f"'{TAB}'!A1:{LAST_COL}", body={}).execute()
    sheets.spreadsheets().values().update(spreadsheetId=APP_SHEET_ID, range=f"'{TAB}'!A1",
                                          valueInputOption="RAW", body={"values": body}).execute()
    back = sheets.spreadsheets().values().get(spreadsheetId=APP_SHEET_ID, range=f"'{TAB}'!B3:B").execute().get("values", [])
    if len(back) != len(grid):
        raise RuntimeError(f"[{TAB}] 되읽기 {len(back)}행 ≠ 기입 {len(grid)}행")
    print(f"[{TAB}] 기입 {len(grid):,}행 · 되읽기 일치")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sheets = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))["sheets"]
    now = _kst_now()
    grid, cnt = build(sheets, now.date())
    print("[state] " + " · ".join(f"{k} {v:,}" for k, v in cnt.most_common()))
    if len(grid) < MIN_ROWS:
        raise RuntimeError(f"발매 행 {len(grid)} < {MIN_ROWS} — 아이템마스터를 반쯤 읽은 것으로 보고 쓰지 않는다")
    if a.apply:
        write(sheets, grid, now.strftime("%Y-%m-%d %H:%M"))
    else:
        print("(드라이런 — --apply 로 기입)")


if __name__ == "__main__":
    main()
