"""입고-발매 알림 수신 라우팅(사용자 지시 2026-11-17).

알림마다 받는 팀이 다르다 — 사람별로 **자기 건만** DM 한다(팀 단위 수신자는 그 알림 전부).

  알림                받는 팀
  발매일 변경          기획 · 소싱 · 콘텐츠마케팅 · 온라인실
  촬샘일 변경          기획 · 소싱 · 콘텐츠마케팅
  촬샘 미입고(D+1)     기획 · 소싱 · 콘텐츠마케팅 · 온라인실
  촬샘 D-7 독촉        기획 · 소싱 · 콘텐츠마케팅 · 온라인실
  (포토·오프라인실은 뺐다 — 사용자: "콘텐츠마케팅이 소통", "오프라인은 ASN 만")

팀원 찾기
  기획   = PLM `데이터` md_nm(스타일별) → `담당자매핑` 이름 → Slack ID
  소싱   = 작업의뢰 발매1 '소싱담당'(정본, 사용자 11/13) · 빈칸이면 PLM sc_nm
  온라인실 = `담당자매핑` role '온라인MD' 중 category = 발매1 '구분'(남성·여성·키즈)
  ID 없음  = `담당자매핑` 에 없는 이름은 `_OPS_TEAM` role 'id' 행(알림 전용 — ASN 엔 안 걸린다)
  콘텐츠마케팅 = 팀 전원 — `_OPS_TEAM` 명단(사용자 1차 명단 9명 시드). 하루 한 번 슬랙 표시 이름의 소속
         ('무신사스탠다드마케팅/콘텐츠마케팅', @musinsa.com)으로 새로 들어온 사람을 더하고, 슬랙에서 사라진 사람은 '이동/퇴사'로.
         사람이 '제외'로 바꾼 행은 건드리지 않는다(이인직 = 1차 명단에 없어 '제외'로 시드).

★ALERT_LIVE=False(지금) — 사용자 지시 "내가 담당자들한테 보내라고 하기 전엔 보내면 안 돼".
  발송은 수영님 DM 한 통뿐이고 줄마다 '→ 받을 사람'을 붙인다. 원장엔 'TEST …' 로 적는다.
  True 로 바꾸면 그 뒤에 감지된 건부터 담당자에게 나간다 — TEST 로 이미 나간 변경·미입고 건은 다시 보내지 않는다
  (밀린 것을 한꺼번에 풀지 않는다 [[CLAUDE 1-3]]). D-7 독촉만 기존 규칙대로 아직 예정일 전·미입고인 TEST 건을 다시 보낸다.
"""
from __future__ import annotations

import collections
import datetime as _dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ALERT_LIVE = False
STRATEGY_DM = ["U09BU1F85TR"]          # 수영님 — 전체 사본 · 시험본 · 담당자 미매핑 건
ROUTES = {
    "launch_chg": ("md", "content", "online", "offline"),   # 소싱 제외·오프라인실(상품컨트롤) 포함(사용자 11/17)
    "shoot_chg": ("md", "content"),                   # 소싱 제외 — 촬샘일을 바꾸는 주체(사용자 11/17)
    "shoot_miss": ("md", "sc", "content", "online"),
    "shoot_pre": ("md", "sc", "content", "online"),
}
ROLE_KO = {"md": "기획", "sc": "소싱", "content": "콘텐츠마케팅", "online": "온라인", "offline": "오프라인"}
ONLINE_LINES = ("남성", "여성", "키즈")   # 발매1 '구분' 그대로 = 담당자매핑 온라인MD category. 제품·언더웨어·신발은 미정(사용자 확인 대기)

TEAM_TAB = "_OPS_TEAM"
TEAM_HEADER = ["role", "name", "slack_id", "status", "src", "updated"]
TEAM_ORGS = {"content": "무신사스탠다드마케팅/콘텐츠마케팅"}
TEAM_DOMAIN = "@musinsa.com"            # 파견(musinsaptnr) 제외 — 사용자 1차 명단 기준
CONTENT_SEED = [   # 사용자 1차 명단 2026-11-17
    ("조예나", "U02MHMT6MGD"), ("정도영", "U06R2EJH10Q"), ("김초은", "U07HH20L00Y"), ("성다은", "U0AG33Z01HV"),
    ("최수영", "U0AM2L50211"), ("김린", "U0BHD8NMRKL"), ("김선재", "U0BHH6QJFD2"), ("양도원", "U0BPNE6G39Q"),
    ("김은애", "U0C6VD5LFFH")]
