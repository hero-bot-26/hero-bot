"""IMC-3: 발매 노출 스킴 자동 알람 (히어로 운영 시스템 뒷단 1탄).

발매스케줄(품번별 발매일) × 콘텐츠별 노출 스킴(발매일 기준 상대 타이밍) 을 결합해
발매 카운트다운 알람을 온라인 담당에게 발송. 앞단 triggers.py 의 발송·잠금·dedup 재사용.

확정 스펙 (project_hero_ops_system 메모리, 2026-06-18 IMC 정독):
- 발매일 진실소스 = ★MSTRD_26FW 상품MAP '발매스케줄' 탭 (C=등급 D=신품번 E=시리즈 H=팀 N=품명 O=발매일).
- 노출 룰 = '콘텐츠별 노출 스킴'(드롭): 발매 D-2 티징 → D-DAY 발매 노출(발매판/퀵버튼/앱스플래시/검색/뉴스).
  (시트 병합셀이라 룰은 코드에 고정. 변경 시 SCHEME 갱신.)
- 담당 라우팅 = 발매스케줄 팀(남/여/키즈) → 온라인MD R&R. 실발송은 triggers.TEST_ONLY 잠금(본인 DM만).

사용:
  python -m soo.hero_ops.imc_triggers                 # dry-run(오늘)
  python -m soo.hero_ops.imc_triggers --asof=2026-07-06   # 기준일 시뮬
  python -m soo.hero_ops.imc_triggers --send          # 발송(TEST_ONLY=본인 DM)
"""
from __future__ import annotations
import datetime
import re
import sys
from collections import defaultdict
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops import triggers as T   # 공유 인프라(TEST_ONLY/_test_target/_slack_token/dedup/persona)

ROOT = T.ROOT
RELEASE_SHEET = T.HERO_SHEET          # ★MSTRD_26FW 상품MAP
RELEASE_TAB = "발매스케줄"
GRADES = ("HERO", "HERO SUB", "핵심상품")

# 노출 스킴(드롭) — 발매 D-N 별 핵심 액션. 소스 '콘텐츠별 노출 스킴' 탭(병합셀이라 고정).
SCHEME = {
    7: ("노출 스킴 준비", "드롭 구좌 예약 · 콘텐츠/촬영 준비 (무신사 릴리즈·에디션)"),
    2: ("티징 시작", "무신사 릴리즈 티징 · 추천판 빅배너(D-2~) · 앱푸시 스케줄"),
    0: ("발매 노출 ON", "발매판 · 퀵버튼 · 앱스플래시 · 검색창 키워드 · 검색결과 배너 · 뉴스 · SNS"),
}
IMC_DDAYS = {7: "발매 D-7", 2: "발매 D-2", 0: "발매 D-DAY"}

# 발매스케줄 팀 → 온라인MD 담당 (온라인MD팀 R&R). 실 Slack ID 는 담당자매핑 시트에 채우면 매칭.
ONLINE_LEADS = {"남성": "유다휘", "여성": "한상은", "키즈": "이지현", "글로벌": "신명철"}

_EPOCH = datetime.date(1899, 12, 30)


def _to_date(v) -> datetime.date | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return _EPOCH + datetime.timedelta(days=int(v))
        except (ValueError, OverflowError):
            return None
    m = re.findall(r"\d+", str(v or ""))
    if len(m) >= 3:
        try:
            return datetime.date(int(m[0]), int(m[1]), int(m[2]))
        except ValueError:
            return None
    return None


def load_releases(sheets) -> list[dict]:
    """발매스케줄 → [{style, series, team, name, grade, release(date)}] (HERO/SUB/핵심 + 발매일 有)."""
    res = sheets.spreadsheets().values().get(
        spreadsheetId=RELEASE_SHEET, range=f"'{RELEASE_TAB}'!A10:O400",
        valueRenderOption="UNFORMATTED_VALUE").execute()
    out = []
    for r in res.get("values", []):
        def c(i): return r[i] if i < len(r) and r[i] is not None else ""
        grade = str(c(2)).strip()
        if grade not in GRADES:
            continue
        style = str(c(3)).strip()
        rel = _to_date(c(14))
        if not style or not rel:
            continue
        out.append({"style": style, "series": str(c(4)).strip(), "team": str(c(7)).strip(),
                    "name": str(c(13)).strip(), "grade": grade, "release": rel})
    return out


