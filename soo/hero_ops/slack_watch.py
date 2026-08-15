# -*- coding: utf-8 -*-
"""슬랙에서만 도는 히어로 IMC·발매 일정 변경을 잡아 **제안 큐**로 올린다.

발의(2026-08-13, 사용자): "파일에 기록 안 됐어도 슬랙에서 상시 논의로 바뀌는 경우가 있다."

## 설계 원칙 (바꾸기 전에 읽을 것)

1. **자동 반영 금지 — 제안 큐까지만.** IMC·발매의 진실소스는 시트다. 슬랙이 두 번째 소스가 되면
   담당자가 시트를 고쳐도 조용히 덮인다. 여기서는 DM 요약 + 원장 기록만 하고, 반영은 사람이 한다.

2. **감시 대상 = 봇이 초대된 채널.** 채널 목록을 코드에 박지 않는다(2026-08-15 사용자 결정).
   "히어로라고 적힌 채널 말고도 히어로를 말하는 데가 많다" — 초대/나가기가 그대로 설정이 되게 한다.
   ★전사 검색은 금지다. '히어로'가 UI 히어로 배너·티몰 히어로 validation 같은 전혀 다른 뜻으로도
   쓰여 노이즈가 절반을 넘는다(2026-08-13 실측). 초대된 채널로 좁히는 게 그 필터 역할을 한다.

3. **판정은 규칙 기반.** CI에 `claude` CLI 가 없어 LLM 분류를 못 쓴다(트렌드 봇 때 확인).
   제안 큐라 오탐 비용이 낮으므로 느슨하게 거르고, 애매한 건 우선순위를 낮춰 **남긴다**.
   ★거르되 드롭하지 않는다 — 비히어로 일정을 파이프라인에서 영구 드롭했다가 "5월부터 마케팅이
   멈췄나?"는 착시를 만든 전례가 있다.

4. **히어로 별칭은 앱의 `HERO_LINEUP` 을 그대로 읽는다.** 여기서 목록을 새로 만들면 진실소스가
   둘이 된다. 못 읽으면 추측하지 말고 **중단**한다.

## ★스레드 답글 (2026-08-15 추가 — 여기가 알맹이다)
실측: 14일간 최상위 479건 vs **답글 706건(147%)**. `#무탠본부-히어로-pj` 는 최상위 55 : 답글 237
(4.3배)로, 최상위만 보면 논의의 대부분을 놓친다.

★**커서만으로는 답글을 못 잡는다** — `conversations.history` 는 답글이 달려도 부모를 최신으로
끌어올려 주지 않는다. 커서 뒤의 부모에 오늘 답글이 달리면 영영 안 보인다.
→ 부모는 `THREAD_LOOKBACK_DAYS` 만큼 **소급 스캔**하고, `latest_reply > 커서` 인 스레드만
`conversations.replies(oldest=커서)` 로 새 답글을 가져온다. 워터마크는 답글 ts 까지 포함해 올린다.
(소급 범위보다 오래된 글에 달리는 답글은 못 잡는다 — 상수를 늘리면 되지만 매 실행 스캔량도 는다.)

## 판정 규칙 (2026-08-15, 14일 416건 실측으로 교정)

후보 = 히어로 신호 AND (날짜 표현 OR 일정 변경 어휘)

- ★`.` 구분자를 빠뜨리면 진짜배기를 놓친다 — "발매 : 9.9(수) 입고 : 9.15일 and 29일" 이 통째로
  탈락했다. `/` 와 `월 N일` 만으로는 부족하다.
- ★날짜만으로도 부족하다 — "[리커버리 & 힛탠다드] 주요 일정 변경 / 변경 사유 : …" 처럼 **제목에
  일정 변경이라고 대놓고 적혔는데 날짜가 없는** 메시지가 있다. 일정 어휘를 OR 로 둔다.
- ★`26/08/13_27SS 1st QC` 의 앞머리는 날짜가 아니라 **문서 발행일**이다. 연도 접두는 날짜로 세지 않는다.
- ★`.` 구분자에 공백을 허용하면 목록 번호가 날짜가 된다("1. 26FW 운영간은" → 1.26).
  월/일 범위(1~12, 1~31)도 검사한다 — 안 하면 비율 "80/20" 이 날짜로 둔갑한다.
- 정형 진행 로그(수납샘플 제출알림·Initial PO 발행·1st QC 투입)는 IMC 일정이 아니고 히어로 매칭도
  상품명 우연 일치다 → 우선순위 '낮음'으로 접어두되 큐에는 남긴다.

## 실행
    python -m soo.hero_ops.slack_watch            # 드라이런(기본) — 발송·기록 안 함
    python -m soo.hero_ops.slack_watch --send     # DM 발송 + 원장 기록
    python -m soo.hero_ops.slack_watch --days 14  # 커서 무시하고 N일 소급

필요 스코프: channels:history, channels:read, groups:history, groups:read (2026-08-15 부여됨).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

SLACK_API = "https://slack.com/api/"

# 원장 — ops_watch 와 같은 파일(랭킹봇 아카이브)에 둔다.
ARCHIVE_SHEET = "1UqH-pi5YuQrVYpfj74khXSTVbWml1m4YuaKUAnUgzPI"
CURSOR_TAB = "_슬랙커서"
CURSOR_HEADER = ["채널ID", "채널명", "마지막ts", "갱신시각"]
QUEUE_TAB = "_슬랙제안"
QUEUE_HEADER = ["발견일", "채널", "ts", "우선순위", "히어로", "날짜표현", "요약", "링크",
                "원글(스레드)", "첨부링크"]

FIRST_RUN_DAYS = 7          # 커서가 없는 채널의 첫 수집 범위. 14일이면 첫 발송이 너무 시끄럽다.
THREAD_LOOKBACK_DAYS = 30   # ★부모 소급 범위 — 이보다 오래된 글에 달린 답글은 못 잡는다(아래 주석)
MAX_PER_CALL = 200

# ── 판정 사전 ────────────────────────────────────────────────────────────────
# 날짜 표현. ★연도 접두(26/08/13)는 제외 — 문서 발행일이지 일정이 아니다.
#   ★`.` 구분자는 공백을 허용하지 않는다 — "1. 26FW 운영간은…" 의 목록 번호가 9.9 처럼 잡힌다.
#   ★월/일 범위를 검사한다 — "80/20"(비율) 같은 게 날짜로 둔갑한다. 둘 다 2026-08-15 실측 오탐.
_MD_RE = re.compile(r"(?<![\d/.])(?<!\d[/.])(\d{1,2})\s*(?:/\s*|\.)(\d{1,2})(?![\d.]*\s*[/.]\s*\d)")
_KDATE_RE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일"                    # 8월 20일
                       r"|\d{1,2}\s*월\s*(?:초|중순|중|말|첫째|둘째|셋째|넷째)"  # 9월 말
                       r"|\d{1,2}\s*주\s*차")                             # 3주차


def find_date(text: str) -> str:
    """날짜로 읽히는 첫 표현. 없으면 빈 문자열."""
    m = _KDATE_RE.search(text)
    if m:
        return m.group(0).strip()
    for m in _MD_RE.finditer(text):
        mo, da = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= da <= 31:
            return m.group(0).strip()
    return ""

# 일정이 '바뀐다'는 신호. 날짜가 없어도 이건 봐야 한다.
_SCHED_RE = re.compile(
    r"일정\s*[^\n]{0,6}?(변경|변동|조정|확정|연기|취소|sync|싱크)"
    r"|발매\s*(일정|일자|일)?\s*(변경|연기|당[겨김]|앞당)"
    r"|(입고|발매|오픈|론칭|런칭)\s*(일정)?\s*(변경|연기|조정|확정)"
    r"|리드\s*타임\s*변경",
    re.IGNORECASE)

# ★'일정'이 붙었다고 다 IMC 일정은 아니다 — WBR·위클리·미팅 취소/조정이 '높음'을 희석한다
#   (2026-08-15 실측: 높음 10건 중 3건). 상품 일정 어휘가 하나도 없으면 '보통'으로 내린다.
_MEETING_RE = re.compile(r"WBR|위클리|미팅|회의|보고\s*(드|만|일정)|아젠다|인비", re.IGNORECASE)
_PRODUCT_RE = re.compile(r"발매|입고|기획전|캠페인|쇼케이스|론칭|런칭|오픈|선발매|예약\s*판매"
                         r"|PPL|프로모션|쿠폰|물량|컬러\s*추가")

# 정형 진행 로그 — 우선순위 낮춤(제외 아님).
_ROUTINE_RE = re.compile(
    r"수납\s*샘플|제출\s*알림|Initial\s*PO|INITIAL\s*PO|1st\s*QC|1ST\s*QC"
    r"|QC\s*투입|큐씨\s*투입|PROTO\s*투입|SR\s*발행|APP\s*진행|위클리플랜",
    re.IGNORECASE)
# 메시지 첫머리의 YY/MM/DD·YY.MM.DD 접두 = 문서 발행일
_DOCDATE_RE = re.compile(r"^\W{0,6}\d{2}\s*[/.]\s*\d{1,2}\s*[/.]\s*\d{1,2}")


def kst_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=9)))


# ── Slack ────────────────────────────────────────────────────────────────────
def _slack(method: str, token: str, **params):
    url = SLACK_API + method + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def bot_channels(token: str) -> list[dict]:
    """봇이 멤버인 공개·비공개 채널 전부. ★이 목록이 곧 감시 범위다(코드에 박지 않는다)."""
    out, cur = [], ""
    while True:
        r = _slack("users.conversations", token, types="public_channel,private_channel",
                   exclude_archived="true", limit=200, cursor=cur)
        if not r.get("ok"):
            raise RuntimeError(f"users.conversations 실패: {r.get('error')} "
                               f"(needed={r.get('needed', '-')})")
        out += r.get("channels", [])
        cur = (r.get("response_metadata") or {}).get("next_cursor") or ""
        if not cur:
            return out
        time.sleep(1.0)


def channel_history(token: str, cid: str, oldest: str) -> list[dict]:
    msgs, cur = [], ""
    while True:
        p = dict(channel=cid, limit=MAX_PER_CALL)
        if oldest:
            p["oldest"] = oldest
        if cur:
            p["cursor"] = cur
        r = _slack("conversations.history", token, **p)
        if not r.get("ok"):
            print(f"  [주의] #{cid} 읽기 실패: {r.get('error')}")
            return msgs
        msgs += r.get("messages", [])
        if not r.get("has_more"):
            return msgs
        cur = (r.get("response_metadata") or {}).get("next_cursor") or ""
        if not cur:
            return msgs
        time.sleep(1.2)


def thread_replies(token: str, cid: str, parent_ts: str, oldest: str) -> list[dict]:
    """스레드 답글(부모 제외). oldest 로 이미 본 것은 서버에서 잘라 받는다."""
    out, cur = [], ""
    while True:
        p = dict(channel=cid, ts=parent_ts, limit=MAX_PER_CALL)
        if oldest:
            p["oldest"] = oldest
        if cur:
            p["cursor"] = cur
        r = _slack("conversations.replies", token, **p)
        if not r.get("ok"):
            print(f"  [주의] 스레드 {parent_ts} 읽기 실패: {r.get('error')}")
            return out
        out += [m for m in r.get("messages", []) if m.get("ts") != parent_ts]
        if not r.get("has_more"):
            return out
        cur = (r.get("response_metadata") or {}).get("next_cursor") or ""
        if not cur:
            return out
        time.sleep(1.2)


def permalink(token: str, cid: str, ts: str) -> str:
    try:
        r = _slack("chat.getPermalink", token, channel=cid, message_ts=ts)
        return r.get("permalink", "") if r.get("ok") else ""
    except Exception:
        return ""


def extract_links(m: dict) -> list[str]:
    """메시지에 붙은 URL. 슬랙은 <url|라벨> 로 감싸므로 url 만 뽑는다."""
    urls = re.findall(r"<(https?://[^|>\s]+)", m.get("text") or "")
    for a in m.get("attachments") or []:
        for k in ("title_link", "from_url", "original_url"):
            if a.get(k):
                urls.append(a[k])
    seen, out = set(), []
    for u in urls:
        u = u.rstrip(">,.")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ── 히어로 별칭 (앱 HERO_LINEUP 이 유일한 소스) ───────────────────────────────
def load_aliases(app_html: str | None = None) -> dict:
    """별칭 → 히어로명. 못 읽으면 RuntimeError — 목록을 새로 지어내지 않는다(진실소스 하나)."""
    path = app_html or os.path.join(
        os.environ.get("APP_REPO_PATH", "").strip() or "../hero-master-app", "public", "app.html")
    if not os.path.exists(path):
        raise RuntimeError(f"app.html 을 못 찾음({path}) — HERO_LINEUP 을 읽을 수 없어 중단. "
                           f"APP_REPO_PATH 를 지정하거나 hero-master-app 을 체크아웃할 것.")
    s = open(path, encoding="utf-8").read()
    m = re.search(r"const HERO_LINEUP = (\[.*?\]);", s, re.DOTALL)
    if not m:
        raise RuntimeError("app.html 에서 HERO_LINEUP 을 못 찾음 — 앱 구조 변경 확인 필요.")
    out = {}
    for h in json.loads(m.group(1)):
        for a in h.get("aliases", []):
            if len(a.replace(" ", "")) >= 2:
                out[a] = h.get("name", a)
    return out


# ── 판정 ─────────────────────────────────────────────────────────────────────
def classify(text: str, aliases: dict) -> dict | None:
    """후보면 dict, 아니면 None. 우선순위 = 높음(일정변경 명시) / 보통 / 낮음(정형 로그)."""
    t = " ".join((text or "").split())
    if not t:
        return None
    heroes = sorted({aliases[a] for a in aliases if a in t})
    direct = "히어로" in t
    if not heroes and not direct:
        return None

    # ★문서 발행일 접두는 날짜 신호에서 뺀다(26/08/13_27SS 1st QC …).
    body = _DOCDATE_RE.sub("", t)
    dtxt = find_date(body)
    sm = _SCHED_RE.search(t)
    if not dtxt and not sm:
        return None

    routine = bool(_ROUTINE_RE.search(t)) or bool(_DOCDATE_RE.search(t))
    # 회의 일정만 걸린 건 '높음'에서 내린다 — 큐에는 남지만 🔴 로는 안 뜬다.
    meeting_only = bool(sm) and _MEETING_RE.search(t) and not _PRODUCT_RE.search(t)
    prio = "낮음" if routine else ("높음" if (sm and not meeting_only) else "보통")
    return {"heroes": heroes or (["히어로(직접 언급)"] if direct else []),
            "date": dtxt,
            "sched": sm.group(0).strip() if sm else "",
            "prio": prio, "text": t}


# ── 원장 (구글시트) ──────────────────────────────────────────────────────────
def _ensure_tab(sheets, tab: str, header: list[str]) -> bool:
    try:
        meta = sheets.spreadsheets().get(
            spreadsheetId=ARCHIVE_SHEET,
            fields="sheets.properties(title,sheetId,gridProperties/columnCount)").execute()
        props = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
        if tab in props:
            # 컬럼이 늘어난 경우(스레드·링크 추가 등) 그리드와 헤더만 확장한다. 데이터는 안 건드림.
            cur = props[tab].get("gridProperties", {}).get("columnCount", 0)
            if cur < len(header):
                sheets.spreadsheets().batchUpdate(spreadsheetId=ARCHIVE_SHEET, body={"requests": [
                    {"appendDimension": {"sheetId": props[tab]["sheetId"], "dimension": "COLUMNS",
                                         "length": len(header) - cur}}]}).execute()
                print(f"[slack-watch] '{tab}' 컬럼 {cur} → {len(header)} 확장")
            sheets.spreadsheets().values().update(
                spreadsheetId=ARCHIVE_SHEET, range=f"'{tab}'!A1",
                valueInputOption="RAW", body={"values": [header]}).execute()
            return True
        sheets.spreadsheets().batchUpdate(spreadsheetId=ARCHIVE_SHEET, body={"requests": [
            {"addSheet": {"properties": {"title": tab,
                                         "gridProperties": {"rowCount": 5000,
                                                            "columnCount": len(header)}}}}]}).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{tab}'!A1",
            valueInputOption="RAW", body={"values": [header]}).execute()
        return True
    except Exception as e:
        print(f"[slack-watch] 탭 '{tab}' 준비 실패: {type(e).__name__}: {e}")
        return False


def load_cursors(sheets) -> dict:
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{CURSOR_TAB}'!A2:C").execute().get("values", [])
    except Exception:
        return {}
    return {str(r[0]).strip(): str(r[2]).strip()
            for r in vals if len(r) > 2 and str(r[0]).strip()}


def save_cursors(sheets, cursors: dict, names: dict) -> None:
    """★전체 덮어쓰기(update). append 로 쌓으면 원장이 무한히 길어지고 커서 조회가 느려진다."""
    rows = [[cid, names.get(cid, ""), ts, kst_now().strftime("%Y-%m-%d %H:%M")]
            for cid, ts in sorted(cursors.items())]
    if not rows:
        return
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{CURSOR_TAB}'!A2",
            valueInputOption="RAW", body={"values": rows}).execute()
    except Exception as e:
        print(f"[slack-watch] 커서 저장 실패: {type(e).__name__}: {e}")


def seen_keys(sheets) -> set:
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{QUEUE_TAB}'!B2:C").execute().get("values", [])
    except Exception:
        return set()
    return {f"{r[0]}|{r[1]}" for r in vals if len(r) > 1}


def log_queue(sheets, rows: list[list]) -> None:
    if not rows:
        return
    try:
        # ★insertDataOption=INSERT_ROWS 금지 — 같은 파일 다른 탭의 참조 범위가 밀려 집계가 0이 된다
        #   (2026-08-10 사용현황 실사고). 기본 OVERWRITE 는 참조가 고정된다.
        sheets.spreadsheets().values().append(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{QUEUE_TAB}'!A1",
            valueInputOption="RAW", insertDataOption="OVERWRITE",
            body={"values": rows}).execute()
    except Exception as e:
        print(f"[slack-watch] 큐 기록 실패: {type(e).__name__}: {e}")


# ── 본체 ─────────────────────────────────────────────────────────────────────
def run(send: bool = False, days: int = 0, app_html: str | None = None) -> int:
    tok = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not tok:
        print("SLACK_BOT_TOKEN 없음 — 중단")
        return 1

    aliases = load_aliases(app_html)
    print(f"[slack-watch] 히어로 별칭 {len(aliases)}개 (앱 HERO_LINEUP)")

    sheets = None
    try:
        from soo.auth import build_services, get_credentials
        sheets = build_services(get_credentials(ROOT / "credentials.json",
                                                ROOT / "token.json"))["sheets"]
        if send:                      # 드라이런은 시트를 건드리지 않는다(탭 생성도 쓰기다)
            for tab, hdr in ((CURSOR_TAB, CURSOR_HEADER), (QUEUE_TAB, QUEUE_HEADER)):
                _ensure_tab(sheets, tab, hdr)
    except Exception as e:
        print(f"[slack-watch] 시트 접근 실패 — 커서·dedup 없이 진행: {type(e).__name__}: {e}")

    cursors = load_cursors(sheets) if sheets else {}
    seen = seen_keys(sheets) if sheets else set()
    chans = bot_channels(tok)
    print(f"[slack-watch] 감시 채널 {len(chans)}개 (봇 멤버십 기준)")

    fallback = f"{(kst_now() - dt.timedelta(days=days or FIRST_RUN_DAYS)).timestamp():.0f}"
    cands, names, newcur = [], {}, dict(cursors)
    scanned = 0
    n_thread_msgs = [0]        # 스레드에서 추가로 읽은 답글 수(로그용)

    for c in chans:
        cid = c["id"]
        nm = c.get("name") or cid
        names[cid] = nm
        mark = fallback if days else (cursors.get(cid) or fallback)

        # ★부모는 커서보다 넓게 훑는다. `conversations.history` 는 답글이 달려도 부모를 최신으로
        #   끌어올려 주지 않아, 커서만 쓰면 **지난주 글에 오늘 달린 답글이 영영 안 보인다**.
        #   실측 2026-08-15: 답글이 최상위의 147%(히어로-pj 는 4.3배)라 놓치면 알맹이를 통째로 놓친다.
        scan_from = min(mark, f"{(kst_now() - dt.timedelta(days=THREAD_LOOKBACK_DAYS)).timestamp():.0f}")
        msgs = channel_history(tok, cid, scan_from)
        top = mark

        def _take(m, parent=None):
            """후보면 담는다. parent 가 있으면 답글."""
            nonlocal scanned
            ts = m.get("ts", "")
            if m.get("bot_id") or m.get("subtype") in ("channel_join", "channel_leave"):
                return            # 봇 발화(랭킹봇 등)는 우리가 만든 소음이라 제외
            scanned += 1
            hit = classify(m.get("text", ""), aliases)
            if not hit or f"{nm}|{ts}" in seen:
                return
            hit.update(ch=nm, cid=cid, ts=ts, links=extract_links(m),
                       parent=(" ".join((parent.get("text") or "").split())[:80] if parent else ""))
            cands.append(hit)

        for m in msgs:
            ts = m.get("ts", "")
            if ts > top:
                top = ts
            if ts > mark:                       # 최상위 신규
                _take(m)
            # 스레드: 부모가 오래됐어도 **새 답글이 있으면** 그 답글만 가져온다.
            if m.get("reply_count") and (m.get("latest_reply") or "") > mark:
                for rp in thread_replies(tok, cid, ts, mark):
                    rts = rp.get("ts", "")
                    if rts > top:
                        top = rts
                    if rts > mark:
                        _take(rp, parent=m)
                    n_thread_msgs[0] += 1
                time.sleep(1.0)
        newcur[cid] = top
        time.sleep(1.0)

    order = {"높음": 0, "보통": 1, "낮음": 2}
    cands.sort(key=lambda x: (order[x["prio"]], x["ts"]))
    n_rep = sum(1 for c in cands if c.get("parent"))
    print(f"[slack-watch] 사람 메시지 {scanned}건(스레드 답글 {n_thread_msgs[0]}건 포함) "
          f"→ 신규 후보 {len(cands)}건(답글발 {n_rep}) "
          + " · ".join(f"{k} {sum(1 for c in cands if c['prio'] == k)}" for k in order))

    for c in cands[:40]:
        c["link"] = permalink(tok, c["cid"], c["ts"]) if send else ""
        kind = "↳답글" if c.get("parent") else "글"
        print(f"  [{c['prio']}] {kind} #{c['ch']} {c['date'] or c['sched']} {c['heroes']}"
              + (f"  링크{len(c['links'])}" if c.get("links") else ""))
        if c.get("parent"):
            print(f"      (원글: {c['parent']})")
        print(f"      {c['text'][:160]}")

    if not send:
        print("\n(드라이런 — 발송·기록 안 함. 실제 발송은 --send)")
        return 0

    if cands:
        log_queue(sheets, [[kst_now().strftime("%Y-%m-%d"), c["ch"], c["ts"], c["prio"],
                            ", ".join(c["heroes"]), c["date"] or c["sched"],
                            ("[답글] " if c.get("parent") else "") + c["text"][:300],
                            c.get("link", ""), c.get("parent", ""),
                            " ".join(c.get("links") or [])[:500]] for c in cands]) if sheets else None
        _notify(cands)
    else:
        print("신규 후보 없음 — 발송 스킵")

    if sheets:
        save_cursors(sheets, newcur, names)
    return 0


def _notify(cands: list[dict]) -> None:
    real = [c for c in cands if c["prio"] != "낮음"]
    lines = [f"*슬랙 히어로 일정 후보 {len(real)}건* (참고 {len(cands) - len(real)}건 별도)",
             "_시트에 자동 반영하지 않습니다 — 확인 후 반영해 주세요._", ""]
    for c in real[:15]:
        tag = "🔴" if c["prio"] == "높음" else "•"
        head = c["text"][:110] + ("…" if len(c["text"]) > 110 else "")
        who = ", ".join(c["heroes"][:3])
        when = c["date"] or c["sched"]
        link = f" <{c['link']}|보기>" if c.get("link") else ""
        kind = " ↳답글" if c.get("parent") else ""
        lines.append(f"{tag} *#{c['ch']}*{kind} · {who} · `{when}`{link}")
        if c.get("parent"):
            lines.append(f"    _원글: {c['parent']}_")
        lines.append(f"    {head}")
        if c.get("links"):
            lines.append("    🔗 " + " ".join(c["links"][:3]))
    if len(real) > 15:
        lines.append(f"\n… 외 {len(real) - 15}건 (원장 `{QUEUE_TAB}` 탭)")
    try:
        from soo.hero_ops import notify
        notify.send("\n".join(lines))
    except Exception as e:
        print(f"[slack-watch] 발송 실패: {type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="DM 발송 + 원장 기록(기본은 드라이런)")
    ap.add_argument("--days", type=int, default=0, help="커서 무시하고 N일 소급 수집")
    ap.add_argument("--app-html", default=None, help="app.html 경로(HERO_LINEUP 소스)")
    a = ap.parse_args()
    return run(send=a.send, days=a.days, app_html=a.app_html)


if __name__ == "__main__":
    raise SystemExit(main())
