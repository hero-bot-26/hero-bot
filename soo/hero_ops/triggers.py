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
TEST_CHANNEL = ""  # "히어로봇(슈퍼맨) 앱" 테스트 채널 ID. 비면 본인 DM 으로 테스트.


def _test_target() -> str:
    """TEST_ONLY 발송 목적지 — 히어로봇 테스트 채널 있으면 거기, 없으면 본인 DM."""
    return TEST_CHANNEL or TEST_DM_SLACK_ID

# ⚠️ 안전 하드락 — 절대 실제 담당자에게 발송하지 않음. 모든 발송은 본인 DM 테스트로만.
#    실운영 전환은 (1) TEST_ONLY=False (2) OWNER_SLACK_IDS 채움 둘 다 명시적으로 해야 함.
#    사용자 지시(2026-06-16): "슬랙 실제 실행은 절대 돌리지않고 나한테만 테스트로".
TEST_ONLY = True

# 수신자/팀장 매핑은 SA 시트 '담당자매핑' 탭에서 로드 (코드 수정 없이 시트만 채우면 됨).
OWNER_MAP_TAB = "담당자매핑"   # 컬럼: name/role/kind(담당자|팀장)/category/slack_id/sty_count/note
TEAM_CHANNEL = ""              # scope='all' 전체 공지 채널 (placeholder). 비면 미설정.
STAGE_SCOPE_ALL: set[int] = set()   # 전체 공지로 보낼 단계(예 {5}). 현재 비움 = 전부 담당자.

# 정적 시드(본인). 실담당자/팀장 Slack ID 는 load_owner_map 이 시트에서 채움.
OWNER_SLACK_IDS: dict[str, str] = {"문수영": TEST_DM_SLACK_ID, "Sooyoung Moon": TEST_DM_SLACK_ID}
TEAM_LEADS: list[dict] = []    # [{name, role, category, slack_id}] — load_owner_map 가 채움

# 상품MD 세부 카테고리 (히어로 item/series → 팀장 라우팅). 카테고리 팀장 없으면 전체 팀장(김병관).
CATEGORY_KEYS = {
    "언더웨어": ["underwear", "언더웨어", "이너", "심리스", "브라", "힛탠"],
    "ACC": ["acc", "액세서리", "양말", "벨트", "sock", "belt"],
    "키즈": ["kids", "키즈"],
}


def category_of(item: str, series: str = "") -> str:
    s = (str(item) + " " + str(series)).lower()
    for cat, keys in CATEGORY_KEYS.items():
        if any(k.lower() in s for k in keys):
            return cat
    return "전체"


def load_owner_map(sheets, sheet_id: str = DBX_SHEET_ID):
    """담당자매핑 탭 → OWNER_SLACK_IDS(담당자 중 slack_id 채워진 것) + TEAM_LEADS 갱신."""
    global TEAM_LEADS
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{OWNER_MAP_TAB}!A2:E").execute()
    except Exception:
        return
    leads = []
    for r in res.get("values", []):
        def c(i): return (str(r[i]).strip() if i < len(r) and r[i] is not None else "")
        name, role, kind, cat, sid = c(0), c(1), c(2), c(3) or "전체", c(4)
        if not name:
            continue
        if kind == "팀장":
            leads.append({"name": name, "role": role, "category": cat, "slack_id": sid})
        elif sid:
            OWNER_SLACK_IDS[name] = sid
    TEAM_LEADS = leads


def team_leads_for(role: str, category: str = "전체") -> list[dict]:
    """역할(상품MD는 카테고리 우선)에 해당하는 팀장. 카테고리 팀장 없으면 전체 팀장."""
    same = [l for l in TEAM_LEADS if l["role"] == role]
    if role == "md" and category != "전체":
        cat = [l for l in same if l["category"] == category]
        if cat:
            return cat
    return [l for l in same if l["category"] == "전체"] or same


def route_recipients(owner: str, role: str, category: str, has_dday: bool) -> list[tuple[str | None, str]]:
    """(a)에스컬레이션 + (b)미매칭 폴백 라우팅. 반환 list[(slack_id|None, 라벨)] = 의도된 수신자.
    실제 발송 target 은 send 단계서 TEST_ONLY 면 본인 DM 으로 치환되고, 라벨은 메시지에 표기."""
    recips: list[tuple[str | None, str]] = []
    osid = OWNER_SLACK_IDS.get(owner)
    leads = team_leads_for(role, category)
    if osid:
        recips.append((osid, owner))
        if has_dday:                                    # (a) D-DAY 에스컬레이션 → 팀장 추가
            for l in leads:
                recips.append((l["slack_id"] or None, f"{l['name']} 팀장(에스컬레이션)"))
    elif leads:                                          # (b) 담당자 미매칭 → 팀장 폴백
        for l in leads:
            recips.append((l["slack_id"] or None, f"{l['name']} 팀장(폴백·{owner} 미매칭)"))
    else:
        recips.append((None, f"{owner}(미매칭·팀장없음)"))
    return recips

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
        out.append({"style": style, "cls": c(1), "team": c(6), "item": c(7),
                    "season": c(9), "name": c(12), "series": c(3),
                    "category": category_of(c(7), c(3))})
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