# ── 무탠본부 아이템마스터: 26FW 발매일자 진실소스 ──────────────────────────
# 배경: '발매스케줄'(상품MAP)은 stale — 리커버리는 품번이 '발주전'(MDP 미링크)이라
#   발매일이 통째로 빠지고, 힛탠다드·웜팬츠·데님 등은 지난 날짜가 남아있음. 기획MD팀이
#   실제 발매일을 관리하는 곳은 이 '무탠본부_아이템 코드 관리' 시트의 '무탠' 탭 B열.
#   '히어로(26FW)' 탭이 대표품번→시리즈(히어로) 매핑, 리커버리는 품명 [리커버리] 브라켓으로 보강.
MUTAN_SHEET = "1rVbq1UVwKAdNApYovVDPF9ALwoE-v1KhNZUyHtf_bn4"
MUTAN_TAB = "무탠"                 # 아이템마스터: B=발매일정 C=UID D=신품번 E=대표품번 F=컬러 G=품명 H=발매시즌
MUTAN_HERO_TAB = "히어로(26FW)"     # A/C=대표품번 B=HERO/SUB D=시리즈 F=신규/캐리 M=품명
# 대표품번이 아직 '발주전'이라 레지스트리로 못 잡는 히어로 → 품명 [키워드] 브라켓으로 매칭
MUTAN_BRACKET_HEROES = {"리커버리": "리커버리"}


def _mutan_date(s) -> datetime.date | None:
    m = re.findall(r"\d+", str(s or ""))
    if len(m) < 3:
        return None
    y, mo, d = int(m[0]), int(m[1]), int(m[2])
    if y < 100:
        y += 2000
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def load_mutan_release_dates(sheets) -> dict:
    """무탠본부 시트 → 26FW 발매일자 진실소스.

    반환 {
      "rep_first": {대표품번: 최초 발매일(date)},          # 스타일 단위 날짜 오버라이드용
      "heroes": {시리즈명: {"dates":[date..], "reps":{대표품번..},
                            "new":int, "carry":int,
                            "events":[{style, name, release}]}},   # events=대표품번 단위 최초일
    }
    """
    # 1) 히어로(26FW): 대표품번 → 시리즈 / 신규·캐리
    hv = sheets.spreadsheets().values().get(
        spreadsheetId=MUTAN_SHEET, range=f"'{MUTAN_HERO_TAB}'!A7:M400").execute().get("values", [])
    rep_series, rep_nc, rep_grade = {}, {}, {}
    for r in hv:
        def h(i): return str(r[i]).strip() if i < len(r) and r[i] is not None else ""
        ser = h(3); rep = h(2) or h(0)
        if not ser or ser == "-" or not rep or rep in ("-", "발주전"):
            continue
        rep_series.setdefault(rep, ser)
        rep_nc.setdefault(rep, "캐리" if "캐리" in h(5) else "신규")
        _g = h(1).upper()   # B열: HERO / HERO SUB
        rep_grade.setdefault(rep, "HERO SUB" if "SUB" in _g else "HERO")

    # 2) 무탠 아이템마스터: 26FW 발매일 있는 행 → 대표품번/히어로별 집계
    mv = sheets.spreadsheets().values().get(
        spreadsheetId=MUTAN_SHEET, range=f"'{MUTAN_TAB}'!B12:H30000").execute().get("values", [])
    rep_first: dict[str, datetime.date] = {}
    heroes: dict[str, dict] = {}
    for r in mv:
        def c(i): return str(r[i]).strip() if i < len(r) and r[i] is not None else ""
        rel = _mutan_date(c(0)); season = c(6); rep = c(3); name = c(5)   # B발매·H시즌·E대표·G품명
        if not rel or not season.startswith("26FW") or not rep:
            continue
        if rep not in rep_first or rel < rep_first[rep]:
            rep_first[rep] = rel
        ser = rep_series.get(rep)
        if not ser:
            for kw, hero in MUTAN_BRACKET_HEROES.items():
                if f"[{kw}]" in name:
                    ser = hero
                    break
        if not ser:
            continue
        h = heroes.setdefault(ser, {"dates": set(), "reps": set(), "events": {}})
        h["dates"].add(rel); h["reps"].add(rep)
        ev = h["events"]
        if rep not in ev or rel < ev[rep]["release"]:
            ev[rep] = {"style": rep, "name": name, "release": rel, "grade": rep_grade.get(rep, "HERO")}

    out = {}
    for ser, h in heroes.items():
        new = sum(1 for rep in h["reps"] if rep_nc.get(rep) != "캐리")   # 레지스트리 미상(리커버리 등)=신규 취급
        carry = len(h["reps"]) - new
        out[ser] = {
            "dates": sorted(h["dates"]), "reps": h["reps"], "new": new, "carry": carry,
            "events": sorted(h["events"].values(), key=lambda e: (e["release"], e["style"])),
        }
    return {"rep_first": rep_first, "heroes": out}


def compute_imc(releases, as_of: datetime.date):
    """(owner, dN) → [release] 그룹. dN ∈ SCHEME(7/2/0)."""
    groups: dict[tuple, list] = defaultdict(list)
    for rel in releases:
        dN = (rel["release"] - as_of).days
        if dN not in SCHEME:
            continue
        owner = ONLINE_LEADS.get(rel["team"], "온라인MD")
        groups[(owner, dN)].append(rel)
    return groups


