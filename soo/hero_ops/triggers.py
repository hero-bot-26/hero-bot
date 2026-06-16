"""히어로 단계 카운트다운 슬랙 알람 (D-7 / D-1 / D-DAY) + 입고 이벤트 알람.

확정 스펙 (project_hero_ops_system 메모리, 2026-05-28 / 2026-06-16 입고 보강):
- 알람 패턴 = D-1주(D-7) / D-1일(D-1) / D-DAY 3-스텝.
- 발화 단위 = STY × 단계 × D-N → **담당자 슬랙 자동 발송**. 화면엔 현황만(수동 X).
- 단계 0~4·6~12 = baseline(계획일) 기준 카운트다운.
- 단계 5(1차수량) = 알람 X (히스토리만).
- 단계 13(입고) = 카운트다운 X → 실제 물류 입고(입고 actual 발생) 시 이벤트 알람.
- 담당자 실명 = PLM(DBX) md_nm(상품MD) / ds_nm(디자인) / sc_nm(소싱).

완료 판정은 생성기(_gen_26fw_heroes.py)와 동일: actual + plm_status floor + carryover.

사용:
  python -m soo.hero_ops.triggers              # dry-run(오늘 기준, 출력만)
  python -m soo.hero_ops.triggers --sheet      # 자동화 시트에서 PLM 읽기(GH Actions 경로)
  python -m soo.hero_ops.triggers --asof=2026-02-17   # 기준일 시뮬레이션(D-7 검증용)
  python -m soo.hero_ops.triggers --send       # 본인 DM으로 실제 발송(테스트)
"""
from __future__ import annotations
import datetime
import sys
from collections import defaultdict
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops.plm_ingest import (
    parse_milestone_dbx, parse_milestone_dbx_from_drive,
    parse_milestone_dbx_from_sheet, DBX_SHEET_ID)

ROOT = Path(__file__).resolve().parents[2]   # hero_bot/
HERO_SHEET = "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"
TEST_DM_SLACK_ID = "U09BU1F85TR"  # sooyoung.moon

# ⚠️ 안전 하드락 — 절대 실제 담당자에게 발송하지 않음. 모든 발송은 본인 DM 테스트로만.
#    실운영 전환은 (1) TEST_ONLY=False (2) OWNER_SLACK_IDS 채움 둘 다 명시적으로 해야 함.
#    사용자 지시(2026-06-16): "슬랙 실제 실행은 절대 돌리지않고 나한테만 테스트로".
TEST_ONLY = True

# 담당자 실명 → Slack member ID. (users:read.email scope 없어 자동 lookup 불가 → 수기 등록.)
#   실운영 시 여기에 담당자 ID를 채우면 resolve_target 이 자동 라우팅.
#   현재는 본인만 등록 — TEST_ONLY 라 어차피 전부 본인 DM으로 감.
OWNER_SLACK_IDS: dict[str, str] = {
    "문수영": TEST_DM_SLACK_ID,
    "Sooyoung Moon": TEST_DM_SLACK_ID,
    # "홍유석": "U...", "박은진": "U...",  ← 실운영 시 채움
}


def resolve_target(owner: str) -> tuple[str, str]:
    """담당자 → (실제 발송 target, 라우팅 설명).

    TEST_ONLY 면 무조건 본인 DM. 아니면 OWNER_SLACK_IDS 매핑(없으면 본인 DM 폴백).
    """
    mapped = OWNER_SLACK_IDS.get(owner)
    if TEST_ONLY:
        note = f"테스트 → 본인 DM (원래 수신자: {owner}{'·매핑됨' if mapped else '·미매핑'})"
        return TEST_DM_SLACK_ID, note
    if mapped:
        return mapped, f"→ {owner}"
    return TEST_DM_SLACK_ID, f"미매핑({owner}) → 본인 DM 폴백"

import re
STYLE_RE = re.compile(r"^M[A-Z0-9]{8}$")

# 단계 정의 = app.html stages 와 동일. (라벨, 담당역할, 알람여부)
#   role: md=md_nm(상품MD) / ds=ds_nm(디자인) / sc=sc_nm(소싱)
STAGES: dict[int, tuple[str, str, bool]] = {
    0: ("MDP 정해짐", "md", True),
    1: ("히어로 진행", "md", True),
    2: ("매트릭스 합의", "md", True),
    3: ("품평회", "md", True),
    4: ("GO-DROP", "md", True),
    5: ("1차 수량 결정", "md", False),   # noAlert
    6: ("컬러 확정", "ds", True),
    7: ("원단 확정", "ds", True),
    8: ("PO 전송", "md", True),
    9: ("PO 작성", "sc", True),
    10: ("QC APP", "sc", True),
    11: ("사후원가 확정", "md", True),
    12: ("판매가 확정", "md", True),
    13: ("입고", "sc", False),           # 카운트다운 X — 입고 actual 이벤트로 별도 처리
}
ROLE_ATTR = {"md": "md_nm", "ds": "ds_nm", "sc": "sc_nm"}