QUANTITY_TAB = "1차수량"   # SA 시트(DBX_SHEET_ID): hero(히어로명), role, qty, by, at — append 순서라 마지막=최신


def load_quantity_inputs(sheets, sheet_id: str = DBX_SHEET_ID) -> dict:
    """1차수량 탭 → {hero_name: {role: {qty:int, by:str, at:str}}}. 같은 (hero,role) 중복이면 최신(마지막) 우선.

    key=히어로명(series). 위치기반 id는 매 생성마다 바뀌므로 안정 키로 히어로명을 쓴다.
    role ∈ {planning_md, online_sales, offline_sales}.
    """
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{QUANTITY_TAB}!A2:E").execute()
    except Exception:
        return {}
    out: dict = {}
    for r in res.get("values", []):
        if len(r) < 3:
            continue
        hero, role = str(r[0]).strip(), str(r[1]).strip()
        if not hero or not role:
            continue
        try:
            qty = int(round(float(str(r[2]).replace(",", "").strip())))
        except (TypeError, ValueError):
            continue
        by = str(r[3]).strip() if len(r) > 3 else ""
        at = str(r[4]).strip() if len(r) > 4 else ""
        out.setdefault(hero, {})[role] = {"qty": qty, "by": by, "at": at}
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
            inbound.append({"owner": owner, "role": STAGES[13][1],
                            "category": row.get("category", "전체"),
                            "style": row["style"], "series": row["series"],
                            "actual": cell13.actual})

    countdown = []
    for (owner, n, dN), stys in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0][1])):
        label, role, _ = STAGES[n]
        cats = [s.get("category", "전체") for s in stys]
        category = max(set(cats), key=cats.count) if cats else "전체"
        countdown.append({
            "owner": owner, "role": role, "stage_n": n, "label": label,
            "dN": dN, "dlabel": DDAYS[dN], "category": category,
            "scope": "all" if n in STAGE_SCOPE_ALL else "owner",
            "stys": [{"style": s["style"], "series": s["series"]} for s in stys],
        })
    return countdown, inbound


def format_messages(countdown, inbound):
    """담당자별/전체로 묶어 메시지 생성.
    반환 list[dict]: {kind:'owner'|'all', owner, role, category, has_dday, text}."""
    def cd_line(g):
        stys = ", ".join(f"{s['style']}({s['series']})" for s in g["stys"])
        return f":alarm_clock: *{g['label']} {g['dlabel']}* — {len(g['stys'])} STY 미완\n>{stys}"

    owner_lines: dict[str, list[str]] = defaultdict(list)
    owner_meta: dict[str, dict] = {}
    all_lines: list[str] = []   # scope='all' 전체 공지

    for g in countdown:
        if g.get("scope") == "all":
            all_lines.append(cd_line(g))
            continue
        o = g["owner"]
        owner_lines[o].append(cd_line(g))
        m = owner_meta.setdefault(o, {"role": g["role"], "category": "전체", "has_dday": False})
        if g["dN"] == 0:
            m["has_dday"] = True
        if m["category"] == "전체" and g["category"] != "전체":
            m["category"] = g["category"]
    for ib in inbound:
        o = ib["owner"]
        owner_lines[o].append(
            f":package: *입고 시작* — {ib['style']}({ib['series']}) 물류 입고 진행 ({ib['actual']})")
        owner_meta.setdefault(o, {"role": ib.get("role", "sc"),
                                  "category": ib.get("category", "전체"), "has_dday": False})

    out = []
    for owner, lines in owner_lines.items():
        m = owner_meta[owner]
        out.append({"kind": "owner", "owner": owner, "role": m["role"],
                    "category": m["category"], "has_dday": m["has_dday"],
                    "text": f"*[히어로 단계 알람] {owner}님*\n" + "\n".join(lines)})
    if all_lines:
        out.append({"kind": "all", "owner": "전체", "role": "", "category": "전체",
                    "has_dday": False,
                    "text": "*[히어로 단계 알람 · 전체 공지]*\n" + "\n".join(all_lines)})
    return out


ALARM_LOG_TAB = "알람발송로그"   # 중복발송 방지(dedup): (sent_date, key) — 하루 1회/수신단위.


