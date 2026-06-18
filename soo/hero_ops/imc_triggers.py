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
    msgs = msgs3 + msgs4
    print(f"기준일 {as_of} · 발매 {len(releases)}건/노출알람 {len(msgs3)} · 캠페인 {len(camps)}건/역산알람 {len(msgs4)} · TEST_ONLY={T.TEST_ONLY}")
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