def format_imc(groups):
    """반환 list[dict]: {owner, dN, text}."""
    out = []
    for (owner, dN), rels in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        label, action = SCHEME[dN]
        lines = [f":loudspeaker: *{label} ({IMC_DDAYS[dN]})* — {len(rels)}건 발매 예정",
                 f">_{action}_"]
        for r in sorted(rels, key=lambda x: x["release"]):
            lines.append(f"• {r['style']}({r['series']}/{r['grade']}) {r['name']} — 발매 {r['release']}")
        out.append({"owner": owner, "dN": dN, "key": f"imc3:{owner}:{dN}",
                    "text": f"*[IMC 발매 노출 알람] {owner}님*\n" + "\n".join(lines)})
    return out


# ── IMC-4: 캠페인/기획전 역산 알람 ────────────────────────────────────────────
CAMPAIGN_SHEET = "13P65W4wjZBkDfoKQ1X1s9Uc2eG84vMH1etw2ewCNHdE"  # [통합] 26년 무탠다드 프로모션 스케줄
CAMPAIGN_TAB = "26년 캠페인_특별 기획전"
SEASON_YEAR = 2026                 # 시트 '26년'. "M/D(요일)" 날짜의 연도.
AD_LEAD_DAYS = 20                  # 광고 신청 마감 = 캠페인 시작 D-20 (시트 메모 "D-20일 전 신청 필수")
RECKON_DDAYS = {3: "마감 D-3", 1: "마감 D-1", 0: "마감 D-DAY"}   # 역산 마일스톤 카운트다운


def _md_to_date(s, year=SEASON_YEAR) -> datetime.date | None:
    """"5/8(금)" / "12/12(금)" → date(year, m, d). 숫자 2개 이상만."""
    m = re.findall(r"\d+", str(s or ""))
    if len(m) >= 2:
        try:
            return datetime.date(year, int(m[0]), int(m[1]))
        except ValueError:
            return None
    return None


def _kor_name(s) -> str:
    s = str(s or "").strip()
    return s if re.fullmatch(r"[가-힣]{2,4}", s) else ""


def load_campaigns(sheets) -> list[dict]:
    """특별기획전 → [{owner, gubun, name, brand, start, design_due}]. (구분·캠페인명·시작일 有)
    담당 = D열(담당자) 한글이름, 없으면 B열(광고번호칸에 약칭 들어오는 경우) 한글이름, 그래도 없으면 '캠페인담당'."""
    res = sheets.spreadsheets().values().get(
        spreadsheetId=CAMPAIGN_SHEET, range=f"'{CAMPAIGN_TAB}'!B6:L1013",
        valueRenderOption="FORMATTED_VALUE").execute()
    out = []
    for r in res.get("values", []):
        def c(i): return r[i] if i < len(r) and r[i] is not None else ""
        gubun, name = str(c(1)).strip(), str(c(4)).strip()    # C구분, F캠페인명
        start = _md_to_date(c(5))                              # G시작일
        if not gubun or not name or not start:
            continue
        owner = _kor_name(c(2)) or _kor_name(c(0)) or "캠페인담당"   # D담당자 → B → 폴백
        out.append({"owner": owner, "gubun": gubun, "name": name,
                    "brand": str(c(3)).strip(), "start": start,
                    "design_due": _md_to_date(c(9))})          # K디자인요청
    return out


def compute_campaign_alarms(camps, as_of: datetime.date):
    """역산 마일스톤(광고신청 마감=시작D-20 / 디자인요청 마감=K) 카운트다운.
    반환 (owner, milestone_label, dN) → [camp]."""
    groups: dict[tuple, list] = defaultdict(list)
    for cp in camps:
        milestones = [("광고신청", cp["start"] - datetime.timedelta(days=AD_LEAD_DAYS))]
        if cp["design_due"]:
            milestones.append(("디자인요청", cp["design_due"]))
        for label, due in milestones:
            dN = (due - as_of).days
            if dN in RECKON_DDAYS:
                groups[(cp["owner"], label, dN)].append(cp)
    return groups


def format_campaign(groups):
    """반환 list[dict]: {owner, dN, key, text}."""
    out = []
    for (owner, label, dN), camps in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0][0])):
        lines = [f":memo: *{label} {RECKON_DDAYS[dN]}* — {len(camps)}건"]
        for cp in sorted(camps, key=lambda x: x["start"]):
            lines.append(f"• [{cp['gubun']}] {cp['name']} ({cp['brand']}) — 캠페인 시작 {cp['start']}")
        out.append({"owner": owner, "dN": dN, "key": f"imc4:{owner}:{label}:{dN}",
                    "text": f"*[IMC 캠페인 역산 알람] {owner}님*\n" + "\n".join(lines)})
    return out


# ── IMC-6: 오프라인 조닝 시즌전환/빅캠페인 게이트 알람 ────────────────────────
OFFLINE_SHEET = "1YkJchgCn7B5LCjbNFU5-Fg7LrjIscIkEff9N7PHl8bY"   # ★MSTRD_26FW 오프라인 VM 플랜
OFFLINE_TAB = "오프라인 조닝 플랜"
OFFLINE_OWNER = "오프라인VMD"
GATE_DDAYS = {7: "D-7", 1: "D-1", 0: "D-DAY"}
OFFLINE_ROWS = {"Big Campaign": "빅캠페인", "Holyday": "명절", "New Open": "신규오픈", "Re New": "리뉴얼"}


