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

3. **후보 판정은 규칙 기반, 브리핑은 LLM.** 무엇을 후보로 담을지는 규칙으로 넓게 거르고(오탐
   비용이 낮다), 그걸 사람이 읽을 문장으로 압축하는 건 `claude -p` 가 한다.
   ★거르되 드롭하지 않는다 — 비히어로 일정을 파이프라인에서 영구 드롭했다가 "5월부터 마케팅이
   멈췄나?"는 착시를 만든 전례가 있다.
   ★한때 "CI에 claude CLI 가 없어 LLM 을 못 쓴다"고 단정했는데 **틀렸다** —
   `claude setup-token` 으로 장기 토큰을 발급하면 CI 에서도 쓸 수 있다(2026-08-15).

5. **승인은 슬랙 이모지 반응으로 받는다.** 브리핑 스레드에 건별 메시지를 달고 ✅/❌ 를 읽는다.
   버튼·슬래시커맨드는 상시 서버가 필요해 이 구조로는 못 받는다. 대신 **실시간이 아니다** —
   다음 실행 때 반영된다.

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

필요 스코프(2026-08-15 전부 부여됨):
  channels:history · channels:read · channels:join · groups:history · groups:read  (채널 수집)
  im:write · im:history · reactions:read                                            (DM 발송·승인 반응)
CI 시크릿: SLACK_BOT_TOKEN · GOOGLE_* · CLAUDE_CODE_OAUTH_TOKEN(없으면 규칙 기반 요약으로 폴백).
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
                "원글(스레드)", "첨부링크", "문서제목", "승인메시지ts", "승인상태", "제목요약"]
ACK_TS_COL, ACK_STATE_COL, TITLE_COL = "L", "M", "N"   # 1-based 12·13·14
#   ★N열(제목요약) = 승인분이 IMC 에 뜰 때 쓰는 제목. **수집 시점에 만들어 원장에 박아 둔다**
#     (생성기에서 만들지 않는다 — daily 갱신 잡에는 claude CLI 가 없고, 매일 다시 만들면 같은
#     일정의 캘린더 제목이 날마다 바뀐다). 못 만들면 빈칸 → 소비 쪽이 원문 앞 60자로 폴백한다.

APPROVED_MAX_DAYS = 120   # 수집일 ↔ 해석된 날짜 거리 상한. 목록번호('1.5')·배수('3.2')가 날짜로
                          # 둔갑하면 반년 밖으로 튄다(2026-09-01 실측 5건: 3.2·1.7·1.5·1.8·3.5).

# ★승인은 슬랙 이모지 반응으로 받는다(2026-08-15 사용자 결정: "굳이 스프레드시트를 통해야 해?").
#   버튼·슬래시커맨드는 슬랙이 우리 쪽으로 요청을 쏘는 구조라 **상시 떠 있는 공개 HTTPS 서버**가
#   필요하다. 하루 2회 도는 잡뿐인 지금 구조로는 못 받는다. 반응은 우리가 나중에 읽으러 가면
#   되므로 서버가 필요 없다 — 대신 **실시간이 아니다**(다음 실행 때 반영).
APPROVE_EMOJI = {"white_check_mark", "heavy_check_mark", "o", "ok_hand", "+1", "thumbsup"}
REJECT_EMOJI = {"x", "negative_squared_cross_mark", "no_entry", "no_entry_sign", "-1", "thumbsdown"}
MAX_ACK_ITEMS = 10          # 한 번에 승인 요청할 최대 건수(DM 이 너무 길어지지 않게)

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


# ── 구글 링크 제목 해석 ───────────────────────────────────────────────────────
# ★내용까지 읽지 않고 **제목만** 가져온다(사용자 결정 2026-08-15). 어느 문서 얘기인지 바로
#   보이면서 Sheets/Docs 본문 읽기의 쿼터·권한 문제를 피한다.
# ★못 읽는 게 정상이다 — 링크의 절반 이상이 미공유고(로컬 실측 12개 중 6개),
#   CI 는 **서비스계정**이라 더 적다. 실패하면 조용히 URL 그대로 둔다.
_GDOC_RE = re.compile(
    r"https?://(?:docs|drive|sheets|slides)\.google\.com/[^\s]*?/d/([A-Za-z0-9_-]{20,})"
    r"|https?://drive\.google\.com/[^\s]*?[?&]id=([A-Za-z0-9_-]{20,})")