CONTENT_SEED_OUT = [("이인직", "U02AZR9PNTF")]   # 같은 소속이지만 1차 명단에 없음 → '제외'
# 알림 전용 이름 → Slack ID(role 'id'). `담당자매핑` 에 넣으면 **ASN 알림까지 받기 시작**하므로 여기 따로 둔다(2026-11-17).
#   작업의뢰 소싱담당·PLM 기획MD 에 나오는데 담당자매핑에 ID 가 없던 사람 + 사용자가 준 소싱 명단 중 빠진 사람.
# PLM 담당자 이름 오기 → 실제 사람(사용자 확인 2026-11-17: 27SS 우먼즈 쿨 터치 팬츠 2건 '황수현' = 황서영 담당)
NAME_FIX = {"황수현": "황서영"}
ID_SEED = [
    ("문환웅", "U0AP5GE5R55"), ("주인아", "U0C0EMBVBD4"), ("엄기훈", "U06H5CESNA3"), ("장인영", "U08QSUR31U7"),
    ("김재희", "U0CAUUYRSBC"), ("권경민", "U0CEU2VCCAY"),                      # 소싱
    ("김세현", "U0B9F182X9C"), ("황수정", "U0BCV28NVAQ"), ("구새봄", "U0C1QG2N1L7"),    # 기획MD (PLM '황수현'은 슬랙에 없음 — 매핑 안 함)
    ("전혜미", "U07L63PNQBB")]   # 온라인MD 여성(담당자매핑엔 ID 빈칸 — ASN 은 그대로, 사용자 지시 2026-11-17)


def _names(v) -> list[str]:
    from soo.hero_ops.asn_notify import _names as n
    return n(v)


class Router:
    """스타일 → 팀별 받을 사람. 시트 읽기는 생성 때 한 번."""

    def __init__(self, sheets, app_sheet_id: str):
        from soo.hero_ops import asn_notify, triggers as T
        T.load_owner_map(sheets, app_sheet_id)
        self._sid = asn_notify._sid_of
        self.owners = asn_notify.load_owners(sheets)
        self.team: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        self.ids: dict[str, str] = {}
        for r in read_team(sheets, app_sheet_id):
            if r["status"] == "포함" and r["slack_id"]:
                if r["role"] == "id":
                    self.ids[r["name"]] = r["slack_id"]
                else:
                    self.team[r["role"]].append((r["name"], r["slack_id"]))
        rows = sheets.spreadsheets().values().get(spreadsheetId=app_sheet_id, range="담당자매핑!A2:E").execute().get("values", [])
        self.online: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        for r in rows:
            r = [str(x).strip() for x in r] + [""] * 5
            sid = r[4] or self.ids.get(r[0], "")       # 매핑 ID 빈칸이면 알림 전용 ID(전혜미)
            if r[1] == "온라인MD" and r[2] == "담당자" and sid:
                self.online[r[3]].append((r[0], sid))
        print(f"[route] 온라인MD {sum(map(len, self.online.values()))}명({', '.join(f'{k} {len(v)}' for k, v in self.online.items())})"
              f" · 콘텐츠마케팅 {len(self.team['content'])}명")

    def who(self, route: str, style: str, line: str, sourcing: str) -> list[tuple[str, str, str]]:
        """[(팀, 이름, slack_id)] — slack_id 빈칸 = 미매핑(이름은 있는데 ID 없음 / 이름 자체가 없음은 이름 '')."""
        out: list[tuple[str, str, str]] = []
        o = self.owners.get(style, {})
        for role in ROUTES[route]:
            if role == "md":
                ns = list(dict.fromkeys(NAME_FIX.get(n, n) for n in _names(o.get("md"))))
            elif role == "sc":
                ns = _names(sourcing) or _names(o.get("sc"))
            elif role == "offline":      # 오프라인실 = ASN 과 같은 상품컨트롤팀 배정(품번 앞 2자, ME 없음)
                from soo.hero_ops.asn_notify import CTRL_LEADS
                ns = list(CTRL_LEADS.get(str(style)[:2], ()))
                out += [(role, n, self._sid(n) or self.ids.get(n, "")) for n in ns] or [(role, "", "")]
                continue
            elif role == "online":
                ppl = self.online.get(line, []) if line in ONLINE_LINES else []
                out += [(role, n, s) for n, s in ppl] or [(role, "", "")]
                continue
            else:
                out += [(role, n, s) for n, s in self.team.get(role, [])] or [(role, "", "")]
                continue
            out += [(role, n, self._sid(n) or self.ids.get(n, "")) for n in ns] or [(role, "", "")]
        return out