def load_offline_gates(sheets) -> list[dict]:
    """조닝 플랜의 캠페인/명절/매장오픈 행 → [{label, date, kind, season_gate}].
    셀 텍스트 '라벨(M/D…)' 파싱. season_gate=시즌전환(FA/WI, 매장 존 전체 교체)."""
    res = sheets.spreadsheets().values().get(
        spreadsheetId=OFFLINE_SHEET, range=f"'{OFFLINE_TAB}'!A7:BD22",
        valueRenderOption="FORMATTED_VALUE").execute()
    rows = res.get("values", [])
    out = []
    for key, kind in OFFLINE_ROWS.items():
        row = next((r for r in rows if any(key in str(c) for c in r[:4])), None)
        if not row:
            continue
        for cell in row:
            s = str(cell or "").strip()
            if not s or key in s:
                continue
            m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", s)   # 첫 M/D = 게이트일
            if not m:
                continue
            label = re.split(r"[(（\n]", s)[0].strip()
            try:
                d = datetime.date(SEASON_YEAR, int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
            out.append({"label": label, "date": d, "kind": kind,
                        "season_gate": ("시즌변경" in s or "시즌전환" in s)})
    return out


# 오프라인 '전개 플랜' 본문 파싱 — 히어로별 월·주차 조닝 전개 + 브랜드협업/IP.
#   그리드 = 월(R7)×주차(R13, 각 2열) 가로 레이아웃. 콘텐츠 셀을 좌측 주차-시작열로 스냅.
#   히어로 행(HERO 섹션, C열 라벨) = 주차 근사일(월×주). ISSUE 행 = 셀에 '(M/D)' 명시일.
_ROLLOUT_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUNE": 6,
    "JUL": 7, "JULY": 7, "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBER": 10, "NOV": 11, "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12,
}
# 히어로 행: C열 라벨에 이 키워드 있으면 히어로 전개 행. 매칭은 생성기 별칭이 하니 표시용.
_ROLLOUT_HEROES = ["커브드", "라이트 다운", "힛탠", "빅토리아", "그리드", "메시", "플러피", "폴라", "웜 팬츠", "리커버리"]


def _rollout_colmap(month_row, week_row):
    """(month_row, week_row) → {주차_시작열: (month, week)}. 월/주차 라벨 위치에서 동적 생성."""
    month_cols = []
    for j, c in enumerate(month_row):
        s = str(c or "").strip().upper()
        if s in _ROLLOUT_MONTHS:
            month_cols.append((j, _ROLLOUT_MONTHS[s]))

    def month_at(j):
        m = None
        for jc, mm in month_cols:
            if jc <= j:
                m = mm
        return m

    cmap = {}
    for j, c in enumerate(week_row):
        mm = re.match(r"(\d)\s*W", str(c or "").strip())
        if mm and month_at(j):
            cmap[j] = (month_at(j), int(mm.group(1)))
    return cmap


