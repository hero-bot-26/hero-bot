# -*- coding: utf-8 -*-
"""27SS 작업의뢰(★ MS_27SS_작업의뢰 '기획시트') → 보드 단계별 진행/타겟일.

26FW 보드가 `D+NN` 지연을 띄우는 원리는, 각 STY의 단계 문자열에 `기준 YYYY-MM-DD`가 박혀 있고
앱 파서(`lateDoneDays`/`overdueDays`)가 그걸 읽기 때문. 27SS도 원천에 동일한 스케줄/목표일 컬럼이
있으므로, 여기서 STY별로 읽어 같은 형식의 stages/dates(14칸)를 만들어 앱에 주입한다.
스펙 = hero-master-app/docs/27ss-schedule-targets.md

안전 규칙(오탐=슬랙 오발 방지):
  1. 값이 없거나 파싱 실패 → 그 단계는 '베이스라인 대기'(타겟 없음). 절대 임의 날짜를 만들지 않는다.
  2. 원천에 쓰레기 값이 섞여 있다(`99/10/16`, `1899-11-15` 등) → 시즌 창(WINDOW) 밖이면 버린다.
  3. 연도 오타(27SS 입고를 `27/12/22`로 적어 2027-12-22가 되는 케이스)는 상한(발매목표/입고목표)을
     넘을 때만 -1년을 시도하고, 그래도 안 맞으면 버린다. 보정분은 warnings 로 남겨 CI 로그에 노출.
  4. `원단발주 목표`는 다수 행에서 `MD입고 목표일`을 그대로 복사한 placeholder라, 두 값이 같으면
     타겟으로 쓰지 않는다(원단확정 기준일이 입고일이 되어버리는 오해 방지). 다르면 진짜 타겟으로 사용.
"""
from __future__ import annotations

import datetime as dt
import re

# ★ MS_27SS_작업의뢰 (최신본). 소스 레지스트리 키 `plm_27ss_req`로 교체 가능.
DEFAULT_SHEET_ID = "1NshiEIK3o8Kczi5Zg746cz2I3-Q-lq0eGNOe0YTuibI"
TAB = "기획시트"
HEADER_ROW = 5          # 라벨 행(A5:..). 데이터는 6행부터.
RANGE = f"'{TAB}'!A{HEADER_ROW}:FZ2000"

# 27SS 일정이 놓일 수 있는 창. 밖이면 원천 오류로 보고 버린다.
WINDOW = (dt.date(2026, 1, 1), dt.date(2027, 12, 31))

NOT_DONE = {"미완료", "누락", "-", "N/A", "#N/A", "미정"}

# 히어로 후보 판정 — L열 `히어로 핵심 상품`. '핵심상품'도 같은 열에 오지만 대상 아님.
HERO_COL = "히어로 핵심 상품"
HERO_MARK = "히어로"

# 앱 14단계 → (진행 컬럼, 타겟 컬럼). 타겟 없는 단계는 None.
#   9 'PO 작성'(PLM 'PO발송')은 작업의뢰에 대응 컬럼이 없어 미매핑(정직하게 비움).
#   11 사후원가·12 판매가는 원천이 금액 컬럼뿐이라 날짜 없음.
STAGE_COLS: dict[int, tuple[str, str | None]] = {
    6:  ("컬러 확정",         None),
    7:  ("원단 확정",         "원단발주 목표"),
    8:  ("PO 발행 (작지투입)", None),
    # 9(PO 작성=PLM Initial PO)도 작업의뢰의 같은 PO 컬럼으로 닫힌다 — 원천에 PO 컬럼이 하나뿐이라
    # 8/9를 가르는 실적 컬럼이 없다. 타겟만 MDP에서 각각(작지 R137 / Initial PO R138) 다르게 잡는다.
    # 이렇게 두면 PO가 실제로 나갔는데 9가 '지연'으로 뜨는 오탐이 안 생긴다.
    9:  ("PO 발행 (작지투입)", None),
    10: ("테크팩 확정 (APP)",  "APP 목표"),
    13: ("입고 완료",         "MD입고 목표일"),
}

