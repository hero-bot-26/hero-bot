"""MDP → 시트 ① stage{1..4}_baseline 자동 적재 (IA v0.2 §1 흡수 흐름).

실행:
  python -m soo.hero_ops.baseline_ingest                 # dry-run (변경 미리보기)
  python -m soo.hero_ops.baseline_ingest --apply         # 실제 적재
  python -m soo.hero_ops.baseline_ingest --season 27SS   # 시즌 지정 (기본 27SS)

매핑 규칙:
- 시즌별 MDP 행/컬럼 위치는 SEASON_MDP_MAP에 명시 (시즌 추가 시 여기만 갱신)
- 27SS MAIN MDP는 #.상세일정 R110부터. 헤더 R118, 봄=G, 여름=J
- 단계 1~4만 자동 적재. 단계 5(월코드별 가변)·6(마케팅 트랙)은 NULL 유지
- 시트 ①의 track 컬럼(봄/여름/공통)에 따라 분기. track 비어있으면 skip
"""

from __future__ import annotations

import argparse
import sys
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import yaml

from soo.auth import build_services, get_credentials
from soo.hero_ops.schema import SHEET1_COLUMNS


ROOT = Path(__file__).resolve().parents[2]


# ──────────────────────────────────────────────────────
# 시즌별 MDP 위치 매핑 (시즌 추가 시 여기만 갱신)
# ──────────────────────────────────────────────────────

@dataclass
class StageRow:
    row: int          # MDP 시트의 1-indexed 행 번호
    label: str
    cols: dict[str, str]  # 트랙명 → A1 컬럼 (예: {"봄": "G", "여름": "J"} or {"가을": "G", "겨울": "K"})


@dataclass
class GlobalOrderfairRow:
    row: int
    cols: dict[str, str]


@dataclass
class SeasonMDP:
    spreadsheet_id: str
    tab: str
    year: int           # 시즌 시작 연도. FW는 wraps_year=True로 1~9월은 year+1
    tracks: tuple[str, str]  # 두 트랙명 (예: ("봄","여름") or ("가을","겨울"))
    stages: dict[int, StageRow]
    global_orderfair: GlobalOrderfairRow | None = None
    wraps_year: bool = False  # True면 1~9월은 year+1로 해석 (FW 시즌)


SEASON_MDP_MAP: dict[str, SeasonMDP] = {
    "27SS": SeasonMDP(
        spreadsheet_id="10guWc_5t06nu9QryPymTIl2oogQfV4qOEO81iXSgenI",
        tab="#.상세일정",
        year=2026,
        tracks=("봄", "여름"),
        stages={
            1: StageRow(122, "킥오프 미팅 (방향성 합의)", {"봄": "G", "여름": "G"}),
            2: StageRow(125, "상품확정 준비 (PLM 스타일코드 생성)", {"봄": "G", "여름": "J"}),
            3: StageRow(129, "상품확정 GO/DROP", {"봄": "G", "여름": "J"}),
            4: StageRow(138, "Initial PO 발행", {"봄": "G", "여름": "J"}),
        },
        global_orderfair=GlobalOrderfairRow(row=134, cols={"봄": "G", "여름": "J"}),
    ),
    "26FW": SeasonMDP(
        spreadsheet_id="10guWc_5t06nu9QryPymTIl2oogQfV4qOEO81iXSgenI",
        tab="#.상세일정",
        year=2025,           # 시즌 시작 연도. 1~9월 셀은 wraps_year로 2026 처리
        tracks=("가을", "겨울"),
        wraps_year=True,
        stages={
            1: StageRow(89, "킥오프 미팅", {"가을": "G", "겨울": "G"}),  # 동일 일자
            2: StageRow(91, "매트릭스 합의 완료", {"가을": "G", "겨울": "K"}),
            3: StageRow(95, "GO/DROP/컬러/원단/보정 완료", {"가을": "G", "겨울": "K"}),
            4: StageRow(96, "Initial PO 발행 완료", {"가을": "G", "겨울": "K"}),
        },
        # 26FW 글로벌 수주회 일자는 MDP에서 미식별 — 추후 확인 후 추가
        global_orderfair=None,
    ),
}


# ──────────────────────────────────────────────────────
# 일자 파싱: "3/31" / "7/3" → date(year, 3, 31)
# ──────────────────────────────────────────────────────

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})")