def load_offline_rollout(sheets) -> list[dict]:
    """조닝 플랜 본문 → [{title, sub, date, approx, owner}].
    ①히어로별 전개(주차 근사일) ②브랜드협업·IP/그래픽(명시일). 타입=오프라인."""
    res = sheets.spreadsheets().values().get(
        spreadsheetId=OFFLINE_SHEET, range=f"'{OFFLINE_TAB}'!A1:BD42",
        valueRenderOption="FORMATTED_VALUE").execute()
    rows = res.get("values", [])

    def _row(i):
        return rows[i] if i < len(rows) else []

    def _cell(r, j):
        return str(r[j]).strip() if j < len(r) and r[j] is not None else ""

    # 헤더행 탐색: 월 라벨(JULY 등) 최다 행 = month_row, 주차 라벨('1W'등) 최다 행 = week_row.
    #   시트에 월/주차 헤더 그리드가 2벌(게이트용·히어로용) 있으나 컬럼 위치 동일 → 최다 행 하나면 충분.
    scan = range(min(25, len(rows)))
    m_idx = max(scan, key=lambda i: sum(1 for c in _row(i)
                if str(c or "").strip().upper() in _ROLLOUT_MONTHS), default=6)
    w_idx = max(scan, key=lambda i: sum(1 for c in _row(i)
                if re.match(r"\dW", str(c or "").strip())), default=m_idx + 1)
    cmap = _rollout_colmap(_row(m_idx), _row(w_idx))
    starts = sorted(cmap)

    def snap(j):
        prev = None
        for s in starts:
            if s <= j:
                prev = s
            else:
                break
        return prev

    out = []
    # ① 히어로별 전개 행: C열(idx2) 라벨이 히어로 키워드인 행.
    for r in rows:
        label = _cell(r, 2).lstrip("★☆*∙ ").strip()
        if not label or not any(k in label for k in _ROLLOUT_HEROES):
            continue
        for j in range(3, len(r)):
            s = _cell(r, j)
            if not s or s == "Extension":
                continue
            sc = snap(j)
            if sc is None:
                continue
            mo, wk = cmap[sc]
            try:
                d = datetime.date(SEASON_YEAR, mo, min((wk - 1) * 7 + 1, 28))
            except ValueError:
                continue
            out.append({"title": f"[{label}] {s}", "sub": f"오프라인 전개 · {mo}월 {wk}주",
                        "date": d, "approx": True, "owner": OFFLINE_OWNER})
    # ② ISSUE 행(브랜드 협업 / IP·그래픽): 셀 '(M/D)라벨' 세그먼트별 명시일.
    for r in rows:
        cat = _cell(r, 2)
        if "협업" not in cat and "IP" not in cat and "그래픽" not in cat:
            continue
        tag = "브랜드 협업" if "협업" in cat else "IP·그래픽 티셔츠"
        for j in range(3, len(r)):
            s = _cell(r, j)
            if not s:
                continue
            for seg in re.split(r"(?=\(\s*\d{1,2}\s*/\s*\d{1,2}\s*\))", s):
                seg = seg.strip()
                m = re.search(r"\(\s*(\d{1,2})\s*/\s*(\d{1,2})\s*\)", seg)
                if not m:
                    continue
                try:
                    d = datetime.date(SEASON_YEAR, int(m.group(1)), int(m.group(2)))
                except ValueError:
                    continue
                name = re.sub(r"\(\s*\d{1,2}\s*/\s*\d{1,2}\s*\)", "", seg).strip()
                if not name:
                    continue
                out.append({"title": f"[{tag}] {name}", "sub": f"오프라인 · {tag}",
                            "date": d, "approx": False, "owner": OFFLINE_OWNER})
    return out


def compute_offline_alarms(gates, as_of: datetime.date):
    """게이트 카운트다운(D-7/D-1/D-DAY). 반환 (dN) → [gate]."""
    groups: dict[int, list] = defaultdict(list)
    for g in gates:
        dN = (g["date"] - as_of).days
        if dN in GATE_DDAYS:
            groups[dN].append(g)
    return groups


def format_offline(groups):
    """반환 list[dict]: {owner, dN, key, text}."""
    out = []
    for dN, gates in sorted(groups.items()):
        lines = [f":department_store: *오프라인 게이트 {GATE_DDAYS[dN]}* — {len(gates)}건"]
        for g in sorted(gates, key=lambda x: x["date"]):
            if g["season_gate"]:
                lines.append(f":rotating_light: {g['label']} — {g['date']} (존 전체 교체)")
            else:
                lines.append(f"• [{g['kind']}] {g['label']} — {g['date']}")
        out.append({"owner": OFFLINE_OWNER, "dN": dN, "key": f"imc6:{dN}",
                    "text": f"*[IMC 오프라인 게이트] {OFFLINE_OWNER}*\n" + "\n".join(lines)})
    return out


# ── 온라인 캠페인 스케줄 ('[통합] 26년 무탠다드 프로모션 스케줄' 시트) ──────────
#   ① 월별 SUMMARY 탭("26' N월 SUMMARY") = 매월 다음 달 상세 확정 → 하단 기획전 테이블
#      (브랜드/카테고리/기획전 구분/시작일·종료일ISO/운영 가이드). 탭이 매월 신규 생성 → 자가 확장.
#   ② 연간 '26년 캠페인 스케줄' = 월(R3)×일(R4) 가로 그리드. -2026 전사 백본 + 주요이슈 + CPCMS.
#      월별 SUMMARY 있는 달은 상세가 권위 → 백본은 미확정(월별 SUMMARY 없는) 달만 채움.
ONLINE_SHEET = "13P65W4wjZBkDfoKQ1X1s9Uc2eG84vMH1etw2ewCNHdE"
ONLINE_ANNUAL_TAB = "26년 캠페인 스케줄"
ONLINE_OWNER = "온라인MD"
_ONLINE_MONTH_RE = re.compile(r"26'?\s*(\d{1,2})\s*월\s*SUMMARY")


def _md(d):
    return f"{d.month}/{d.day}" if d else ""


def _online_monthly_tabs(sheets) -> list[tuple[int, str]]:
    """존재하는 '26' N월 SUMMARY' 탭 → [(month, title)] (월 오름차순)."""
    meta = sheets.spreadsheets().get(
        spreadsheetId=ONLINE_SHEET, fields="sheets.properties.title").execute()
    out = []
    for s in meta.get("sheets", []):
        t = str(s["properties"]["title"]).strip()
        m = _ONLINE_MONTH_RE.match(t)
        if m:
            out.append((int(m.group(1)), t))
    return sorted(out)