# 상한 검증용(타겟이 이보다 늦으면 연도 오타 의심) — 단계: 상한 컬럼
TARGET_CEILING = {10: "MD입고 목표일", 13: "MD 발매 목표일"}

# ── 트랙별 베이스라인(MDP 기획 관리판 '#.상세일정') ────────────────────────────
# 작업의뢰엔 STY별 목표일이 10(APP)·13(입고)뿐이라, 앞단(수량·컬러·원단·PO)은 MDP 트랙 일정이 기준.
# ★열: G=봄, J=여름 (R122~R138 구간에 한함). R139 이후는 축이 '입고 월코드'로 바뀌므로 절대 쓰지 말 것
#   (그 구간의 J는 여름이 아니라 4월이다).
MDP_SHEET_ID = "10guWc_5t06nu9QryPymTIl2oogQfV4qOEO81iXSgenI"
MDP_TAB = "#.상세일정"
MDP_YEAR = 2026
MDP_COLS = {"봄": "G", "여름": "J"}
# ★MDP에서 가져올 단계 목록(행 번호는 이제 공용 로더가 문구로 찾는다 — 값은 설명용으로만 남김)
MDP_STAGE_ROWS = {
    5:  (133, "수량 결정 & 예판가 & 발매일 확정"),
    6:  (131, "컬러 확정 및 BT 투입"),
    7:  (130, "원단 확정"),
    8:  (137, "풀패키지 PLM upload(작업지시)"),
    9:  (138, "PLM 통한 Initial PO 발행"),
}
# 판매시즌 → MDP 트랙. '상시'는 MDP에 전용 열이 없어 봄(간절기) 일정을 준용한다(비수기·캐리오버 성격).
TRACK_TO_MDP = {"간절기": "봄", "여름": "여름", "상시": "봄"}

KEY_COL, TRACK_COL, STRAT_COL = "신품번", "판매 시즌", "히어로 핵심 상품"
TRACKS = {"간절기", "여름", "상시"}


