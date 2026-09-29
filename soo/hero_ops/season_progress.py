"""홈 시즌 진척 카드 — 기획 관리판 `#.상세일정` 에서 **라벨로** 단계·트랙 일자를 읽는다.

★행·열 번호를 박지 않는다. 27SS 카드가 행 하드코딩(122/124/129/138)이었는데 시트 행이 밀려
  '킥오프·매트릭스·GO-DROP·Initial PO' 자리에 샘플 도착·GO/DROP·수주회·PP 날짜가 찍혀 있었다(2026-09-29 발견).

블록   = B열에 `■ 무탠다드 {시즌} MAIN` 이 있는 행 ~ 다음 `■` 행 전
헤더   = 블록 안에서 B='KR' · C='Initiative' 인 행. 트랙 열 = 헤더 칸 텍스트 부분일치
단계행 = C열(Initiative) 부분일치. 키워드 우선순위대로, 트랙마다 **값이 있는 첫 행**의 일자
"""
from __future__ import annotations

import datetime as dt
import re

SHEET_ID = "10guWc_5t06nu9QryPymTIl2oogQfV4qOEO81iXSgenI"
TAB = "#.상세일정"
READ_RANGE = f"'{TAB}'!A1:P"

# 시즌별 카드 정의 — (단계번호, 카드 라벨, Initiative 키워드들(우선순위), {카드 트랙명: 헤더 키워드})
CARDS: dict[str, dict] = {
    "27SS": {
        "tracks": {"공통": "봄", "봄": "봄", "여름": "여름"},
        "stages": [
            (1, "킥오프",       ["방향성 합의"],                 ["공통"]),
            (2, "매트릭스 합의", ["Matrix 합의"],                 ["봄", "여름"]),
            (3, "GO-DROP",     ["GO / DROP", "GO/DROP"],        ["봄", "여름"]),
            (4, "Initial PO",  ["Initial PO"],                  ["봄", "여름"]),
        ],
    },
    # 27FW 트랙 = 표 구조 그대로 ①캐리오버 / ②MAIN / ③QR (사용자 확정 2026-09-29)
    "27FW": {
        "tracks": {"캐리오버": "캐리오버", "MAIN": "MAIN", "QR": "QR"},
        "stages": [
            (1, "실무 킥오프",   ["실무 방향성"],                   ["MAIN"]),
            (2, "매트릭스",      ["Matrix 합의", "Matrix 작성"],    ["캐리오버", "MAIN", "QR"]),
            (3, "킥오프 미팅",   ["세일즈/마케팅", "하이레벨 방향성"], ["MAIN"]),
            (4, "GO-DROP",      ["GO / DROP", "GO/DROP"],          ["MAIN", "QR"]),
            (5, "글로벌 수주회", ["글로벌 수주회 완료"],             ["MAIN"]),
            (6, "Initial PO",   ["Initial PO"],                    ["캐리오버", "MAIN", "QR"]),
        ],
    },
}

_MD = re.compile(r"(\d{1,2})/(\d{1,2})")


def _year_for(season: str, month: int) -> int:
    """'27FW' → 기획이 전년 7월부터 돈다(7~12월=2026, 1~6월=2027). '27SS' → 전년 3월부터."""
    yy = 2000 + int(season[:2])
    pivot = 7 if season.endswith("FW") else 3
    return yy - 1 if month >= pivot else yy


def _date(season: str, text) -> dt.date | None:
    m = _MD.search(str(text or ""))
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    try:
        return dt.date(_year_for(season, mo), mo, d)
    except ValueError:
        return None


def _cell(rows, r, c):
    row = rows[r] if r < len(rows) else []
    return str(row[c]).replace("\n", " ").strip() if c < len(row) else ""


def build_progress(sheets, season: str, today: dt.date, warns: list[str],
                   sheet_id: str | None = None) -> list[dict]:
    """[{stage,label,tracks:[{track,status,date,msg}]}]. 블록·헤더를 못 찾으면 [] + 경고."""
    cfg = CARDS[season]
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id or SHEET_ID, range=READ_RANGE,
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    start = next((i for i in range(len(rows))
                  if "■" in _cell(rows, i, 1) and season in _cell(rows, i, 1) and "MAIN" in _cell(rows, i, 1)), None)
    if start is None:
        warns.append(f"{season} 진척: `■ 무탠다드 {season} MAIN` 블록 없음 — 카드 직전값 유지")
        return []
    end = next((i for i in range(start + 1, len(rows)) if "■" in _cell(rows, i, 1)), len(rows))
    hdr = next((i for i in range(start, end)
                if _cell(rows, i, 1) == "KR" and _cell(rows, i, 2) == "Initiative"), None)
    if hdr is None:
        warns.append(f"{season} 진척: 헤더(KR/Initiative) 없음 — 카드 직전값 유지")
        return []
    # 같은 블록 아래 두 번째 표(예: 27FW R310 'AS-IS')는 읽지 않는다 — 다음 헤더 전에서 끊음
    end = next((i for i in range(hdr + 1, end)
                if _cell(rows, i, 1) == "KR" and _cell(rows, i, 2) == "Initiative"), end)
    hrow = rows[hdr]
    tcol: dict[str, int] = {}
    for tname, kw in cfg["tracks"].items():
        c = next((j for j in range(4, len(hrow)) if kw in _cell(rows, hdr, j)), None)
        if c is None:
            warns.append(f"{season} 진척: 트랙 '{kw}' 열 없음")
        else:
            tcol[tname] = c

    prog = []
    for stage, label, kws, tracks in cfg["stages"]:
        out = []
        for tname in tracks:
            if tname not in tcol:
                continue
            d = None
            for kw in kws:
                for r in range(hdr + 1, end):
                    if kw in _cell(rows, r, 2):
                        d = _date(season, _cell(rows, r, tcol[tname]))
                        if d:
                            break
                if d:
                    break
            if not d:
                continue
            md, delta = f"{d.month}/{d.day}", (d - today).days
            if delta < 0:
                status, msg = "done", f"✓ 완료 ({md})"
            elif delta == 0:
                status, msg = "imminent", f"D-DAY ({md})"
            elif delta <= 7:
                status, msg = "imminent", f"D-{delta} ({md})"
            else:
                status, msg = "upcoming", f"D-{delta} ({md})"
            out.append({"track": tname, "status": status, "date": d.isoformat(), "msg": msg})
        if out:
            prog.append({"stage": stage, "label": label, "tracks": out})
        else:
            warns.append(f"{season} 진척: 단계 '{label}' 일자 못 찾음(키워드 {kws})")
    print(f"[진척] {season} 블록 R{start + 1}~R{end} · 헤더 R{hdr + 1} · 트랙 {tcol} · "
          f"단계 {len(prog)}/{len(cfg['stages'])}")
    return prog


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8")
    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT))
    from soo.auth import build_services, get_credentials
    sh = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))["sheets"]
    for s in sys.argv[1:] or list(CARDS):
        w: list[str] = []
        p = build_progress(sh, s, dt.date.today(), w)
        for st in p:
            print(f"  {st['stage']}. {st['label']:10} " + " · ".join(f"{t['track']} {t['msg']}" for t in st["tracks"]))
        for x in w:
            print("  [경고]", x)