def load_online_monthly(sheets) -> list[dict]:
    """각 '26' N월 SUMMARY' 하단 기획전 테이블 → 상세 온라인 기획전.
    헤더(카테고리/테마·기획전 구분·시작일·종료일 포함 행)를 동적 감지 후 컬럼 매핑.
    ★탭 전체를 batchGet 1회로 읽어 Sheets 읽기수 절약(429 방지)."""
    tabs = _online_monthly_tabs(sheets)
    if not tabs:
        return []
    try:
        vr = sheets.spreadsheets().values().batchGet(
            spreadsheetId=ONLINE_SHEET, ranges=[f"'{t}'!A1:N130" for _, t in tabs],
            valueRenderOption="FORMATTED_VALUE").execute().get("valueRanges", [])
    except Exception:
        return []
    out = []
    for (mon, _tab), vrng in zip(tabs, vr):
        rows = vrng.get("values", [])
        # 헤더행 = '시작일'·'종료일'·'기획전 구분'·'카테고리/테마' 모두 있는 행.
        hi, cols = None, {}
        for i, r in enumerate(rows):
            cells = [str(c or "").strip() for c in r]
            pos = {}
            for name in ("브랜드 구분", "카테고리/테마", "기획전 구분", "시작일", "종료일", "운영 가이드"):
                for j, c in enumerate(cells):
                    if c == name:
                        pos[name] = j
                        break
            if all(k in pos for k in ("카테고리/테마", "기획전 구분", "시작일", "종료일")):
                hi, cols = i, pos
                break
        if hi is None:
            continue

        def _cell(r, key):
            j = cols.get(key)
            return str(r[j]).strip() if j is not None and j < len(r) and r[j] is not None else ""

        for r in rows[hi + 1:]:
            name = _cell(r, "카테고리/테마")
            sd = _to_date(_cell(r, "시작일"))
            if not name or not sd or sd.year != SEASON_YEAR:
                continue
            ed = _to_date(_cell(r, "종료일"))
            out.append({"date": sd, "end": ed, "brand": _cell(r, "브랜드 구분"),
                        "name": name, "kind": _cell(r, "기획전 구분"),
                        "guide": _cell(r, "운영 가이드"), "month": mon})
    return out