# MDP 26FW 트랙별 베이스라인 (단계 n → 'YYYY-MM-DD'). _gen_26fw_heroes.BASELINE 와 동일.
BASELINE = {
    "가을": {3: "2025-12-19", 4: "2026-01-22", 6: "2026-01-28", 7: "2026-01-28",
            8: "2026-02-20", 9: "2026-02-20", 10: "2026-04-17", 11: "2026-05-01",
            12: "2026-05-01", 13: "2026-08-01"},
    "겨울": {3: "2026-01-14", 4: "2026-02-05", 6: "2026-02-24", 7: "2026-02-24",
            8: "2026-02-27", 9: "2026-02-27", 10: "2026-05-04", 11: "2026-05-25",
            12: "2026-05-25", 13: "2026-09-01"},
}
PLM_STATUS_FLOOR = {
    "New": 2, "Proto Approved": 3, "QC Confirmed": 10,
    "PO Issued": 9, "PP Confirmed": 12, "Final Cost Set": 11,
}
ORDER = [3, 4, 6, 7, 8, 9, 10, 11, 12, 13]
# 카운트다운 대상 = 알람 O + baseline 존재 (0~2는 baseline 없어 항상 done 취급).
COUNTDOWN_STAGES = [n for n in (3, 4, 6, 7, 8, 9, 10, 11, 12) if STAGES[n][2]]
DDAYS = {7: "D-1주", 1: "D-1일", 0: "D-DAY"}


def _d(s: str | None) -> datetime.date | None:
    return datetime.date.fromisoformat(s) if s and len(s) == 10 else None


def season_to_track(s: str) -> str:
    return "가을" if s == "간절기" else "겨울"


def load_styles(sheets, use_sheet: bool):
    """HERO STY 시트 → [{style, cls, team, season, name}] (HERO/HERO SUB 행만)."""
    book, rng = (DBX_SHEET_ID, "HERO_STY!A7:M400") if use_sheet else (HERO_SHEET, "'HERO STY'!A7:M400")
    res = sheets.spreadsheets().values().get(
        spreadsheetId=book, range=rng, valueRenderOption="UNFORMATTED_VALUE").execute()
    out = []
    for r in res.get("values", []):
        def c(i): return (str(r[i]).strip() if i < len(r) and r[i] is not None else "")
        if c(1) not in ("HERO", "HERO SUB"):
            continue
        style = c(2) or c(0)
        if not STYLE_RE.match(style) or not c(3):
            continue
        out.append({"style": style, "cls": c(1), "team": c(6),
                    "season": c(9), "name": c(12), "series": c(3)})
    return out


COMPLETIONS_TAB = "단계완료"   # SA 시트(DBX_SHEET_ID)의 완료 클릭 저장 탭


def load_completions(sheets, sheet_id: str = DBX_SHEET_ID) -> set[tuple[str, int]]:
    """단계완료 탭 → {(style_no, stage_n)} 완료 기록 집합. (수동 완료 단계 done 판정용.)"""
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{COMPLETIONS_TAB}!A2:B").execute()
    except Exception:
        return set()
    out = set()
    for r in res.get("values", []):
        if len(r) >= 2 and str(r[0]).strip() and str(r[1]).strip().isdigit():
            out.add((str(r[0]).strip(), int(str(r[1]).strip())))
    return out


def done_floor(rec) -> int:
    """이 단계 이하는 날짜 없어도 완료 간주 (생성기 규칙 A/B/C)."""
    if rec is None:
        return -1
    actual_stages = [n for n in ORDER if rec.stages.get(n) and rec.stages[n].actual]
    floor = max([-1] + actual_stages + [PLM_STATUS_FLOOR.get(rec.plm_status, -1)])
    if rec.carryover:
        floor = max(floor, 4)
    return floor


def stage_done(rec, n: int, floor: int) -> bool:
    cell = rec.stages.get(n) if rec else None
    if cell and cell.actual and len(cell.actual) == 10:
        return True
    return n <= floor