def parse_mdp_date(cell: str, year: int, wraps_year: bool = False) -> date | None:
    """MDP 셀의 '3/31' 형식을 date로. 빈 셀이나 다른 형식은 None.

    wraps_year=True (FW 시즌): 시즌이 두 해에 걸침 → month >= 10이면 year 그대로,
    1~9월이면 year+1 (예: 26FW year=2025 → 10/2=2025-10-02, 1/23=2026-01-23).
    """
    if not cell:
        return None
    m = _DATE_RE.search(str(cell).strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    resolved_year = year + 1 if (wraps_year and month <= 9) else year
    try:
        return date(resolved_year, month, day)
    except ValueError:
        return None


# ──────────────────────────────────────────────────────
# 컬럼 letter 유틸
# ──────────────────────────────────────────────────────

def col_letter(idx_0: int) -> str:
    result = ""
    n = idx_0
    while True:
        result = chr(ord("A") + (n % 26)) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


def col_index(letter: str) -> int:
    """A→0, B→1, ..., Z→25, AA→26"""
    idx = 0
    for ch in letter.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


# ──────────────────────────────────────────────────────
# MDP 읽기 → 단계별 (봄, 여름) baseline 추출
# ──────────────────────────────────────────────────────

def read_mdp_baselines(sheets, season: str) -> tuple[dict[int, dict[str, date | None]], dict[str, date | None]]:
    """반환:
      - stages: {stage_n: {track_name: date}}  (트랙명은 sm.tracks의 두 값)
      - global_orderfair: {track_name: date} (없으면 빈 dict)
    """
    sm = SEASON_MDP_MAP[season]
    # 한 번에 필요한 셀들만 batchGet
    ranges = []
    for stage_n, sr in sm.stages.items():
        for col in set(sr.cols.values()):
            ranges.append(f"'{sm.tab}'!{col}{sr.row}")
    if sm.global_orderfair:
        for col in set(sm.global_orderfair.cols.values()):
            ranges.append(f"'{sm.tab}'!{col}{sm.global_orderfair.row}")
    resp = sheets.spreadsheets().values().batchGet(
        spreadsheetId=sm.spreadsheet_id,
        ranges=ranges,
    ).execute()
    cell_map: dict[str, str] = {}
    for vr in resp.get("valueRanges", []):
        rng = vr["range"]
        vals = vr.get("values", [])
        cell_value = vals[0][0] if vals and vals[0] else ""
        a1 = rng.split("!")[-1]
        cell_map[a1] = cell_value

    stages_out: dict[int, dict[str, date | None]] = {}
    for stage_n, sr in sm.stages.items():
        stages_out[stage_n] = {
            track: parse_mdp_date(cell_map.get(f"{col}{sr.row}", ""), sm.year, sm.wraps_year)
            for track, col in sr.cols.items()
        }

    of_out: dict[str, date | None] = {}
    if sm.global_orderfair:
        g = sm.global_orderfair
        of_out = {
            track: parse_mdp_date(cell_map.get(f"{col}{g.row}", ""), sm.year, sm.wraps_year)
            for track, col in g.cols.items()
        }
    return stages_out, of_out


# ──────────────────────────────────────────────────────
# 시트 ① 읽기·쓰기
# ──────────────────────────────────────────────────────

def _load_config() -> dict:
    with (ROOT / "config.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def read_sheet1_rows(sheets, master_sheet_id: str, sheet1_title: str) -> list[dict]:
    """시트 ①에서 hero_id 있는 행만 추출. 단계 1~4 baseline + global_orderfair_baseline 현재값 포함."""
    keys = [c.key for c in SHEET1_COLUMNS]
    end_col = col_letter(len(keys) - 1)
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=master_sheet_id,
        range=f"'{sheet1_title}'!A3:{end_col}102",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    rows = resp.get("values", [])

    key_idx = {k: i for i, k in enumerate(keys)}
    out = []
    for offset, row in enumerate(rows):
        padded = row + [""] * (len(keys) - len(row))
        hero_id = padded[key_idx["hero_id"]]
        if not hero_id:
            continue
        out.append({
            "row_number": offset + 3,
            "hero_id": hero_id,
            "season": padded[key_idx["season"]],
            "track": padded[key_idx["track"]],
            "stage1_baseline": padded[key_idx["stage1_baseline"]],
            "stage2_baseline": padded[key_idx["stage2_baseline"]],
            "stage3_baseline": padded[key_idx["stage3_baseline"]],
            "stage4_baseline": padded[key_idx["stage4_baseline"]],
            "global_orderfair_baseline": padded[key_idx["global_orderfair_baseline"]],
        })
    return out


# 컬럼 키 → letter 캐시
_KEY_TO_LETTER: dict[str, str] = {c.key: col_letter(i) for i, c in enumerate(SHEET1_COLUMNS)}


# ──────────────────────────────────────────────────────
# 메인 흐름 — dry-run 또는 apply
# ──────────────────────────────────────────────────────

def run(season: str, apply: bool) -> int:
    cfg = _load_config()
    hero_ops = cfg.get("hero_ops", {})
    master_id = hero_ops.get("master_sheet_id")
    if not master_id:
        print("[FAIL] config.yaml에 hero_ops.master_sheet_id가 없습니다.", file=sys.stderr)
        return 1

    creds = get_credentials(ROOT / "credentials.json", ROOT / "token.json")
    sheets = build_services(creds)["sheets"]

    # 시트 ① 이름 (gid → title 필요)
    meta = sheets.spreadsheets().get(spreadsheetId=master_id).execute()
    sheet1_gid = hero_ops.get("sheet1_gid", 0)
    sheet1_title = None
    for s in meta["sheets"]:
        if s["properties"]["sheetId"] == sheet1_gid:
            sheet1_title = s["properties"]["title"]
            break
    if not sheet1_title:
        print(f"[FAIL] sheet1_gid={sheet1_gid} 시트를 못 찾았습니다.", file=sys.stderr)
        return 1

    print(f"[시즌] {season}")
    print(f"[마스터 시트] {master_id}")
    print(f"[시트 ①] {sheet1_title}")
    print()

    # 1) MDP에서 baseline 추출
    print("[MDP 매핑]")
    sm = SEASON_MDP_MAP[season]
    baselines, orderfair = read_mdp_baselines(sheets, season)
    t1, t2 = sm.tracks
    for n, vals in baselines.items():
        sr = sm.stages[n]
        print(f"  단계 {n} ({sr.label})  R{sr.row}: {t1}={vals.get(t1)}  {t2}={vals.get(t2)}")
    if sm.global_orderfair:
        print(f"  글로벌 수주회  R{sm.global_orderfair.row}: {t1}={orderfair.get(t1)}  {t2}={orderfair.get(t2)}")
    print()

    # 2) 시트 ① 히어로 행 읽기
    heroes = read_sheet1_rows(sheets, master_id, sheet1_title)
    print(f"[시트 ① 히어로 행] {len(heroes)}개")

    # 3) 적재 plan 작성 (변경사항만)
    updates: list[dict] = []
    skipped: list[str] = []

    valid_tracks = set(sm.tracks)
    for h in heroes:
        if h["season"] != season:
            skipped.append(f"  {h['hero_id']}: 시즌 불일치 ({h['season']})")
            continue
        track = h["track"]
        if track not in valid_tracks:
            skipped.append(f"  {h['hero_id']}: track {track or '(빈)'} ∉ {sm.tracks}")
            continue

        # 단계 1~4 비교
        for n in (1, 2, 3, 4):
            new_date = baselines[n][track]
            if not new_date:
                continue
            current = h[f"stage{n}_baseline"]
            new_str = new_date.isoformat()
            if current == new_str:
                continue
            col = _KEY_TO_LETTER[f"stage{n}_baseline"]
            updates.append({
                "hero_id": h["hero_id"],
                "field": f"단계 {n}",
                "track": track,
                "current": current or "(빈)",
                "new": new_str,
                "range": f"'{sheet1_title}'!{col}{h['row_number']}",
            })

        # 글로벌 수주회 비교
        if orderfair:
            new_of = orderfair[track]
            if new_of:
                current_of = h["global_orderfair_baseline"]
                new_of_str = new_of.isoformat()
                if current_of != new_of_str:
                    col = _KEY_TO_LETTER["global_orderfair_baseline"]
                    updates.append({
                        "hero_id": h["hero_id"],
                        "field": "글로벌 수주회",
                        "track": track,
                        "current": current_of or "(빈)",
                        "new": new_of_str,
                        "range": f"'{sheet1_title}'!{col}{h['row_number']}",
                    })

    print(f"[변경 plan] {len(updates)}건")
    for u in updates:
        print(f"  {u['hero_id']:12s} {u['field']:>9s} ({u['track']})  {u['current']:>10s} → {u['new']}  @ {u['range']}")
    if skipped:
        print()
        print(f"[skip] {len(skipped)}건")
        for s in skipped:
            print(s)
    print()

    if not apply:
        print("=" * 60)
        print("dry-run 완료. 적재하려면 --apply 플래그 추가.")
        print("=" * 60)
        return 0

    if not updates:
        print("적재할 변경 없음.")
        return 0

    # 4) 일괄 적재 (batchUpdate values)
    data = [{"range": u["range"], "values": [[u["new"]]]} for u in updates]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=master_id,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    print(f"[OK] {len(updates)}건 적재 완료.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MDP → 시트 ① stage{1..4}_baseline 적재")
    p.add_argument("--season", default="27SS", choices=list(SEASON_MDP_MAP.keys()))
    p.add_argument("--apply", action="store_true", help="실제 적재 (기본 dry-run)")
    args = p.parse_args(argv)

    try:
        return run(args.season, args.apply)
    except Exception as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