def gdoc_id(url: str) -> str:
    m = _GDOC_RE.search(url or "")
    return (m.group(1) or m.group(2)) if m else ""


def resolve_titles(drive, urls: list[str], cache: dict) -> dict:
    """{url: 제목}. 해석 못 한 URL 은 키가 없다(호출부가 URL 그대로 쓴다)."""
    out = {}
    for u in urls:
        fid = gdoc_id(u)
        if not fid:
            continue
        if fid not in cache:
            try:
                f = drive.files().get(fileId=fid, fields="name,mimeType",
                                      supportsAllDrives=True).execute()
                cache[fid] = f.get("name", "")
            except Exception:
                cache[fid] = ""      # 미공유·삭제 — 다시 묻지 않는다
        if cache[fid]:
            out[u] = cache[fid]
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
def run(send: bool = False, days: int = 0, app_html: str | None = None,
        no_dedup: bool = False) -> int:
    tok = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not tok:
        print("SLACK_BOT_TOKEN 없음 — 중단")
        return 1

    aliases = load_aliases(app_html)
    print(f"[slack-watch] 히어로 별칭 {len(aliases)}개 (앱 HERO_LINEUP)")

    sheets = drive = None
    try:
        from soo.auth import build_services, get_credentials
        _svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
        sheets, drive = _svc["sheets"], _svc["drive"]
        if send:                      # 드라이런은 시트를 건드리지 않는다(탭 생성도 쓰기다)
            for tab, hdr in ((CURSOR_TAB, CURSOR_HEADER), (QUEUE_TAB, QUEUE_HEADER)):
                _ensure_tab(sheets, tab, hdr)
    except Exception as e:
        print(f"[slack-watch] 시트 접근 실패 — 커서·dedup 없이 진행: {type(e).__name__}: {e}")

    # 지난 실행에서 보낸 승인 요청의 이모지 반응을 먼저 걷는다(수집보다 앞 — 이번 브리핑에 결과를 싣는다).
    acked = read_approvals(tok, sheets) if send else (0, 0)

    cursors = load_cursors(sheets) if sheets else {}
    # --no-dedup: 이미 올린 건도 다시 담는다(브리핑 재생성·요약 경로 점검용).
    seen = set() if no_dedup else (seen_keys(sheets) if sheets else set())
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

    # 구글 링크 제목 해석 — 후보에 걸린 것만(전량 조회 금지). 못 읽으면 URL 그대로.
    titles, _tc = {}, {}
    if drive:
        all_urls = [u for c in cands for u in (c.get("links") or [])]
        gurls = [u for u in dict.fromkeys(all_urls) if gdoc_id(u)]
        titles = resolve_titles(drive, gurls, _tc)
        if gurls:
            print(f"[slack-watch] 구글 링크 제목 해석 {len(titles)}/{len(gurls)}"
                  f" (못 읽은 건 미공유 — CI 는 서비스계정이라 더 적다)")

    for c in cands[:40]:
        c["link"] = permalink(tok, c["cid"], c["ts"]) if send else ""
        c["titled"] = [(u, titles[u]) for u in (c.get("links") or []) if u in titles]
        kind = "↳답글" if c.get("parent") else "글"
        print(f"  [{c['prio']}] {kind} #{c['ch']} {c['date'] or c['sched']} {c['heroes']}"
              + (f"  링크{len(c['links'])}" if c.get("links") else ""))
        if c.get("parent"):
            print(f"      (원글: {c['parent']})")
        if c["titled"]:
            print(f"      📄 {' · '.join(t for _, t in c['titled'][:3])}")
        print(f"      {c['text'][:160]}")

    if not send:
        print("\n(드라이런 — 발송·기록 안 함. 실제 발송은 --send)")
        return 0

    if cands:
        # ★발송이 먼저다 — 승인 요청 메시지의 ts 를 받아 원장에 같이 적어야, 다음 실행에서
        #   "이 반응 = 이 항목"으로 되짚을 수 있다. 순서를 바꾸면 ts 칸이 영영 빈다.
        _notify(cands, token=tok, acked=acked)
        titles = title_lines(cands)          # 실패해도 {} — 소비 쪽이 원문으로 폴백한다
        if sheets:
            log_queue(sheets, [[kst_now().strftime("%Y-%m-%d"), c["ch"], c["ts"], c["prio"],
                                ", ".join(c["heroes"]), c["date"] or c["sched"],
                                ("[답글] " if c.get("parent") else "") + c["text"][:300],
                                c.get("link", ""), c.get("parent", ""),
                                " ".join(c.get("links") or [])[:500],
                                " · ".join(t for _, t in (c.get("titled") or []))[:300],
                                c.get("ack_ts", ""), "", titles.get(c["ts"], "")]
                               for c in cands])
    else:
        print("신규 후보 없음 — 발송 스킵")

    if sheets:
        save_cursors(sheets, newcur, names)
    return 0


