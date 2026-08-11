# -*- coding: utf-8 -*-
"""히어로 자동화 4종 통합 감시 — 이상이 있으면 슬랙 DM 1건으로 모아 보낸다 (2026-08-11 신설).

감시 대상 (사용자 지정):
  ① 히어로 랭킹봇      — hourly 캡처 / daily 리포트 / rank1 1위 알림
  ② 히어로 마스터 앱    — daily CI + 상류 DBX 실적 잡 + app.html 배포
  ③ 입고               — 마스터앱에서 분리한 `/inbound` 보드의 원천(입고확정 = DBX `입고일자별`)
  ④ 26FW 히어로 대시보드 — DBX 잡 971710339901758 + 데이터시트 신선도

★설계 원칙 (CLAUDE.md 1-3 "잡이 SUCCESS라고 데이터가 들어온 게 아니다")
  - 워크플로/잡 **실패**만 보지 않는다. **실행 자체가 없었던 것**(외부 cron 사망·빌링 차단)과
    **초록불인데 산출물이 안 바뀐 것**(신선도)을 같이 본다. 과거 사고가 전부 이 셋 중 하나였다.
  - 개별 워크플로에 `if: failure()` 를 붙이는 방식으로는 "실행이 아예 안 됨"을 절대 못 잡는다.
    그래서 밖에서 GitHub API·DBX API·시트를 읽는 감시자 하나로 통합했다.
  - 판정은 **시각 게이트** 뒤에만 한다(예: DBX 잡은 09:30 시작·~3h 소요 → 14시 전에는 stale이 정상).
  - 같은 경고는 **하루 1회만** 발송한다(원장 `_감시로그` 탭). 시끄러운 알림은 무시당해 무용지물이 된다.

사용:
  python -m soo.hero_ops.ops_watch            # 점검 + 이상 시 DM (원장 dedup 적용)
  python -m soo.hero_ops.ops_watch --dry-run  # 발송·기록 없이 콘솔만
  python -m soo.hero_ops.ops_watch --force    # 오늘 이미 보낸 건도 다시 발송
  python -m soo.hero_ops.ops_watch --no-gate  # 시각 게이트 무시(전 항목 즉시 판정, 진단용)
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# ── 감시 대상 상수 ────────────────────────────────────────────────────────────
GH_REPO = "hero-bot-26/hero-bot"
APP_REPO = "hero-bot-26/hero-master-app"
APP_FILE = "public/app.html"

ARCHIVE_SHEET = "1UqH-pi5YuQrVYpfj74khXSTVbWml1m4YuaKUAnUgzPI"      # 랭킹봇 아카이브(Long/Wide)
SALES_SHEET = "1iHH2qG8Uj5vmlC3aXkey96usktWODmguDPD_ToT2rfA"        # 마스터앱 실적 시트
DASH_DATA_SHEET = "1O78bMnJZq-U6zO2mZLHV84573uKM9DU2wpgzeGDBIk0"    # 26FW 대시보드 데이터시트

DBX_HOST_DEFAULT = "https://musinsa-data-ws.cloud.databricks.com"
DBX_JOBS = {
    "334354908178394": ("마스터앱", "히어로 마스터 앱_실적"),
    "971710339901758": ("26FW대시보드", "히어로 26FW 실적 (자동화)"),
}

WATCH_TAB = "_감시로그"
WATCH_HEADER = ["날짜", "코드", "레벨", "시스템", "메시지", "발송시각"]

# 랭킹 리포트가 커버해야 하는 3개 뷰 (soo.tasks.ranking_daily 의 VIEWS 와 같은 라벨)
RANKING_VIEWS = ["전체", "남자", "여자"]


# ── 결과 컨테이너 ─────────────────────────────────────────────────────────────
class Finding:
    """이상 1건. code 는 dedup 키라 안정적으로 유지할 것(문구가 바뀌어도 같은 건이면 같은 code)."""

    def __init__(self, code: str, system: str, msg: str, level: str = "경고"):
        self.code, self.system, self.msg, self.level = code, system, msg, level

    def __repr__(self):
        return f"[{self.level}] {self.system} · {self.code} — {self.msg}"


def kst_now() -> dt.datetime:
    return dt.datetime.utcnow() + dt.timedelta(hours=9)


# ── HTTP 헬퍼 ────────────────────────────────────────────────────────────────
def _get_json(url: str, headers: dict, timeout: int = 30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ── ① GitHub Actions 워크플로 상태 ────────────────────────────────────────────
def _gh_runs(token: str, repo: str, wf_file: str, limit: int = 12) -> list | None:
    """워크플로 최근 실행 목록. 조회 자체가 실패하면 None(=판단 보류)."""
    url = (f"https://api.github.com/repos/{repo}/actions/workflows/{wf_file}"
           f"/runs?per_page={limit}")
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
           "User-Agent": "hero-ops-watch"}
    try:
        return _get_json(url, hdr).get("workflow_runs", [])
    except Exception as e:
        print(f"[gh] {wf_file} 조회 실패 — 판단 보류: {type(e).__name__}: {e}")
        return None


def _parse_gh_time(s: str) -> dt.datetime:
    """GitHub ISO8601(Z) → KST naive."""
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ") + dt.timedelta(hours=9)


def check_workflows(token: str, now: dt.datetime, gate: bool) -> list[Finding]:
    """워크플로별 '마지막 실행 실패' + '오늘 실행 자체가 없음'을 본다.

    ★후자가 진짜 위험이다 — 2026-06-23 빌링 차단으로 잡이 2초 만에 죽었을 때도,
      cron-job.org 가 멈췄을 때도 `if: failure()` 알림으로는 아무것도 오지 않는다.
    """
    out: list[Finding] = []
    if not token:
        return [Finding("GH_NO_TOKEN", "감시", "GITHUB_TOKEN 없음 — 워크플로 상태 점검 스킵", "정보")]

    # (파일, 표시명, 시스템, 오늘 실행이 없으면 경고할 KST 시각, 최근 성공 허용 간격[시간])
    specs = [
        ("hourly.yml", "Hourly ranking capture", "랭킹봇", None, 3),
        ("daily.yml", "Daily ranking report", "랭킹봇", 10.5, None),
        ("rank1_watch.yml", "Rank1 watch", "랭킹봇", None, None),
        ("hero_app_daily.yml", "Hero master app daily update", "마스터앱", 15.0, None),
    ]
    today = now.date()
    hour_f = now.hour + now.minute / 60

    for wf, name, system, need_by, fresh_h in specs:
        runs = _gh_runs(token, GH_REPO, wf)
        if runs is None:
            continue
        if not runs:
            out.append(Finding(f"WF_NONE_{wf}", system, f"{name} — 실행 이력이 없다", "경고"))
            continue

        last = runs[0]
        last_at = _parse_gh_time(last["created_at"])
        concl = last.get("conclusion")          # success / failure / cancelled / None(진행중)

        # (a) 마지막 실행이 실패로 끝났나 — 진행중(None)·취소는 실패로 보지 않는다.
        if concl == "failure":
            # 연속 실패 길이를 세어 심각도를 올린다.
            streak = 0
            for r in runs:
                if r.get("conclusion") is None:
                    continue
                if r.get("conclusion") == "failure":
                    streak += 1
                else:
                    break
            lvl = "심각" if streak >= 2 else "경고"
            out.append(Finding(
                f"WF_FAIL_{wf}", system,
                f"{name} 실패 — {last_at:%m/%d %H:%M} KST"
                + (f" · **{streak}회 연속**" if streak >= 2 else "")
                + f"\n  {last.get('html_url', '')}",
                lvl))

        # (b) 오늘 돌았어야 하는데 실행 자체가 없나 (외부 cron 사망 / 빌링 차단)
        if need_by is not None and (not gate or hour_f >= need_by):
            ran_today = any(_parse_gh_time(r["created_at"]).date() == today for r in runs)
            if not ran_today:
                out.append(Finding(
                    f"WF_MISSING_{wf}", system,
                    f"{name} — 오늘 실행 자체가 없다(기대 {need_by:.0f}시 이전). "
                    f"마지막 실행 {last_at:%m/%d %H:%M}. 외부 cron(cron-job.org) 중단이나 "
                    f"Actions 빌링 차단을 의심할 것.", "심각"))

        # (c) 짧은 주기(hourly)는 '최근 성공'이 끊겼는지로 본다 — 1위 알림이 여기 체이닝돼 있다.
        if fresh_h is not None:
            oks = [_parse_gh_time(r["created_at"]) for r in runs if r.get("conclusion") == "success"]
            if oks:
                gap = (now - max(oks)).total_seconds() / 3600
                if gap >= fresh_h:
                    out.append(Finding(
                        f"WF_STALE_{wf}", system,
                        f"{name} — 마지막 성공이 {gap:.1f}시간 전({max(oks):%m/%d %H:%M}). "
                        f"1위 즉시 알림이 이 워크플로에 체이닝돼 있어 같이 멈춘다.", "심각"))
            else:
                out.append(Finding(
                    f"WF_NOOK_{wf}", system,
                    f"{name} — 최근 {len(runs)}회 중 성공이 하나도 없다.", "심각"))
    return out


# ── ② 랭킹봇 산출물(Wide 탭) ─────────────────────────────────────────────────
def check_ranking_output(sheets, now: dt.datetime, gate: bool) -> list[Finding]:
    """잡은 초록불인데 실제 리포트가 안 나간 경우를 잡는다.

    근거: 2026-07-08 Sheets 읽기 429로 [전체]·[남자]가 뷰별 try/except에 잡혀 조용히 빠졌는데
          워크플로는 success 였다. Wide 적재는 발송보다 먼저 일어나므로, Wide 에 3뷰가
          다 있으면 발송까지 갔다고 볼 수 있다.
    """
    if gate and now.hour < 10:           # 09:00 발송 + 적재 여유
        return []
    target = (now.date() - dt.timedelta(days=1)).isoformat()
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=ARCHIVE_SHEET, range="'Wide'!A2:B").execute().get("values", [])
    except Exception as e:
        return [Finding("RANK_WIDE_READ", "랭킹봇",
                        f"랭킹 아카이브 Wide 탭 조회 실패 — {type(e).__name__}", "경고")]
    have = {str(r[1]).strip() for r in vals
            if len(r) > 1 and str(r[0]).strip() == target}
    missing = [v for v in RANKING_VIEWS if v not in have]
    if missing:
        return [Finding("RANK_WIDE_MISS", "랭킹봇",
                        f"{target} 일일 리포트 — Wide 적재 누락 뷰 {'·'.join(missing)} "
                        f"(워크플로는 성공이어도 그 뷰는 슬랙 발송이 안 됐을 수 있다).\n"
                        f"  복구: daily.yml 을 as_of={target} 로 workflow_dispatch.", "경고")]
    return []


# ── ③ Databricks 잡 상태 ─────────────────────────────────────────────────────
def check_dbx(now: dt.datetime, gate: bool) -> list[Finding]:
    """정기(PERIODIC) run 결과를 본다. 수동 재실행(ONE_TIME) 성공이 정기 실패를 가리지 않도록 분리."""
    host = (os.environ.get("DATABRICKS_HOST") or "").strip() or DBX_HOST_DEFAULT
    tok = (os.environ.get("DATABRICKS_PAT") or "").strip()
    if not tok:
        p = Path.home() / ".databricks_pat"
        if p.exists():
            tok = p.read_text(encoding="utf-8").strip()
    if not tok:
        return [Finding("DBX_NO_PAT", "감시",
                        "DATABRICKS_PAT 없음 — DBX 잡 상태 점검 스킵(시트 신선도만 판단).", "정보")]

    out: list[Finding] = []
    hdr = {"Authorization": f"Bearer {tok}", "User-Agent": "hero-ops-watch"}
    today = now.date()
    for jid, (system, jname) in DBX_JOBS.items():
        try:
            runs = _get_json(f"{host}/api/2.1/jobs/runs/list?job_id={jid}&limit=10",
                             hdr).get("runs", [])
        except urllib.error.HTTPError as e:
            body = e.read()[:150].decode("utf-8", "replace")
            out.append(Finding(f"DBX_API_{jid}", system,
                               f"DBX 잡 조회 실패 {e.code} — PAT 만료 의심. {body}", "경고"))
            continue
        except Exception as e:
            print(f"[dbx] {jid} 조회 실패 — 판단 보류: {type(e).__name__}: {e}")
            continue

        def _started(r):
            return dt.datetime.utcfromtimestamp(r.get("start_time", 0) / 1000) + dt.timedelta(hours=9)

        periodic = [r for r in runs if r.get("trigger") == "PERIODIC"]
        if not periodic:
            continue

        # ★진행중(RUNNING/PENDING) run 은 판정하지 않되, **그것 때문에 직전 실패를 놓치면 안 된다.**
        #   오늘 09:30 잡이 3시간 도는 동안 어제 실패가 감시에서 사라지면, 정작 아침에
        #   "어제 데이터가 왜 없지" 를 알려줄 사람이 없어진다. → 완료된 첫 run 을 기준으로 본다.
        DEAD = ("FAILED", "TIMEDOUT", "CANCELED")
        done = [r for r in periodic if r.get("state", {}).get("result_state")]
        running = [r for r in periodic if not r.get("state", {}).get("result_state")]

        if done:
            last = done[0]
            started = _started(last)
            result = last["state"]["result_state"]
            if result in DEAD:
                streak = 0
                for r in done:
                    if r["state"]["result_state"] in DEAD:
                        streak += 1
                    else:
                        break
                # 그 뒤에 수동 재실행이 성공했으면 같이 알려 오진을 막는다.
                newer_ok = any(r.get("trigger") != "PERIODIC"
                               and r.get("state", {}).get("result_state") == "SUCCESS"
                               and r.get("start_time", 0) > last.get("start_time", 0) for r in runs)
                # 취소는 사람이 눌렀을 수 있어 한 단계 낮춘다(연속이면 그래도 심각).
                lvl = "심각" if (streak >= 2 or result != "CANCELED") else "경고"
                out.append(Finding(
                    f"DBX_FAIL_{jid}", system,
                    f"DBX 잡 `{jname}` 정기 실행 {result} — {started:%m/%d %H:%M} KST"
                    + (f" · **{streak}회 연속**" if streak >= 2 else "")
                    + ("\n  (이후 수동 재실행 1건 성공 — 부분 복구됐을 수 있으나 정기 스케줄은 깨진 상태)"
                       if newer_ok else "")
                    + (f"\n  (현재 {_started(running[0]):%m/%d %H:%M} 정기 실행이 진행 중 — 곧 해소될 수 있다)"
                       if running else "")
                    + "\n  ★셀 단위 부분 고장일 수 있다. 탭별 기준일을 찍어 어디까지 살아있는지 먼저 가를 것."
                    + f"\n  {host}/jobs/{jid}/runs/{last.get('run_id')}",
                    lvl))

        # 오늘 정기 실행이 아예 없나 (스케줄 해제·일시중지·클러스터 정책 변경).
        # ★위와 독립으로 본다 — '어제 실패'와 '오늘 미실행'은 서로 다른 사고다.
        if not gate or now.hour >= 11:
            if not any(_started(r).date() == today for r in periodic):
                newest = _started(periodic[0])
                gap = (today - newest.date()).days
                out.append(Finding(
                    f"DBX_MISSING_{jid}", system,
                    f"DBX 잡 `{jname}` — 오늘 정기 실행이 없다(마지막 {newest:%m/%d %H:%M}, "
                    f"{gap}일 전). 스케줄 해제·일시중지를 확인할 것.", "심각"))
    return out


# ── ④ 시트 신선도 (잡은 SUCCESS인데 데이터가 안 들어온 경우) ──────────────────
def _tab_labels(sheets, sid: str, tabs: list[str]) -> dict:
    try:
        res = sheets.spreadsheets().values().batchGet(
            spreadsheetId=sid, ranges=[f"'{t}'!A1" for t in tabs]).execute()
    except Exception as e:
        print(f"[fresh] 라벨 조회 실패 — 판단 보류: {type(e).__name__}: {e}")
        return {}
    out = {}
    for t, r in zip(tabs, res.get("valueRanges", [])):
        v = r.get("values") or []
        out[t] = str(v[0][0]) if (v and v[0]) else ""
    return out


def _col_max(sheets, sid: str, tab: str, col: str = "A", tail: int = 800) -> str | None:
    """날짜 라벨이 없는 탭(잔여재고·입고일자별)의 기준일 = 날짜 열 최대값.

    ★전체 열을 읽으면 20만 행이라 무겁다 → 행수를 먼저 보고 **마지막 tail 행만** 읽는다.
      (원천이 dt 오름차순이라 최신일은 항상 꼬리에 있다.)
    """
    try:
        meta = sheets.spreadsheets().get(
            spreadsheetId=sid,
            fields="sheets(properties(title,gridProperties(rowCount)))").execute()
        n = next((s["properties"]["gridProperties"]["rowCount"]
                  for s in meta["sheets"] if s["properties"]["title"] == tab), None)
        if not n:
            return None
        start = max(3, n - tail)
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!{col}{start}:{col}{n}",
            valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
        got = [str(r[0]).strip() for r in vals if r and str(r[0]).strip().isdigit()]
        return max(got) if got else None
    except Exception as e:
        print(f"[fresh] {tab}.{col} 조회 실패 — 판단 보류: {type(e).__name__}: {e}")
        return None


def check_sheet_freshness(sheets, now: dt.datetime, gate: bool) -> list[Finding]:
    """DBX 잡은 09:30 시작 · ~3시간 소요 → 14시 전에는 전일 미반영이 정상이라 판정하지 않는다."""
    if gate and now.hour < 14:
        return []
    import re
    want = (now.date() - dt.timedelta(days=1)).strftime("%Y%m%d")
    asof_re = re.compile(r"(\d{8})(?!.*\d{8})", re.S)
    out: list[Finding] = []

    # 실적 탭 — A1 라벨 끝 8자리가 전일이어야 한다.
    for sid, system, tabs, code in (
        (SALES_SHEET, "마스터앱", ["YTD", "MTD", "WEEK", "DAY", "FWTD"], "SHEET_SALES"),
        (DASH_DATA_SHEET, "26FW대시보드", ["YTD", "MTD", "WEEK", "DAY"], "SHEET_DASH"),
    ):
        labels = _tab_labels(sheets, sid, tabs)
        bad = []
        for t, lab in labels.items():
            m = asof_re.search(lab)
            if m and m.group(1) != want:
                bad.append(f"{t} {m.group(1)}")
        if bad:
            out.append(Finding(
                code, system,
                f"실적 시트가 전일({want}) 기준이 아니다 — {' · '.join(bad)}. "
                f"잡이 SUCCESS 로 끝났어도 데이터는 안 들어온 상태다.", "심각"))

        # 잔여재고 — 라벨에 날짜가 없어 dt 열로 판정.
        st = _col_max(sheets, sid, "잔여재고", tail=50)
        if st and st != want:
            out.append(Finding(
                code + "_STOCK", system,
                f"잔여재고 스냅샷이 {st} — 전일({want}) 기준이 아니다.", "경고"))

    # 입고확정 — `/inbound` 보드의 원천. ★FRESH_TABS(대기 대상) 밖이라 아무도 안 지키는 구간.
    inb = _col_max(sheets, SALES_SHEET, "입고일자별")
    if inb and inb != want:
        lag = 0
        try:
            lag = (dt.datetime.strptime(want, "%Y%m%d") - dt.datetime.strptime(inb, "%Y%m%d")).days
        except ValueError:
            pass
        out.append(Finding(
            "SHEET_INBOUND", "입고",
            f"입고확정(`입고일자별`)이 {inb} 에 고착 — 전일({want}) 대비 {lag}일 지연.\n"
            f"  ★이 탭은 CI 대기(FRESH_TABS) 대상이 아니라, 늦으면 `/inbound` 화면의 "
            f"**미입고가 그대로 부풀려진다**(2026-08-10 실측 22 → 52 SKU).", "심각"))
    return out


# ── ⑤ 앱 배포(app.html) ──────────────────────────────────────────────────────
def check_app_deploy(now: dt.datetime, gate: bool) -> list[Finding]:
    """app.html 이 실제로 새로 커밋됐는지 — CI 는 성공했는데 push 가 rejected 된 사고 대비.

    근거: 2026-08-06 생성은 성공했는데 `! [rejected] main -> main` 으로 커밋이 폐기돼
          자동 갱신이 8/5 에 멈췄다(오후 수동 재생성으로 겨우 메움).
    """
    if gate and now.hour < 15:
        return []
    tok = (os.environ.get("APP_REPO_PAT") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not tok:
        return []
    hdr = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
           "User-Agent": "hero-ops-watch"}
    try:
        commits = _get_json(
            f"https://api.github.com/repos/{APP_REPO}/commits?path={APP_FILE}&per_page=1", hdr)
    except Exception as e:
        print(f"[app] 커밋 조회 실패 — 판단 보류: {type(e).__name__}: {e}")
        return []
    if not commits:
        return []
    when = commits[0]["commit"]["committer"]["date"]
    last = _parse_gh_time(when)
    gap = (now.date() - last.date()).days
    if gap >= 2:
        return [Finding("APP_STALE", "마스터앱",
                        f"`{APP_FILE}` 마지막 커밋이 {last:%m/%d %H:%M} — {gap}일째 갱신 없음. "
                        f"CI 가 성공해도 push 가 거절되면 그날 갱신이 통째로 버려진다.", "심각")]
    return []


# ── dedup 원장 ───────────────────────────────────────────────────────────────
def _ensure_watch_tab(sheets) -> bool:
    try:
        meta = sheets.spreadsheets().get(spreadsheetId=ARCHIVE_SHEET,
                                         fields="sheets.properties(title)").execute()
        titles = {s["properties"]["title"] for s in meta["sheets"]}
        if WATCH_TAB in titles:
            return True
        sheets.spreadsheets().batchUpdate(spreadsheetId=ARCHIVE_SHEET, body={"requests": [
            {"addSheet": {"properties": {"title": WATCH_TAB,
                                         "gridProperties": {"rowCount": 5000, "columnCount": 6}}}}
        ]}).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{WATCH_TAB}'!A1",
            valueInputOption="RAW", body={"values": [WATCH_HEADER]}).execute()
        return True
    except Exception as e:
        print(f"[watch] 원장 탭 준비 실패 — dedup 없이 진행: {type(e).__name__}: {e}")
        return False


def _sent_today(sheets, today: str) -> set:
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{WATCH_TAB}'!A2:B").execute().get("values", [])
    except Exception:
        return set()
    return {str(r[1]).strip() for r in vals
            if len(r) > 1 and str(r[0]).strip() == today}


def _log_sent(sheets, rows: list[list]) -> None:
    if not rows:
        return
    try:
        # ★insertDataOption=INSERT_ROWS 를 쓰면 안 된다 — 같은 파일의 다른 탭이 참조 범위를
        #   갖고 있으면 append 마다 한 칸씩 밀려 집계가 영원히 0 이 된다(2026-08-10 실사고).
        sheets.spreadsheets().values().append(
            spreadsheetId=ARCHIVE_SHEET, range=f"'{WATCH_TAB}'!A1",
            valueInputOption="RAW", insertDataOption="OVERWRITE",
            body={"values": rows}).execute()
    except Exception as e:
        print(f"[watch] 원장 기록 실패(발송은 완료): {type(e).__name__}: {e}")


# ── 본체 ─────────────────────────────────────────────────────────────────────
def run(dry_run: bool = False, force: bool = False, gate: bool = True) -> int:
    now = kst_now()
    print(f"[watch] 점검 시작 — {now:%Y-%m-%d %H:%M} KST"
          f"{' (게이트 무시)' if not gate else ''}")

    from soo.auth import build_services, get_credentials
    sheets = None
    try:
        sheets = build_services(get_credentials(ROOT / "credentials.json",
                                                ROOT / "token.json"))["sheets"]
    except Exception as e:
        print(f"[watch] 구글 인증 실패 — 시트 점검 스킵: {type(e).__name__}: {e}")

    findings: list[Finding] = []
    gh_token = (os.environ.get("GITHUB_TOKEN") or "").strip()

    # 각 점검은 독립 — 하나가 예외로 죽어도 나머지는 계속한다(감시가 통째로 침묵하지 않도록).
    steps = [
        ("워크플로", lambda: check_workflows(gh_token, now, gate)),
        ("DBX 잡", lambda: check_dbx(now, gate)),
        ("앱 배포", lambda: check_app_deploy(now, gate)),
    ]
    if sheets is not None:
        steps += [
            ("랭킹 산출물", lambda: check_ranking_output(sheets, now, gate)),
            ("시트 신선도", lambda: check_sheet_freshness(sheets, now, gate)),
        ]
    for name, fn in steps:
        try:
            got = fn()
            findings.extend(got)
            print(f"  · {name}: {len(got)}건")
        except Exception as e:
            print(f"  · {name}: 점검 자체 실패 {type(e).__name__}: {e}")
            findings.append(Finding(f"WATCH_ERR_{name}", "감시",
                                    f"{name} 점검이 예외로 실패 — {type(e).__name__}: {e}", "경고"))

    # 정보 레벨은 콘솔에만 남기고 알림에서 뺀다(PAT 미설정 등 운영자가 이미 아는 것).
    alerts = [f for f in findings if f.level != "정보"]
    for f in findings:
        print(f"    {f!r}")

    if not alerts:
        print("[watch] 이상 없음")
        return 0

    today = now.date().isoformat()
    have_ledger = (sheets is not None) and (not dry_run) and _ensure_watch_tab(sheets)
    already = _sent_today(sheets, today) if (have_ledger and not force) else set()
    fresh = [f for f in alerts if f.code not in already]
    if not fresh:
        print(f"[watch] {len(alerts)}건 모두 오늘 이미 발송함 — 스킵(--force 로 재발송)")
        return 0

    order = {"심각": 0, "경고": 1}
    fresh.sort(key=lambda f: (order.get(f.level, 9), f.system))
    head = (f"⚠️ 히어로 자동화 이상 {len(fresh)}건 ({now:%m/%d %H:%M} KST)")
    body = "\n\n".join(f"[{f.level}] {f.system} — {f.msg}" for f in fresh)
    msg = (head + "\n\n" + body)[:3500]

    if dry_run:
        print("\n===== (dry-run) 발송할 메시지 =====\n" + msg)
        return 0

    from soo.hero_ops import notify
    notify.send(msg)
    if have_ledger:
        stamp = f"{now:%H:%M}"
        _log_sent(sheets, [[today, f.code, f.level, f.system, f.msg[:400], stamp] for f in fresh])
    print(f"[watch] {len(fresh)}건 발송 완료")
    return 0


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser(description="히어로 자동화 통합 감시")
    ap.add_argument("--dry-run", action="store_true", help="발송·기록 없이 콘솔만")
    ap.add_argument("--force", action="store_true", help="오늘 이미 보낸 건도 재발송")
    ap.add_argument("--no-gate", action="store_true", help="시각 게이트 무시(진단용)")
    a = ap.parse_args()
    return run(dry_run=a.dry_run, force=a.force, gate=not a.no_gate)


if __name__ == "__main__":
    raise SystemExit(main())