def load_sent_today(sheets, as_of: datetime.date, sheet_id: str = DBX_SHEET_ID) -> set[str]:
    """오늘(as_of) 이미 발송된 key 집합. (같은 날 재실행 시 중복 방지.)"""
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{ALARM_LOG_TAB}!A2:B").execute()
    except Exception:
        return set()
    d = as_of.isoformat()
    return {str(r[1]).strip() for r in res.get("values", [])
            if len(r) >= 2 and str(r[0]).strip() == d}


def record_sent(sheets, as_of: datetime.date, key: str, labels: str, sheet_id: str = DBX_SHEET_ID):
    try:
        sheets.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=f"{ALARM_LOG_TAB}!A:D", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[as_of.isoformat(), key, labels, as_of.isoformat()]]}).execute()
    except Exception as e:
        print(f"  ⚠️ dedup 기록 실패({key}): {e}")


def _slack_token():
    """Slack 봇 토큰 — CI는 env SLACK_BOT_TOKEN, 로컬은 secrets.yaml."""
    import os
    t = os.environ.get("SLACK_BOT_TOKEN")
    if t:
        return t.strip()
    try:
        from soo.secrets import load_secrets
        return load_secrets(ROOT / "secrets.yaml").get("slack_bot_token")
    except Exception:
        return None


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
    load_owner_map(sheets)
    matched = sum(1 for v in OWNER_SLACK_IDS.values() if v != TEST_DM_SLACK_ID)
    print(f"기준일 {as_of} · STY {len(styles)} · PLM {len(plm)} · 완료기록 {len(completions)}")
    print(f"담당자 매핑 {matched}명(slack_id 채움) · 팀장 {len(TEAM_LEADS)}명 · TEST_ONLY={TEST_ONLY}")

    countdown, inbound = compute_alarms(styles, plm, as_of, completions)
    msgs = format_messages(countdown, inbound)

    n_alarm = sum(len(g["stys"]) for g in countdown)
    print(f"카운트다운 그룹 {len(countdown)} (STY발화 {n_alarm}) · 입고이벤트 {len(inbound)} · 담당자 {len(msgs)}명")
    for m in msgs:
        print("\n" + "─" * 50)
        if m["kind"] == "all":
            note = "전체 공지" + ("" if TEAM_CHANNEL else "(채널 미설정)")
        else:
            note = " / ".join(lbl for _, lbl in route_recipients(
                m["owner"], m["role"], m["category"], m["has_dday"]))
        print(f"[라우팅 → {note}]")
        print(m["text"])

    if do_send:
        from soo import persona
        bot_token = _slack_token()
        if not bot_token:
            print("⚠️ Slack 봇 토큰 없음(env SLACK_BOT_TOKEN/secrets.yaml) — 발송 스킵")
            return 0
        log = persona.setup_logger(ROOT / "logs", dry_run=False)
        sent_keys = load_sent_today(sheets, as_of)   # dedup: 오늘 이미 보낸 것
        sent = 0
        for m in msgs:
            key = m["owner"]
            if key in sent_keys:
                print(f"  {key}: 오늘 이미 발송 — 스킵(dedup)")
                continue
            if m["kind"] == "all":
                recips = [(TEAM_CHANNEL or None, "전체 공지" + ("" if TEAM_CHANNEL else "(채널 미설정)"))]
            else:
                recips = route_recipients(m["owner"], m["role"], m["category"], m["has_dday"])
            labels = " / ".join(lbl for _, lbl in recips)
            ok = False
            if TEST_ONLY:
                dest = "히어로봇 채널" if TEST_CHANNEL else "본인 DM"
                prefix = f":test_tube: *[테스트 → {dest} | 원래 수신: {labels}]*\n"
                ts = persona.send_slack(prefix + m["text"], bot_token=bot_token,
                                        target=_test_target(), persona=persona.RANKING_BOT, log=log)
                print(f"  {key}: 테스트 → {labels} — {'OK' if ts else '실패'}")
                ok = bool(ts)
            else:
                for sid, lbl in recips:
                    tgt = sid or _test_target()
                    note = lbl if sid else f"{lbl}·미매핑→본인DM폴백"
                    ts = persona.send_slack(f":bell: *[{note}]*\n" + m["text"], bot_token=bot_token,
                                            target=tgt, persona=persona.RANKING_BOT, log=log)
                    print(f"  → {note} — {'OK' if ts else '실패'}")
                    ok = ok or bool(ts)
            if ok:
                record_sent(sheets, as_of, key, labels)   # dedup 기록
                sent_keys.add(key)
                sent += 1
        mode = f"테스트({'히어로봇 채널' if TEST_CHANNEL else '본인 DM'} 전용)" if TEST_ONLY else "운영"
        print(f"\n[발송·{mode}] 발송 {sent}건 (dedup으로 스킵 제외)")
    else:
        print("\n(dry-run — 실제 발송하려면 --send)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