SUMMARY_PROMPT = """당신은 무신사 스탠다드 전략팀의 IMC 운영 담당자입니다.
아래는 슬랙 채널들에서 히어로 상품의 일정 관련으로 자동 수집된 메시지 원문입니다.
이걸 담당자가 **읽자마자 판단할 수 있는 브리핑**으로 압축하세요.

규칙:
- 히어로 상품별로 묶고, 상품명을 굵게(`*라이트다운*`) 시작합니다.
- 각 줄은 "무슨 일정이 어떻게 됐는지" 한 문장. **바뀐 건 `9/9 → 8/26` 처럼 변화를 명시**합니다.
- 확정/변경/신규 일정을 우선하고, 단순 문의·잡담·진행 로그는 버립니다.
- ★**원문에 없는 날짜·수량·상품명을 절대 만들지 마세요.** 애매하면 그 항목을 빼십시오.
- 각 줄 끝에 출처를 `<링크|#채널>` 형식으로 답니다. 링크가 없으면 `#채널`만.
- 전체 15줄 이내. 서론·맺음말·"요약하면" 같은 군더더기 금지. 슬랙 mrkdwn 로만.
- 마지막에 `⚠️ 확인 필요:` 한 줄로, 서로 어긋나는 일정이나 미확정 사항이 있으면 짚어 주세요. 없으면 생략.

원문(JSON):
"""