def load_online_annual(sheets, skip_months=()) -> list[dict]:
    """'26년 캠페인 스케줄' 가로 그리드 → 2026 전사/무탠 캠페인 백본.
    월(R3)×일(R4) 컬럼→날짜 매핑. -2026 전사 블록 + 주요이슈 + CPCMS 행만 추출."""
    try:
        rows = sheets.spreadsheets().values().get(
            spreadsheetId=ONLINE_SHEET, range=f"'{ONLINE_ANNUAL_TAB}'!A3:NN60",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    except Exception:
        return []
    if len(rows) < 4:
        return []
    r_month = rows[0]                       # 원래 R3 = 월 라벨행
    mstart = {}                             # 월 시작 컬럼 → month
    for j, c in enumerate(r_month):
        m = re.match(r"(\d{1,2})\s*월", str(c or "").strip())
        if m:
            mstart[j] = int(m.group(1))
    starts = sorted(mstart)

    def col_date(j):
        sc = None
        for s in starts:
            if s <= j:
                sc = s
            else:
                break
        if sc is None:
            return None
        try:
            return datetime.date(SEASON_YEAR, mstart[sc], j - sc + 1)
        except ValueError:
            return None

    def _c(r, j):
        return str(r[j]).strip() if j < len(r) and r[j] is not None else ""

    # 대상 행 = (그룹라벨, row). ① -2026 전사 블록(빅캠페인~무신사스탠다드) ② 주요이슈 ③ CPCMS.
    targets, used = [], set()
    y26 = next((i for i, r in enumerate(rows) if _c(r, 1) == "-2026"), None)
    if y26 is None:   # 폴백: 마지막 '빅캠페인' 행 = 최신년(2026)
        bigs = [i for i, r in enumerate(rows) if _c(r, 2) == "빅캠페인"]
        y26 = (bigs[-1] + 1) if bigs else None
    if y26 is not None:
        for i in range(y26 - 1, y26 + 4):   # 빅캠페인·그외전사·멤버스/브랜드위크·월간·무신사스탠다드
            if 0 <= i < len(rows) and i not in used:
                used.add(i)
                targets.append((_c(rows[i], 2) or "전사 캠페인", rows[i]))
    for i, r in enumerate(rows):
        lab = " ".join(_c(r, k) for k in range(min(3, len(r))))
        if "주요 이슈" in lab and i not in used:
            used.add(i)
            targets.append(("무탠 이슈", r))
        elif "CPCMS" in lab and i not in used:
            used.add(i)
            targets.append(("CPCMS", r))
            if i + 1 < len(rows) and _c(rows[i + 1], 2) == "" and (i + 1) not in used:
                used.add(i + 1)                # 병합 연속행
                targets.append(("CPCMS", rows[i + 1]))

    def _grp(g):
        g = re.sub(r"\s*\(.*\)", "", g).strip()
        return {"그 외 전사캠페인": "전사 캠페인", "멤버스/브랜드위크": "멤버스/브랜드위크",
                "무신사 스탠다드": "무신사 스탠다드"}.get(g, g) or "전사 캠페인"

    best = {}   # (그룹, 정규화명, 월) → 최초일 (같은 캠페인 병합셀/반복 dedup)
    for group, r in targets:
        g = _grp(group)
        for j in range(3, len(r)):
            s = _c(r, j)
            if not s or s == "0":
                continue
            d = col_date(j)
            if d is None or d.month in skip_months:
                continue
            name = re.split(r"[(（]", s)[0].strip()
            if len(name) < 2:
                continue
            key = (g, name.replace(" ", ""), d.month)
            if key not in best or d < best[key][0]:
                best[key] = (d, name, g)
    return [{"date": d, "name": name, "kind": g} for (d, name, g) in best.values()]


def load_online(sheets) -> list[dict]:
    """온라인 캠페인 스케줄 = 월별 상세(권위) + 연간 백본(월별 없는 달만).
    반환 [{name, sub, date, end, approx, guide}] — 생성기가 IMC '온라인' 타입으로 주입."""
    monthly = load_online_monthly(sheets)
    covered = {it["month"] for it in monthly}
    annual = load_online_annual(sheets, skip_months=covered)
    out = []
    for it in monthly:
        sd, ed = it["date"], it.get("end")
        rng = _md(sd) + (f"~{_md(ed)}" if ed and ed != sd else "")
        parts = [p for p in (it.get("brand"), it.get("kind")) if p]
        sub = " · ".join(parts) + (f" · {rng}" if rng else "")
        out.append({"name": it["name"], "sub": sub, "date": sd, "end": ed,
                    "approx": False, "guide": it.get("guide", "")})
    for it in annual:
        out.append({"name": it["name"], "sub": f"전사 · {it['kind']}", "date": it["date"],
                    "end": None, "approx": True, "guide": ""})
    return out


# ── IMC-7: 발매이슈(D-4 공유) + 일반기획전(작성 D-1) 역산 ─────────────────────
ISSUE_TAB = "발매 이슈"
ISSUE_LEADS = {"맨": "유다휘", "우먼": "전혜미", "키즈": "이지현", "전체": "김민수"}
ISSUE_DDAYS = {4: "공유 마감 D-4", 1: "진행 D-1", 0: "진행 D-DAY"}
PROMO_TAB = "26년 프로모션 경로"
PROMO_DDAYS = {1: "작성 마감 D-1", 0: "시작 D-DAY"}


def _any_date(v) -> datetime.date | None:
    s = str(v or "").strip()
    try:
        return datetime.date.fromisoformat(s[:10])        # "2026-09-12"
    except ValueError:
        return _md_to_date(s)                              # "9/12"


def load_release_issues(sheets) -> list[dict]:
    """발매 이슈 → [{issue, brand, owner, when}]. 진행일자 기준, 담당=브랜드 키워드."""
    res = sheets.spreadsheets().values().get(
        spreadsheetId=CAMPAIGN_SHEET, range=f"'{ISSUE_TAB}'!A8:L500",
        valueRenderOption="FORMATTED_VALUE").execute()
    out = []
    for r in res.get("values", []):
        def c(i): return str(r[i]).strip() if i < len(r) and r[i] is not None else ""
        issue, brand, when = c(2), c(3), _any_date(c(7))   # C이슈, D브랜드, H진행일자
        if not issue or not when:
            continue
        owner = next((v for k, v in ISSUE_LEADS.items() if k in brand), "온라인MD")
        out.append({"issue": issue, "brand": brand, "owner": owner, "when": when})
    return out


def compute_issue_alarms(issues, as_of: datetime.date):
    groups: dict[tuple, list] = defaultdict(list)
    for it in issues:
        dN = (it["when"] - as_of).days
        if dN in ISSUE_DDAYS:
            groups[(it["owner"], dN)].append(it)
    return groups


def format_issue(groups):
    out = []
    for (owner, dN), its in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        lines = [f":truck: *발매이슈 {ISSUE_DDAYS[dN]}* — {len(its)}건"]
        for it in sorted(its, key=lambda x: x["when"]):
            lines.append(f"• {it['issue']} ({it['brand']}) — 진행 {it['when']}")
        out.append({"owner": owner, "dN": dN, "key": f"imc7i:{owner}:{dN}",
                    "text": f"*[IMC 발매이슈 알람] {owner}님*\n" + "\n".join(lines)})
    return out


def load_general_promos(sheets) -> list[dict]:
    """26년 프로모션 경로 → [{title, owner, start}]. 형태=일반 기획전 + 시작일·제목 有."""
    res = sheets.spreadsheets().values().get(
        spreadsheetId=CAMPAIGN_SHEET, range=f"'{PROMO_TAB}'!A8:T400",
        valueRenderOption="FORMATTED_VALUE").execute()
    out = []
    for r in res.get("values", []):
        def c(i): return str(r[i]).strip() if i < len(r) and r[i] is not None else ""
        if "일반 기획전" not in c(6):           # G형태
            continue
        start = _md_to_date(c(7))               # H시작일
        title = c(12)                            # M제목
        if not start or not title:
            continue
        owner = _kor_name(c(17).split(",")[0]) or "온라인MD"   # R담당자(첫 명)
        out.append({"title": title.splitlines()[0][:30], "owner": owner, "start": start})
    return out


def compute_promo_alarms(promos, as_of: datetime.date):
    groups: dict[tuple, list] = defaultdict(list)
    for p in promos:
        dN = (p["start"] - as_of).days
        if dN in PROMO_DDAYS:
            groups[(p["owner"], dN)].append(p)
    return groups


def format_promo(groups):
    out = []
    for (owner, dN), ps in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        lines = [f":calendar: *일반기획전 {PROMO_DDAYS[dN]}* — {len(ps)}건"]
        for p in sorted(ps, key=lambda x: x["start"]):
            lines.append(f"• {p['title']} — 시작 {p['start']}")
        out.append({"owner": owner, "dN": dN, "key": f"imc7p:{owner}:{dN}",
                    "text": f"*[IMC 일반기획전 알람] {owner}님*\n" + "\n".join(lines)})
    return out


def _imc_route(owner: str) -> tuple[str, str]:
    """IMC 온라인 담당 라우팅 (앞단 MD/디자인/소싱 팀장 폴백과 무관).
    담당자매핑에 온라인 담당 Slack ID 있으면 그쪽, 없으면 본인 DM(테스트) 폴백."""
    sid = T.OWNER_SLACK_IDS.get(owner)
    if T.TEST_ONLY:
        return T._test_target(), f"{owner}(온라인){'·매핑됨' if sid else '·미매핑'}"
    if sid:
        return sid, owner
    return T._test_target(), f"{owner}(온라인) 미매핑→본인DM"


def _utf8():
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


def main() -> int:
    _utf8()
    do_send = "--send" in sys.argv
    asof_arg = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--asof=")), None)
    as_of = datetime.date.fromisoformat(asof_arg) if asof_arg else datetime.date.today()

    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sheets = svc["sheets"]
    T.load_owner_map(sheets)        # 온라인 담당 Slack ID(담당자매핑에 있으면) 로드

    releases = load_releases(sheets)
    msgs3 = format_imc(compute_imc(releases, as_of))           # IMC-3 발매 노출
    camps = load_campaigns(sheets)
    msgs4 = format_campaign(compute_campaign_alarms(camps, as_of))  # IMC-4 캠페인 역산
    gates = load_offline_gates(sheets)
    msgs6 = format_offline(compute_offline_alarms(gates, as_of))    # IMC-6 오프라인 게이트(+명절/매장오픈)
    issues = load_release_issues(sheets)
    msgs7i = format_issue(compute_issue_alarms(issues, as_of))      # IMC-7 발매이슈 D-4
    promos = load_general_promos(sheets)
    msgs7p = format_promo(compute_promo_alarms(promos, as_of))      # IMC-7 일반기획전 D-1
    msgs = msgs3 + msgs4 + msgs6 + msgs7i + msgs7p
    print(f"기준일 {as_of} · 발매 {len(releases)}/노출 {len(msgs3)} · 캠페인 {len(camps)}/역산 {len(msgs4)} · "
          f"오프라인게이트 {len(gates)}/{len(msgs6)} · 발매이슈 {len(issues)}/{len(msgs7i)} · 일반기획전 {len(promos)}/{len(msgs7p)} · TEST_ONLY={T.TEST_ONLY}")
    for m in msgs:
        print("\n" + "─" * 50)
        _, lbl = _imc_route(m["owner"])
        print(f"[라우팅 → {lbl}]")
        print(m["text"])

    if do_send:
        from soo import persona
        bot_token = T._slack_token()
        if not bot_token:
            print("⚠️ Slack 토큰 없음 — 발송 스킵")
            return 0
        log = persona.setup_logger(ROOT / "logs", dry_run=False)
        sent_keys = T.load_sent_today(sheets, as_of)
        sent = 0
        for m in msgs:
            key = m["key"]
            if key in sent_keys:
                print(f"  {key}: 오늘 이미 발송 — 스킵(dedup)")
                continue
            target, labels = _imc_route(m["owner"])
            dest = "히어로봇 채널" if T.TEST_CHANNEL else "본인 DM"
            prefix = f":test_tube: *[테스트 → {dest} | 원래 수신: {labels}]*\n" if T.TEST_ONLY else ""
            ts = persona.send_slack(prefix + m["text"], bot_token=bot_token,
                                    target=target, persona=persona.RANKING_BOT, log=log)
            print(f"  {key}: {labels} — {'OK' if ts else '실패'}")
            if ts:
                T.record_sent(sheets, as_of, key, labels)
                sent_keys.add(key)
                sent += 1
        print(f"\n[발송] {sent}건")
    else:
        print("\n(dry-run — 발송하려면 --send)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
