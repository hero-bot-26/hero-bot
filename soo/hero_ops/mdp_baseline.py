# -*- coding: utf-8 -*-
"""MDP 기획 관리판 `#.상세일정` → 시즌·트랙별 단계 기준일 (시즌 무관·자동 인식).

배경(2026-08-01, 사용자 지시): 26FW 단계 기준일이 **코드에 박아둔 추출본**이라 MDP가 바뀌어도
반영되지 않았다(27SS는 이미 시트에서 읽고 있었다). 27FW·28SS도 같은 시트에 같은 모양으로 붙으므로
**행 번호를 박지 않고 시트를 읽어 찾는다**:

  · 블록  = A/B열의 `■ 무탠다드 26FW …` 표식으로 시즌 구간을 찾는다(다음 ■ 전까지).
  · 트랙  = 블록 헤더행(Initiative가 있는 행)의 그룹 라벨(선발주/메인 · 봄/여름)과
            그 아래 월 라벨(7월·9월·11월 …)로 컬럼을 잡는다.
  · 단계  = Initiative 문구 키워드로 행을 찾는다(품평회·GO/DROP·수량 결정·컬러 확정 …).
  · 연도  = 시트에 월/일만 있어 **컬럼을 위에서 아래로 읽으며 월이 줄면 +1년**(표가 시간순이라 성립).

시즌이 추가돼도 코드 수정이 필요 없고, 못 읽으면 빈 dict를 돌려줘 호출부가 기존 값을 유지한다.

단독 실행(점검): python -m soo.hero_ops.mdp_baseline 26FW 27SS
"""
from __future__ import annotations

import datetime as dt
import re
import sys

MDP_SHEET_ID = "10guWc_5t06nu9QryPymTIl2oogQfV4qOEO81iXSgenI"   # ★무탠다드_기획 관리판
MDP_TAB = "#.상세일정"
MDP_RANGE = f"'{MDP_TAB}'!A1:P400"

_MARK = re.compile(r"■\s*무탠다드\s*(\d{2}\s*(?:SS|FW))")
_DATE = re.compile(r"^(\d{1,2})\s*[/.-]\s*(\d{1,2})$")

# 앱 14단계 → MDP Initiative 문구(부분일치, 먼저 맞는 것). 시즌 공통.
#   12(판매가 확정)는 MDP에 전용 행이 없어 사후원가(Final Cost Set)와 같은 행을 쓴다(기존 동작 유지).
STAGE_KEYWORDS = {
    3:  ["품평회 진행"],
    4:  ["GO / DROP", "GO/DROP", "상품확정 (GO"],
    5:  ["수량 결정"],
    6:  ["컬러 확정"],
    7:  ["원단 확정"],
    8:  ["풀패키지 PLM upload"],
    9:  ["Initial PO"],
    10: ["QC 완료"],
    11: ["Final Cost Set"],
    12: ["Final Cost Set"],
    13: ["입고 완료 한다"],
}

# 앱 트랙 → (MDP 그룹 라벨 후보, 월 라벨 후보). 월 라벨이 없으면 그룹의 첫 컬럼.
#   FW = 선발주(7·8월·방모/라이트다운) / 메인(9~12월) · SS = 봄(1~3월) / 여름+ACC(4~6월)
TRACK_HINTS = {
    "FW": {"가을": (["메인"], ["9월"]), "겨울": (["메인"], ["11월"]), "선발주": (["선발주"], [])},
    "SS": {"간절기": (["봄"], []), "여름": (["여름"], []), "상시": (["봄"], [])},
}


def _s(row, i):
    return str(row[i]).strip() if i < len(row) and row[i] is not None else ""


def _blocks(rows):
    """[(시즌, 제목, 시작idx, 끝idx)] — 시트 순서대로."""
    marks = []
    for i, r in enumerate(rows):
        for j in range(3):
            m = _MARK.search(_s(r, j))
            if m:
                marks.append((m.group(1).replace(" ", ""), _s(r, j), i))
                break
    out = []
    for k, (season, title, i) in enumerate(marks):
        end = marks[k + 1][2] if k + 1 < len(marks) else len(rows)
        out.append((season, title, i, end))
    return out