def compute_alarms(styles, plm, as_of: datetime.date, completions: set | None = None):
    """카운트다운 알람 + 입고 이벤트 알람 산출.

    반환: (countdown, inbound)
      countdown: list[ {owner, role, stage_n, label, dN, dlabel, base, stys:[{style,series}]} ]  (담당자×단계×D-N 그룹)
      inbound:   list[ {owner, style, series, actual} ]  (오늘 입고 actual 발생)
    """
    completions = completions or set()
    groups: dict[tuple, list] = defaultdict(list)   # (owner, stage_n, dN) -> [sty]
    inbound = []
    for row in styles:
        rec = plm.get(row["style"])
        if rec is None:            # PLM 미등록(신상/리뉴얼) — 날짜 불명, 카운트다운 제외
            continue
        if rec.plm_status == "Dropped":
            continue
        track = season_to_track(row.get("season", ""))
        bl = BASELINE[track]
        floor = done_floor(rec)

        # ── 카운트다운 단계 ──
        for n in COUNTDOWN_STAGES:
            # done = PLM actual/floor(자동 큐어) OR 완료시트 기록(수동 완료 클릭)
            if stage_done(rec, n, floor) or (row["style"], n) in completions:
                continue
            base = _d(bl.get(n))
            if not base:
                continue
            dN = (base - as_of).days
            if dN not in DDAYS:
                continue
            label, role, _ = STAGES[n]
            owner = getattr(rec, ROLE_ATTR[role], None) or "미지정"
            groups[(owner, n, dN)].append(row)

        # ── 입고(13) 이벤트: 입고 actual 이 as_of 당일 = 실제 물류 입고 시작 ──
        cell13 = rec.stages.get(13)
        if cell13 and cell13.actual and _d(cell13.actual) == as_of:
            owner = getattr(rec, ROLE_ATTR[STAGES[13][1]], None) or "미지정"
            inbound.append({"owner": owner, "style": row["style"],
                            "series": row["series"], "actual": cell13.actual})

    countdown = []
    for (owner, n, dN), stys in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0][1])):
        label, role, _ = STAGES[n]
        track_base = None  # 같은 단계라도 트랙별 base 다를 수 있어 메시지엔 단계명만
        countdown.append({
            "owner": owner, "role": role, "stage_n": n, "label": label,
            "dN": dN, "dlabel": DDAYS[dN],
            "stys": [{"style": s["style"], "series": s["series"]} for s in stys],
        })
    return countdown, inbound


def format_messages(countdown, inbound):
    """담당자별로 묶어 슬랙 메시지 텍스트 리스트 생성. 반환 [(owner, text)]."""
    by_owner: dict[str, list[str]] = defaultdict(list)
    for g in countdown:
        stys = ", ".join(f"{s['style']}({s['series']})" for s in g["stys"])
        by_owner[g["owner"]].append(
            f":alarm_clock: *{g['label']} {g['dlabel']}* — {len(g['stys'])} STY 미완\n>{stys}")
    for ib in inbound:
        by_owner[ib["owner"]].append(
            f":package: *입고 시작* — {ib['style']}({ib['series']}) 물류 입고 진행 ({ib['actual']})")
    out = []
    for owner, lines in by_owner.items():
        head = f"*[히어로 단계 알람] {owner}님*\n"
        out.append((owner, head + "\n".join(lines)))
    return out


def _utf8():
    """Windows 콘솔 cp949 → UTF-8 (이모지/em-dash 출력 시 UnicodeEncodeError 회피)."""
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


def main() -> int:
    _utf8()
    use_sheet = "--sheet" in sys.argv
    do_send = "--send" in sys.argv
    local = next((Path(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--local=")), None)
    asof_arg = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--asof=")), None)
    as_of = datetime.date.fromisoformat(asof_arg) if asof_arg else datetime.date.today()

    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sheets, drive = svc["sheets"], svc["drive"]

    styles = load_styles(sheets, use_sheet)
    if local:
        recs = parse_milestone_dbx(local)
    elif use_sheet:
        recs = parse_milestone_dbx_from_sheet(sheets)
    else:
        _, recs = parse_milestone_dbx_from_drive(drive)
    plm = {r.style_no: r for r in recs}
    completions = load_completions(sheets)
    print(f"기준일 {as_of} · STY {len(styles)} · PLM {len(plm)} · 완료기록 {len(completions)}")

    countdown, inbound = compute_alarms(styles, plm, as_of, completions)
    msgs = format_messages(countdown, inbound)

    n_alarm = sum(len(g["stys"]) for g in countdown)
    print(f"카운트다운 그룹 {len(countdown)} (STY발화 {n_alarm}) · 입고이벤트 {len(inbound)} · 담당자 {len(msgs)}명")
    for owner, text in msgs:
        print("\n" + "─" * 50)
        print(text)

    if do_send:
        import yaml
        from soo import persona
        from soo.secrets import load_secrets
        log = persona.setup_logger(ROOT / "logs", dry_run=False)
        bot_token = load_secrets(ROOT / "secrets.yaml")["slack_bot_token"]
        sent = 0
        for owner, text in msgs:
            target, note = resolve_target(owner)
            prefix = f":test_tube: *[{note}]*\n" if TEST_ONLY else ""
            ts = persona.send_slack(prefix + text, bot_token=bot_token,
                                    target=target, persona=persona.RANKING_BOT, log=log)
            print(f"  {owner}: {note} — {'OK' if ts else '실패'}")
            if ts:
                sent += 1
        mode = "테스트(본인 DM 전용)" if TEST_ONLY else "운영"
        print(f"\n[발송·{mode}] {sent}/{len(msgs)}")
    else:
        print("\n(dry-run — 실제 발송하려면 --send)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