def _norm_date(raw: str) -> dt.date | None:
    """'26-06-18' / '27/1/6' / '2027-01-13' → date. 그 외(미완료·빈칸·쓰레기)는 None."""
    s = (raw or "").strip()
    if not s or s in NOT_DONE:
        return None
    m = re.match(r"^(\d{2}|\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if y < 100:
        y += 2000
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def _in_window(d: dt.date | None) -> bool:
    return bool(d) and WINDOW[0] <= d <= WINDOW[1]


def _fmt(d: dt.date) -> str:
    return d.isoformat()


def _resolve_target(raw: str, ceiling: dt.date | None,
                    label: str, sty: str, warns: list[str]) -> dt.date | None:
    """타겟 파싱 + 창/상한 검증. 연도 오타는 -1년 1회만 시도(로그 남김). 실패 시 None."""
    d = _norm_date(raw)
    if d is None:
        return None
    if _in_window(d) and (ceiling is None or d <= ceiling):
        return d
    try:
        back = d.replace(year=d.year - 1)
    except ValueError:                                   # 2/29 등
        back = None
    if back and _in_window(back) and (ceiling is None or back <= ceiling):
        warns.append(f"{sty} {label}: '{raw.strip()}' → {_fmt(back)} 로 보정(연도 오타 추정)")
        return back
    warns.append(f"{sty} {label}: '{raw.strip()}' 폐기(범위 밖 — 타겟 없이 진행)")
    return None


def load_mdp_baseline(sheets, sheet_id: str | None = None,
                      warns: list[str] | None = None, season: str = "27SS") -> dict[str, dict[int, dt.date]]:
    """MDP '#.상세일정' → {트랙('봄'/'여름'): {stage: date}}. 실패하면 {} (호출부가 타겟 없이 진행).

    ★2026-08-01: 행/열 하드코딩을 걷어내고 **공용 로더**(`soo.hero_ops.mdp_baseline`)로 넘긴다 —
    시트에서 시즌 블록·트랙 컬럼·단계 행을 문구로 찾으므로 27FW·28SS도 코드 수정 없이 붙는다.
    공용 로더는 SS 트랙을 간절기/여름/상시로 돌려주므로 여기서 봄/여름 이름으로 바꿔 준다
    (아래 TRACK_TO_MDP 매핑을 그대로 유지하기 위함).
    """
    warns = warns if warns is not None else []
    from soo.hero_ops.mdp_baseline import load_mdp_baseline as _generic
    bl = _generic(sheets, season, sheet_id=(sheet_id or MDP_SHEET_ID), warns=warns)
    out = {}
    for app_track, mdp_track in (("간절기", "봄"), ("여름", "여름")):
        if bl.get(app_track):
            out[mdp_track] = {n: d for n, d in bl[app_track].items() if n in MDP_STAGE_ROWS}
    if not out:
        warns.append("MDP 트랙 베이스라인 비어 있음 — 앞단 단계 타겟 없이 진행")
    return out


def build_sty_dates(row: dict, today: dt.date, sty: str,
                    warns: list[str], baseline: dict[int, dt.date] | None = None) -> tuple[list[str], list[str]]:
    """작업의뢰 1행(STY) → (stages[14], dates[14]).

    0~4(MDP~GO-DROP)와 5(1차수량)는 원천에 없어 앱 기본값을 유지하도록 ''를 돌려준다.
    (호출부/앱이 ''는 '건드리지 않음'으로 취급.)
    """
    stages = [""] * 14
    dates = [""] * 14

    # 타겟 상한(입고목표 → 발매목표 순으로 먼저 확정해야 APP목표 검증에 쓸 수 있음)
    rel_goal = _resolve_target(row.get("MD 발매 목표일", ""), None, "MD 발매 목표일", sty, warns)
    in_goal = _resolve_target(row.get("MD입고 목표일", ""), rel_goal, "MD입고 목표일", sty, warns)
    ceilings = {10: in_goal, 13: rel_goal}

    baseline = baseline or {}
    for n in sorted(set(STAGE_COLS) | set(baseline)):
        done_col, target_col = STAGE_COLS.get(n, (None, None))
        actual = _norm_date(row.get(done_col, "")) if done_col else None
        if target_col == "MD입고 목표일":
            target = in_goal
        elif target_col:
            raw_t = row.get(target_col, "")
            # 규칙 4: 원단발주 목표가 MD입고 목표일과 같으면 placeholder → 타겟 미사용
            if target_col == "원단발주 목표" and _norm_date(raw_t) == _norm_date(row.get("MD입고 목표일", "")):
                target = None
            else:
                target = _resolve_target(raw_t, ceilings.get(n), target_col, sty, warns)
        else:
            target = None
        # 작업의뢰에 목표 컬럼이 없는 앞단(수량·컬러·원단·PO)은 MDP 트랙 베이스라인이 기준.
        if target is None and n in baseline:
            target = baseline[n]

        if actual and _in_window(actual):
            if target:
                diff = (actual - target).days
                dates[n] = f"{_fmt(actual)} (기준 {_fmt(target)}, {'+' if diff >= 0 else ''}{diff}일)"
            else:
                dates[n] = _fmt(actual)
            stages[n] = "done"
        elif target:
            if target < today:
                stages[n] = "delayed"
                dates[n] = f"지연! 기준 {_fmt(target)} 경과"
            else:
                stages[n] = "pending"
                dates[n] = f"기준 {_fmt(target)}"
        # actual·target 모두 없으면 '' (앱 기본값 '베이스라인 대기' 유지)

    return stages, dates


def load_27ss_heroes(sheets, sheet_id: str | None = None) -> dict:
    """작업의뢰 기획시트 → 27SS 히어로 후보 {신품번: {heroName, category, track, colors, ownerMD, ownerDesigner}}.

    ★대상 = `히어로 핵심 상품` 열이 정확히 '히어로' 인 행(‘핵심상품’은 제외 — 사용자 확정 2026-08-26).
      앱 `PLM_DATA`(27SS STY 입력 화면의 후보 목록)를 이걸로 만든다. 전엔 앱에 28개가 손으로
      박혀 있어 MD가 기획시트에서 히어로를 늘려도 앱엔 영영 안 붙었다.
    ★헤더는 **첫 매치**로 인덱싱한다 — '아이템'이 두 번 나오는데(H=Coat/Outer… , V=한 글자 코드)
      마지막 매치를 쓰면 category 가 'C' 같은 코드로 깨진다.
    ★컬러는 SKU 행들의 '컬러 국문'을 순서대로 유니크 수집. 아직 SKU 전개 전인 STY 는 빈 리스트가
      정상이다(원천에 없는 걸 만들지 않는다).
    """
    vals = sheets.spreadsheets().values().get(
        spreadsheetId=(sheet_id or DEFAULT_SHEET_ID), range=RANGE).execute().get("values", [])
    if not vals:
        raise ValueError("작업의뢰 기획시트가 비어 있음")
    hdr = [c.replace(chr(10), " ").strip() for c in vals[0]]
    H: dict[str, int] = {}
    for i, h in enumerate(hdr):
        if h and h not in H:
            H[h] = i
    need = [KEY_COL, HERO_COL, "아이템", TRACK_COL, "상품명", "컬러 국문", "MD", "DS"]
    missing = [c for c in need if c not in H]
    if missing:
        raise ValueError(f"작업의뢰 헤더 불일치 — 없는 컬럼: {missing}")

    out: dict[str, dict] = {}
    for r in vals[1:]:
        def get(c):
            i = H.get(c)
            return str(r[i]).strip() if i is not None and len(r) > i else ""
        if get(HERO_COL) != HERO_MARK:
            continue
        sty = get(KEY_COL)
        if not sty:
            continue
        e = out.setdefault(sty, {"heroName": "", "category": "", "track": "",
                                 "colors": [], "ownerMD": "", "ownerDesigner": ""})
        for field, colname in (("heroName", "상품명"), ("category", "아이템"),
                               ("track", TRACK_COL), ("ownerMD", "MD"), ("ownerDesigner", "DS")):
            if not e[field]:
                e[field] = get(colname)
        c = get("컬러 국문")
        if c and c not in e["colors"]:
            e["colors"].append(c)
    return out


def load_27ss_sched(sheets, sheet_id: str | None = None,
                    today: dt.date | None = None,
                    only: set[str] | None = None) -> tuple[dict, list[str]]:
    """작업의뢰 시트 → {신품번: {'stages': [...], 'dates': [...], 'track': '간절기'}}, warnings.

    only 를 주면 그 신품번만(앱 PLM_DATA 후보와 교집합). 실패는 호출부가 가드 —
    여기선 예외를 그대로 올린다(조용한 0 덮어쓰기 방지: 호출부에서 '기존값 유지').
    """
    today = today or dt.date.today()
    vals = sheets.spreadsheets().values().get(
        spreadsheetId=(sheet_id or DEFAULT_SHEET_ID), range=RANGE).execute().get("values", [])
    if not vals:
        raise ValueError("작업의뢰 기획시트가 비어 있음")

    hdr = [c.replace("\n", " ").strip() for c in vals[0]]
    H = {h: i for i, h in enumerate(hdr) if h}
    need = [KEY_COL, TRACK_COL] + [c for c, _ in STAGE_COLS.values()]
    missing = [c for c in need if c not in H]
    if missing:
        raise ValueError(f"작업의뢰 헤더 불일치 — 없는 컬럼: {missing}")

    warns: list[str] = []
    base_by_track = load_mdp_baseline(sheets, warns=warns)
    out: dict[str, dict] = {}
    for r in vals[1:]:
        get = lambda c: (r[H[c]].strip() if c in H and len(r) > H[c] else "")   # noqa: E731
        sty = get(KEY_COL)
        if not sty or (only and sty not in only) or sty in out:
            continue                                     # SKU 여러 행 → STY 첫 행만(스케줄은 STY 단위)
        track = get(TRACK_COL)
        if track not in TRACKS:
            warns.append(f"{sty}: 판매시즌 '{track}' 미인식 — 스킵")
            continue
        row = {c: get(c) for c in H}
        _bl = base_by_track.get(TRACK_TO_MDP.get(track, ""), {})
        stages, dates = build_sty_dates(row, today, sty, warns, baseline=_bl)
        if not any(stages):
            continue                                     # 쓸 값이 하나도 없으면 주입 안 함
        out[sty] = {"stages": stages, "dates": dates, "track": track,
                    "strat": get(STRAT_COL)}
    return out, warns