def _pick_block(blocks, season):
    """같은 시즌 블록이 여럿이면 MAIN/MDP를 우선(27SS는 MAIN·워크웨어 두 블록)."""
    cands = [b for b in blocks if b[0] == season]
    if not cands:
        return None
    for key in ("MAIN", "MDP"):
        for b in cands:
            if key in b[1].upper():
                return b
    return cands[0]


def _header(rows, start, end):
    """(헤더 idx, {col: 그룹라벨}, {col: 월라벨}) — Initiative가 있는 행이 헤더."""
    hi = None
    for i in range(start, min(end, start + 20)):
        if any("Initiative" in _s(rows[i], j) for j in range(6)):
            hi = i
            break
    if hi is None:
        return None, {}, {}
    hdr = rows[hi]
    # 그룹 라벨 = 헤더행에서 KR/Initiative/OWNER/END/캐리오버/비고 를 뺀 나머지
    skip = ("KR", "Initiative", "OWNER", "END", "캐리오버", "비고")
    groups = {}
    cur = None
    for j in range(len(hdr)):
        lab = _s(hdr, j)
        if lab and any(k in lab for k in skip):
            cur = None
            continue
        if lab:
            cur = lab
        if cur:
            groups[j] = cur
    # 월 라벨 = 헤더 아래 3행 안에서 '1월'꼴이 2개 이상 있는 행
    subs = {}
    for i in range(hi + 1, min(hi + 5, end)):
        cells = {j: _s(rows[i], j) for j in range(len(rows[i]))}
        months = {j: v for j, v in cells.items() if re.match(r"^\d{1,2}월$", v)}
        if len(months) >= 2:
            subs = {j: v for j, v in cells.items() if v}
            break
    return hi, groups, subs


def _stage_rows(rows, start, end):
    """{stage: row idx} — Initiative(보통 C열) 문구 부분일치."""
    out = {}
    for i in range(start, end):
        init = ""
        for j in range(1, 4):                     # B~D 중 Initiative 열 후보
            v = _s(rows[i], j)
            if len(v) > 4 and not v.startswith("■"):
                init = v if len(v) > len(init) else init
        if not init:
            continue
        for stage, keys in STAGE_KEYWORDS.items():
            if stage in out:
                continue
            if any(k.lower().replace(" ", "") in init.lower().replace(" ", "") for k in keys):
                out[stage] = i
    return out


def _col_years(rows, start, end, col, base_year, cut):
    """컬럼의 {row idx: date}. 시트엔 월/일만 있어 **시즌 시작월(cut)** 로 연도를 가른다.

    FW = 10월에 기획을 열어 다음 해 9월 입고까지(cut=10) · SS = 3월에 열어 다음 해 2월 입고까지(cut=3).
    ★행 순서로 추론(월이 줄면 +1년)하면 안 된다 — 26FW 선발주 열은 GO/DROP(12/26)이 품평(1/14)보다
    아래에 있어 표가 시간순이 아니다(트랙마다 순서가 다름).
    """
    out = {}
    for i in range(start, end):
        m = _DATE.match(_s(rows[i], col))
        if not m:
            continue
        mo, da = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= da <= 31):
            continue
        try:
            out[i] = dt.date(base_year if mo >= cut else base_year + 1, mo, da)
        except ValueError:
            continue
    return out