def who_text(who: list[tuple[str, str, str]]) -> str:
    """시험본 꼬리 '(→ 기획 박지수 · 소싱 서은진 · 온라인 유다휘, 정채은 · 콘텐츠마케팅 9명)'. ID 없는 이름엔 '(ID 없음)'."""
    by: dict[str, list[str]] = collections.OrderedDict()
    for role, n, s in who:
        by.setdefault(role, [])
        if n:
            by[role].append(n + ("" if s else "(ID 없음)"))
    parts = []
    for role, ns in by.items():
        if role == "content":
            parts.append(f"{ROLE_KO[role]} {len(ns)}명" if ns else f"{ROLE_KO[role]} -")
        else:
            parts.append(f"{ROLE_KO[role]} {', '.join(ns) or '-'}")
    return " (→ " + " · ".join(parts) + ")"


def deliver(rows: list[list], route: str, ctx, router: Router | None, fmt, send, label: str) -> set[int]:
    """원장 행들을 라우팅해 보낸다. 보낸(=한 명 이상에게 도달한) 행의 index 집합.

    rows  = [(i, row)] · ctx(row) → (style, line, sourcing) · fmt(rows, who=None|[str]) → 본문 · send(msg, label, targets) → 성공 수
    TEST  = 수영님 한 통(줄마다 받을 사람). LIVE = 사람별 자기 건 + 수영님 전체 사본 + 미매핑 건은 수영님에게 따로."""
    if not rows:
        return set()
    whos = [router.who(route, *ctx(r)) if router else [] for _, r in rows]
    if not ALERT_LIVE:
        msg = fmt([r for _, r in rows])      # 받을 사람 꼬리 안 붙임(사용자 11/17: "누구한테 갔는지는 굳이 안 적어도 돼")
        return {i for i, _ in rows} if send(msg, label + "[TEST]", STRATEGY_DM) else set()
    by: dict[str, list[int]] = collections.defaultdict(list)
    unmapped: list[int] = []
    for k, w in enumerate(whos):
        for _, _, s in w:
            if s and k not in by[s]:
                by[s].append(k)
        if any(not s for _, _, s in w):
            unmapped.append(k)
    sent: set[int] = set()
    for sid, ks in by.items():
        if sid in STRATEGY_DM:
            continue
        if send(fmt([rows[k][1] for k in ks]), label, [sid]):
            sent |= {rows[k][0] for k in ks}
    # 수영님 = 전체 사본(받을 사람 표시) — 담당자 0명이 나간 건도 여기서 보인다
    copy = fmt([r for _, r in rows])
    if send(copy.replace("[시험] ", ""), label + "[사본]", STRATEGY_DM):
        sent |= {i for i, _ in rows}         # 담당자 미매핑 건도 수영님이 받았으면 '보냄'(다음 실행에 또 나가지 않게)
    if unmapped:
        print(f"[route] {label} — 담당자 Slack ID 없는 팀이 낀 {len(unmapped)}건(사본에 표시)")
    return sent


# ── 팀 명단(_OPS_TEAM) ─────────────────────────────────────────────────────────────
def read_team(sheets, app_sheet_id: str) -> list[dict]:
    meta = sheets.spreadsheets().get(spreadsheetId=app_sheet_id, fields="sheets.properties(title)").execute()
    if not any(x["properties"]["title"] == TEAM_TAB for x in meta["sheets"]):
        return ([{"role": "content", "name": n, "slack_id": s, "status": "포함", "src": "1차 명단", "updated": ""} for n, s in CONTENT_SEED]
                + [{"role": "id", "name": n, "slack_id": s, "status": "포함", "src": "알림 전용 ID", "updated": ""} for n, s in ID_SEED])
    vals = sheets.spreadsheets().values().get(spreadsheetId=app_sheet_id, range=f"'{TEAM_TAB}'!A2:F").execute().get("values", [])
    return [dict(zip(TEAM_HEADER, [str(x).strip() for x in r] + [""] * 6)) for r in vals if r and r[0]]


def _slack_users(tok: str, budget_s: int = 240) -> list[dict] | None:
    """users.list 전부(28k명·28회·약 70초). 429 는 Retry-After 만큼 기다린다. 예산 초과·실패 = None(명단 갱신 안 함)."""
    t0, cur, out = time.time(), "", []
    while True:
        q = urllib.parse.urlencode({"limit": 1000, **({"cursor": cur} if cur else {})})
        req = urllib.request.Request("https://slack.com/api/users.list?" + q, headers={"Authorization": "Bearer " + tok})
        for _ in range(8):
            try:
                d = json.load(urllib.request.urlopen(req, timeout=60))
                break
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    print(f"[route] users.list HTTP {e.code}")
                    return None
                time.sleep(int(e.headers.get("Retry-After", 20)) + 1)
        else:
            return None
        if not d.get("ok"):
            print(f"[route] users.list 실패: {d.get('error')}")
            return None
        out += d.get("members", [])
        cur = (d.get("response_metadata") or {}).get("next_cursor") or ""
        if not cur:
            return out
        if time.time() - t0 > budget_s:
            print(f"[route] users.list 예산 {budget_s}s 초과({len(out):,}명에서 중단) — 명단 갱신 건너뜀")
            return None