def llm_brief(cands: list[dict]) -> str | None:
    """claude CLI 로 브리핑 생성. CLI·토큰이 없거나 실패하면 None → 규칙 기반으로 폴백.

    ★Anthropic SDK 를 쓰지 않는다(CLAUDE.md 1-17) — Max 구독의 `claude -p` CLI 를 쓴다.
      CI 에서는 `CLAUDE_CODE_OAUTH_TOKEN` 시크릿이 있어야 한다(`claude setup-token` 으로 발급).
    ★프롬프트는 인자가 아니라 **stdin** 으로 넘긴다(Windows 명령줄 길이 한계).
    """
    import shutil
    import subprocess
    if not shutil.which("claude"):
        print("[slack-watch] claude CLI 없음 — 규칙 기반 요약으로 대체")
        return None
    payload = [{"채널": c["ch"], "히어로": c["heroes"], "날짜": c["date"] or c["sched"],
                "원글": c.get("parent", ""), "본문": c["text"][:600],
                "링크": c.get("link", ""),
                "문서": [t for _, t in (c.get("titled") or [])]} for c in cands]
    try:
        r = subprocess.run(["claude", "-p"],
                           input=SUMMARY_PROMPT + json.dumps(payload, ensure_ascii=False, indent=1),
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
    except Exception as e:
        print(f"[slack-watch] 요약 호출 예외 — 규칙 기반으로 대체: {type(e).__name__}: {e}")
        return None
    if r.returncode != 0 or not (r.stdout or "").strip():
        print(f"[slack-watch] 요약 실패(rc={r.returncode}) — 규칙 기반으로 대체: "
              f"{(r.stderr or '')[:200]}")
        return None
    return r.stdout.strip()


TITLE_PROMPT = """아래는 슬랙에서 수집한 히어로 상품 일정 관련 메시지들입니다.
각 항목을 **캘린더에 그대로 띄울 한 줄 제목**으로 압축하세요.

규칙:
- 20~40자. "무엇이 언제 어떻게" 만 남기고 인사말·존칭·수신자·군더더기는 버립니다.
- 날짜가 바뀐 건이면 `9/9 → 8/26` 처럼 변화를 제목에 넣습니다.
- ★원문에 없는 날짜·수량·상품명을 절대 만들지 마세요. 애매하면 원문 표현을 그대로 짧게 씁니다.
- 이모지·마크다운·따옴표 금지. 순수 텍스트 한 줄.

출력은 **JSON 객체 하나만**. 키=입력의 id, 값=제목 문자열. 다른 말 금지.

입력(JSON):
"""


def title_lines(cands: list[dict]) -> dict:
    """{ts: 한 줄 제목}. claude CLI·토큰이 없거나 실패하면 {} → 소비 쪽이 원문으로 폴백한다.

    ★수집 시점에 한 번만 만든다. 생성기(daily 갱신)에는 claude CLI 가 없고, 매일 다시 만들면
      같은 일정의 캘린더 제목이 날마다 바뀐다.
    """
    if not cands:
        return {}
    import shutil
    import subprocess
    exe = shutil.which("claude")
    if not exe:
        print("[slack-watch] claude CLI 없음 — 제목요약 스킵(원문 폴백)")
        return {}
    payload = [{"id": c["ts"], "히어로": ", ".join(c["heroes"][:3]),
                "날짜": c.get("date") or c.get("sched") or "",
                "원문": c["text"][:400]} for c in cands]
    try:
        r = subprocess.run([exe, "-p"], input=TITLE_PROMPT + json.dumps(payload, ensure_ascii=False),
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
    except Exception as e:
        print(f"[slack-watch] 제목요약 예외 — 원문 폴백: {type(e).__name__}: {e}")
        return {}
    if r.returncode != 0:
        print(f"[slack-watch] 제목요약 실패(rc={r.returncode}) — 원문 폴백")
        return {}
    m = re.search(r"\{.*\}", r.stdout or "", re.DOTALL)
    if not m:
        print("[slack-watch] 제목요약 파싱 실패 — 원문 폴백")
        return {}
    try:
        out = {str(k): " ".join(str(v).split())[:60] for k, v in json.loads(m.group(0)).items()}
    except Exception:
        print("[slack-watch] 제목요약 JSON 오류 — 원문 폴백")
        return {}
    print(f"[slack-watch] 제목요약 {len(out)}/{len(cands)}건")
    return out


def rule_brief(cands: list[dict]) -> str:
    """LLM 없이도 원문 나열보다는 낫게 — 히어로별로 묶어 '언제 무엇' 한 줄씩."""
    _ACT = ["발매", "입고", "기획전", "캠페인", "쇼케이스", "촬영", "랭킹", "쿠폰",
            "선발매", "예약", "PPL", "프로모션", "론칭", "런칭", "오픈"]
    by = {}
    for c in cands:
        for h in (c["heroes"] or ["기타"]):
            by.setdefault(h, []).append(c)
    lines = []
    for h, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"*{h}*")
        for c in items[:4]:
            acts = [a for a in _ACT if a in c["text"]][:3]
            when = c["date"] or c["sched"]
            src = f"<{c['link']}|#{c['ch']}>" if c.get("link") else f"#{c['ch']}"
            lines.append(f"  · `{when}` {' / '.join(acts) or '일정 언급'} — {src}")
        if len(items) > 4:
            lines.append(f"  · … 외 {len(items) - 4}건")
    return "\n".join(lines[:40])


def dm_channel(token: str) -> str:
    """본인 DM 채널 ID. 반응을 읽으려면 채널 ID 가 필요하다."""
    try:
        req = urllib.request.Request(
            SLACK_API + "conversations.open",
            data=json.dumps({"users": os.environ.get("NOTIFY_TARGET", "").strip()
                                      or "U09BU1F85TR"}).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            b = json.load(r)
        return (b.get("channel") or {}).get("id", "") if b.get("ok") else ""
    except Exception:
        return ""


def read_approvals(token: str, sheets) -> tuple[int, int]:
    """지난 실행에서 보낸 승인 요청 메시지의 이모지 반응을 읽어 원장에 기록. (승인, 기각) 건수.

    ★대기 중인 건만 조회한다(승인상태가 빈칸). 전체를 매번 다시 묻지 않는다.
    """
    if not sheets:
        return (0, 0)
    ch = dm_channel(token)
    if not ch:
        print("[slack-watch] DM 채널을 못 열어 승인 확인 스킵")
        return (0, 0)
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{QUEUE_TAB}'!A2:M").execute().get("values", [])
    except Exception as e:
        print(f"[slack-watch] 원장 읽기 실패 — 승인 확인 스킵: {type(e).__name__}: {e}")
        return (0, 0)

    updates, ok_n, no_n = [], 0, 0
    for i, row in enumerate(vals):
        row = list(row) + [""] * (13 - len(row))
        ack_ts, state = str(row[11]).strip(), str(row[12]).strip()
        if not ack_ts or state:
            continue                      # 요청 안 보냈거나 이미 처리됨
        try:
            r = _slack("reactions.get", token, channel=ch, timestamp=ack_ts, full="true")
        except Exception:
            continue
        if not r.get("ok"):
            continue
        names = {x.get("name", "").split("::")[0]
                 for x in ((r.get("message") or {}).get("reactions") or [])}
        if names & APPROVE_EMOJI:
            updates.append((i + 2, "승인")); ok_n += 1
        elif names & REJECT_EMOJI:
            updates.append((i + 2, "기각")); no_n += 1
        time.sleep(0.4)

    if updates:
        try:
            sheets.spreadsheets().values().batchUpdate(
                spreadsheetId=ARCHIVE_SHEET,
                body={"valueInputOption": "RAW",
                      "data": [{"range": f"'{QUEUE_TAB}'!{ACK_STATE_COL}{r}", "values": [[v]]}
                               for r, v in updates]}).execute()
        except Exception as e:
            print(f"[slack-watch] 승인상태 기록 실패: {type(e).__name__}: {e}")
    if ok_n or no_n:
        print(f"[slack-watch] 승인 반응 반영 — 승인 {ok_n} · 기각 {no_n}")
    return (ok_n, no_n)


def _notify(cands: list[dict], token: str = "", acked: tuple[int, int] = (0, 0)) -> None:
    """브리핑(부모) + 승인 대상 개별 메시지(스레드). 각 항목의 ts 를 cand['ack_ts'] 에 남긴다."""
    real = [c for c in cands if c["prio"] != "낮음"]
    if not real:
        return
    body = llm_brief(real) or rule_brief(real)
    head = [f"*슬랙 히어로 일정 브리핑* — 후보 {len(real)}건"
            + (f" (참고 {len(cands) - len(real)}건 별도)" if len(cands) > len(real) else ""),
            "_원천 시트에 자동 반영하지 않습니다._", ""]
    if acked != (0, 0):
        head.append(f"_지난번 반응 반영: 승인 {acked[0]} · 기각 {acked[1]}_\n")
    tail = ["", f"👇 *아래 스레드의 각 건에 ✅(맞음) 또는 ❌(아님)를 눌러주세요.* "
                f"다음 실행 때 반영됩니다.", f"_원문 전건은 원장 `{QUEUE_TAB}` 탭._"]
    try:
        from soo import persona
        from soo.hero_ops import notify
        tok = token or os.environ.get("SLACK_BOT_TOKEN", "").strip()
        target = os.environ.get("NOTIFY_TARGET", "").strip() or notify.DEFAULT_TARGET
        parent = persona.send_slack("\n".join(head + [body] + tail), bot_token=tok,
                                    target=target, persona=persona.RANKING_BOT)
        if not parent:
            print("[slack-watch] 브리핑 발송 실패")
            return
        ch = dm_channel(tok)
        for c in real[:MAX_ACK_ITEMS]:
            when = c["date"] or c["sched"]
            who = ", ".join(c["heroes"][:3])
            src = f"<{c['link']}|#{c['ch']}>" if c.get("link") else f"#{c['ch']}"
            txt = (f"*{who}* · `{when}` · {src}\n{c['text'][:180]}"
                   + ("…" if len(c["text"]) > 180 else ""))
            ts = persona.send_slack(txt, bot_token=tok, target=ch or target,
                                    persona=persona.RANKING_BOT, thread_ts=parent)
            if ts:
                c["ack_ts"] = ts
            time.sleep(0.4)
        if len(real) > MAX_ACK_ITEMS:
            print(f"[slack-watch] 승인 요청은 상위 {MAX_ACK_ITEMS}건만 — 나머지는 원장에서 확인")
    except Exception as e:
        print(f"[slack-watch] 발송 실패: {type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="DM 발송 + 원장 기록(기본은 드라이런)")
    ap.add_argument("--days", type=int, default=0, help="커서 무시하고 N일 소급 수집")
    ap.add_argument("--app-html", default=None, help="app.html 경로(HERO_LINEUP 소스)")
    ap.add_argument("--no-dedup", action="store_true",
                    help="이미 큐에 올린 건도 다시 담는다(브리핑 재생성용)")
    a = ap.parse_args()
    return run(send=a.send, days=a.days, app_html=a.app_html, no_dedup=a.no_dedup)


if __name__ == "__main__":
    raise SystemExit(main())


# ── 승인분 → IMC 항목 (생성기가 import) ──────────────────────────────────────
# ★원천 시트가 여전히 진실소스다. 승인분은 type '슬랙승인' 딱지를 달고 **얹히는** 별도 레인이고,
#   같은 일정이 원천에 들어오면 중복이므로 걸러낸다. 담당자가 원천을 고치면 그쪽이 이긴다.
# ★이모지 단축코드는 한글도 쓴다(`:체크:`) — [a-z] 만 잡으면 제목에 그대로 남는다.
_MARKUP_RE = re.compile(r"<[^>]*>|[*_`~]|:[0-9A-Za-z가-힣_+-]{1,20}:|&amp;|&gt;|&lt;")


def _clean_title(t: str) -> str:
    t = _MARKUP_RE.sub(" ", str(t or ""))
    t = re.sub(r"^\s*\[답글\]\s*", "", t)
    return " ".join(t.split())


def resolve_date(expr: str, anchor: dt.date) -> str:
    """'9/23'·'8월 26일' → ISO 날짜. 슬랙 표기엔 연도가 없어 **논의 시점에서 가장 가까운 해**로 읽는다.

    ★"미래면 무조건 내년"으로 밀면 안 된다 — 8/15 에 언급된 `7/1`(45일 전)이 2027-07-01 로
      1년 뒤에 꽂힌다. 일정 논의는 대개 논의 시점 ±6개월 안이므로 그 창 밖이면 버린다.
    """
    e = str(expr or "")
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", e) or \
        re.search(r"(?<!\d)(\d{1,2})\s*(?:/\s*|\.)(\d{1,2})(?!\d)", e)
    if not m:
        return ""
    mo, da = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= da <= 31):
        return ""
    best = None
    for y in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            d = dt.date(y, mo, da)
        except ValueError:
            continue
        if best is None or abs((d - anchor).days) < abs((best - anchor).days):
            best = d
    if best is None or abs((best - anchor).days) > 200:
        return ""
    return best.isoformat()


def approved_items(sheets, today: dt.date, existing: list[dict] | None = None) -> list[dict]:
    """승인상태='승인' 행 → IMC 항목. 날짜를 못 뽑거나 원천에 이미 있으면 버린다."""
    if not sheets:
        return []
    try:
        # ★범위는 헤더 길이에서 만든다 — 'A2:M' 로 박아 두면 N열(제목요약)을 추가해도 안 읽힌다.
        #   실제로 밟음(2026-09-01): 제목을 원장에 다 채웠는데 캘린더엔 원문이 그대로 떴다.
        last = chr(ord("A") + len(QUEUE_HEADER) - 1)
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=ARCHIVE_SHEET,
            range=f"'{QUEUE_TAB}'!A2:{last}").execute().get("values", [])
    except Exception as e:
        print(f"[slack-watch] 승인분 읽기 실패: {type(e).__name__}: {e}")
        return []

    # 원천에 이미 있는 일정(같은 날짜 + 제목 6자 겹침)은 중복 — 승인분을 얹지 않는다.
    have = {}
    for x in (existing or []):
        have.setdefault(str(x.get("date", "")), []).append(
            re.sub(r"[^가-힣0-9A-Za-z]", "", str(x.get("title", ""))))

    out, skip_nodate, skip_far, skip_self, skip_dup = [], 0, 0, 0, 0
    seen_msg = set()
    for row in vals:
        row = list(row) + [""] * (14 - len(row))
        if str(row[12]).strip() != "승인":
            continue
        # ★같은 슬랙 메시지가 여러 번 수집돼 있다(--no-dedup 실행분). 원천 중복 가드는 원천만 보므로
        #   승인분끼리는 안 막힌다 — 같은 일정이 캘린더에 2·3중으로 뜬다(2026-09-01 실측 136행→87건).
        msg = (str(row[1]), str(row[2]))
        if msg in seen_msg:
            skip_self += 1
            continue
        # ★앵커는 오늘이 아니라 **수집일**이다. 오늘로 잡으면 8월에 논의된 '1/5'가 내년으로 밀린다.
        try:
            anchor = dt.date.fromisoformat(str(row[0]).strip())
        except Exception:
            anchor = today
        d = resolve_date(row[5], anchor)
        if not d:
            skip_nodate += 1
            continue
        if abs((dt.date.fromisoformat(d) - anchor).days) > APPROVED_MAX_DAYS:
            skip_far += 1        # 목록번호·배수가 날짜로 둔갑한 것 — 일정 논의는 이만큼 안 떨어진다
            continue
        # 제목 = 수집 때 만들어 둔 한 줄 요약(N열). 없으면 원문 앞 60자로 폴백.
        #   ★N열엔 _clean_title 을 태우지 않는다 — 마크업 제거가 물결표를 지워 '8/13~8/23' 이
        #     '8/13 8/23'(두 날짜인지 범위인지 모를 문자열)이 된다. 이미 정제된 값이다.
        title = " ".join(str(row[13]).split())[:60] or _clean_title(row[6])[:60]
        if not title:
            continue
        seen_msg.add(msg)
        key = re.sub(r"[^가-힣0-9A-Za-z]", "", title)
        if any(len(key) >= 6 and (key[:6] in h or h[:6] in key) for h in have.get(d, [])):
            skip_dup += 1
            continue
        out.append({"date": d, "title": title, "heroes": str(row[4]),
                    "ch": str(row[1]), "link": str(row[7])})
    if out or skip_nodate or skip_far or skip_self or skip_dup:
        print(f"[slack-watch] 승인분 → IMC {len(out)}건 (날짜 못 뽑아 제외 {skip_nodate} · "
              f"날짜 오탐 {skip_far} · 같은 메시지 중복 {skip_self} · 원천 중복 {skip_dup})")
    return out