def load_mdp_baseline(sheets, season: str, sheet_id: str | None = None,
                      warns: list[str] | None = None) -> dict[str, dict[int, dt.date]]:
    """{트랙: {단계: date}}. 못 읽으면 {} (호출부는 기존/하드코딩 값 유지)."""
    warns = warns if warns is not None else []
    season = season.upper().replace(" ", "")
    try:
        rows = sheets.spreadsheets().values().get(
            spreadsheetId=(sheet_id or MDP_SHEET_ID), range=MDP_RANGE,
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    except Exception as e:
        warns.append(f"MDP 시트 읽기 실패({type(e).__name__}) — 단계 기준일 기존값 유지")
        return {}
    blk = _pick_block(_blocks(rows), season)
    if not blk:
        warns.append(f"MDP에 '{season}' 블록이 없습니다 — 단계 기준일 기존값 유지")
        return {}
    _, title, start, end = blk
    hi, groups, subs = _header(rows, start, end)
    if hi is None:
        warns.append(f"MDP {season}: 헤더행(Initiative)을 못 찾음 — 기준일 없이 진행")
        return {}
    srows = _stage_rows(rows, hi, end)
    if not srows:
        warns.append(f"MDP {season}: 단계 행을 하나도 못 찾음 — 기준일 없이 진행")
        return {}
    kind = "FW" if season.endswith("FW") else "SS"
    base_year = 2000 + int(season[:2]) - 1        # 26FW→2025 · 27SS→2026 (기획 시작 연도)
    cut = 10 if kind == "FW" else 3               # 시즌이 열리는 달 = 연도 경계
    colcache: dict[int, dict[int, dt.date]] = {}

    def col_dates(c):
        if c not in colcache:
            colcache[c] = _col_years(rows, hi, end, c, base_year, cut)
        return colcache[c]

    out: dict[str, dict[int, dt.date]] = {}
    for track, (gkeys, mkeys) in TRACK_HINTS.get(kind, {}).items():
        cols = [j for j, g in sorted(groups.items()) if any(k in g for k in gkeys)]
        if not cols:
            warns.append(f"MDP {season}: 트랙 '{track}' 그룹({'/'.join(gkeys)}) 컬럼 없음")
            continue
        want = [j for j in cols if any(subs.get(j, "") == k for k in mkeys)] if mkeys else []
        order = want + [j for j in cols if j not in want]     # 지정 월 우선, 없으면 그룹 내 좌→우 폴백
        t = {}
        allcols = sorted(groups)                              # 트랙 그룹에 속한 전 컬럼(캐리오버 제외)
        for stage, ri in srows.items():
            for c in order:
                d = col_dates(c).get(ri)
                if d:
                    t[stage] = d
                    break
            else:
                # 그 행이 트랙 컬럼엔 비어 있고 다른 트랙에만 있는 경우(예: 26FW 품평회는 선발주 열에만)
                #   → 그 행의 **가장 늦은 날짜**를 쓴다. 이른 날짜를 쓰면 없던 '지연'이 생긴다.
                cand = [d for c in allcols for d in [col_dates(c).get(ri)] if d]
                if cand:
                    t[stage] = max(cand)
        if t:
            out[track] = t
    if not out:
        warns.append(f"MDP {season}: 트랙별 기준일을 하나도 못 만듦 — 기준일 없이 진행")
    return out


def _main(argv):
    from pathlib import Path
    from soo.auth import build_services, get_credentials
    root = Path(__file__).resolve().parents[2]
    sheets = build_services(get_credentials(root / "credentials.json", root / "token.json"))["sheets"]
    from soo.hero_ops.mdp_baseline import STAGE_KEYWORDS as SK
    labels = {3: "품평회", 4: "GO-DROP", 5: "1차수량", 6: "컬러확정", 7: "원단확정",
              8: "PO전송", 9: "PO작성", 10: "QC APP", 11: "사후원가", 12: "판매가", 13: "입고"}
    for season in (argv or ["26FW", "27SS"]):
        warns = []
        bl = load_mdp_baseline(sheets, season, warns=warns)
        print(f"\n=== {season} ===")
        for track, st in bl.items():
            print(f"  [{track}] " + " · ".join(f"{labels.get(k, k)} {v}" for k, v in sorted(st.items())))
        for w in warns:
            print("  ⚠", w)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