def team_diff(roster: list[dict], members: list[dict], today: str) -> tuple[list[dict], list[str]]:
    """(새 명단, 바뀐 것 설명). 사람이 '제외'로 둔 행은 그대로. 슬랙 소속에 새로 잡힌 사람 = '포함'(슬랙 자동),
    명단의 '포함' 중 슬랙 소속에서 사라진 사람 = '이동/퇴사'. 소속이 하나도 안 잡히면(표기 개편 등) 아무것도 안 바꾼다."""
    found: dict[str, dict[str, str]] = collections.defaultdict(dict)   # role → sid → 이름
    for u in members:
        if u.get("deleted") or u.get("is_bot"):
            continue
        p = u.get("profile") or {}
        label = " ".join([p.get("real_name", ""), p.get("display_name", "")])
        if not str(p.get("email", "")).lower().endswith(TEAM_DOMAIN):
            continue
        for role, org in TEAM_ORGS.items():
            if org in label:
                dn = p.get("display_name") or p.get("real_name") or ""
                found[role][u["id"]] = dn.split("(")[0].strip() or dn
    new, notes = [dict(r) for r in roster], []
    for role in TEAM_ORGS:
        if not found[role]:
            notes.append(f"⚠ {ROLE_KO[role]} 소속으로 잡힌 사람 0명 — 슬랙 표기가 바뀌었을 수 있어 명단을 그대로 둔다")
            continue
        have = {r["slack_id"]: r for r in new if r["role"] == role}
        for sid, nm in found[role].items():
            if sid not in have:
                new.append({"role": role, "name": nm, "slack_id": sid, "status": "포함", "src": "슬랙 자동", "updated": today})
                notes.append(f"+ {ROLE_KO[role]} {nm}")
        for sid, r in have.items():
            if r["status"] == "포함" and sid not in found[role]:
                r.update(status="이동/퇴사", updated=today)
                notes.append(f"- {ROLE_KO[role]} {r['name']}(슬랙 소속에서 빠짐)")
    return new, notes


def sync_team(sheets, app_sheet_id: str, now: _dt.datetime, apply: bool) -> None:
    """명단 탭이 없으면 1차 명단으로 만들고, 하루 한 번 슬랙 소속으로 갱신. 토큰 없으면(로컬) 시드만."""
    today = now.date().isoformat()
    meta = sheets.spreadsheets().get(spreadsheetId=app_sheet_id, fields="sheets.properties(title)").execute()
    exists = any(x["properties"]["title"] == TEAM_TAB for x in meta["sheets"])
    roster = read_team(sheets, app_sheet_id)
    if not exists:
        roster = roster + [{"role": "content", "name": n, "slack_id": s, "status": "제외", "src": "1차 명단에 없음", "updated": ""}
                           for n, s in CONTENT_SEED_OUT]
        for r in roster:
            r["updated"] = today
    elif any(r["updated"] == today and r["src"] == "확인" for r in roster):
        return
    tok = os.environ.get("SLACK_MDP_BOT_TOKEN", "").strip()
    notes: list[str] = []
    if tok:
        members = _slack_users(tok)
        if members is not None:
            roster, notes = team_diff(roster, members, today)
            roster = [r for r in roster if r["src"] != "확인"] + [{"role": "_", "name": "슬랙 소속 확인", "slack_id": "",
                                                                 "status": "", "src": "확인", "updated": today}]
    print(f"[route] 팀 명단 {sum(r['status'] == '포함' for r in roster)}명 포함" + (" · " + " · ".join(notes) if notes else ""))
    if not apply or (exists and not notes and not tok):
        return
    if not exists:
        sheets.spreadsheets().batchUpdate(spreadsheetId=app_sheet_id, body={"requests": [{"addSheet": {"properties": {
            "title": TEAM_TAB, "hidden": True, "gridProperties": {"rowCount": 300, "columnCount": len(TEAM_HEADER)}}}}]}).execute()
    sheets.spreadsheets().values().clear(spreadsheetId=app_sheet_id, range=f"'{TEAM_TAB}'!A1:F", body={}).execute()
    sheets.spreadsheets().values().update(spreadsheetId=app_sheet_id, range=f"'{TEAM_TAB}'!A1", valueInputOption="RAW", body={
        "values": [TEAM_HEADER] + [[r[h] for h in TEAM_HEADER] for r in roster]}).execute()
