# -*- coding: utf-8 -*-
"""보드 단계 지연 슬랙 알람 — 기준일이 지났는데 아직 미완료인 STY를 매일 1회 요약 발송.

앱 화면엔 D+ 배지가 뜨지만 '알람'은 실제로 구현돼 있지 않았다(앱 문구만 있었음).
여기서 생성기가 만든 보드 데이터를 그대로 읽어 요약을 만든다 — 화면과 알람이 같은 소스라 어긋나지 않는다.

수신처: 기본은 시스템 오너 DM(notify.DEFAULT_TARGET). 환경변수 NOTIFY_TARGET 으로 채널/사람 변경.
        담당자별 라우팅은 담당자→슬랙ID 매핑이 있어야 가능(현재 없음) → 우선 오너 다이제스트.
발송 조건: SLACK_BOT_TOKEN 있을 때만(로컬 실행은 조용히 스킵).
"""
from __future__ import annotations

import datetime as dt
import re

STAGE_LABEL = {
    0: "MDP", 1: "히어로 진행", 2: "매트릭스", 3: "품평회", 4: "GO-DROP",
    5: "1차 수량", 6: "컬러 확정", 7: "원단 확정", 8: "PO 전송", 9: "PO 작성",
    10: "QC APP", 11: "사후원가", 12: "판매가", 13: "입고",
}
_TARGET_RE = re.compile(r"기준\s*(\d{4}-\d{2}-\d{2})")
MAX_LINES = 12          # 슬랙 메시지가 길어지면 읽히지 않음 — 단계별 요약 + 상위만


def _overdue_days(cell: str, today: dt.date) -> int | None:
    m = _TARGET_RE.search(cell or "")
    if not m:
        return None
    try:
        return (today - dt.date.fromisoformat(m.group(1))).days
    except ValueError:
        return None


def collect(board: dict, today: dt.date, season: str) -> list[dict]:
    """{키: {'stages': [...], 'dates': [...], 'track': ...}} → 지연 항목 리스트.

    'delayed' 상태이고 기준일이 실제로 지난 것만(오탐 방지: 상태와 날짜가 둘 다 맞아야 함).
    """
    out = []
    for key, v in (board or {}).items():
        stages, dates = v.get("stages") or [], v.get("dates") or []
        for n, st in enumerate(stages):
            if st != "delayed":
                continue
            dd = _overdue_days(dates[n] if n < len(dates) else "", today)
            if dd is None or dd <= 0:
                continue
            out.append({"season": season, "key": key, "track": v.get("track", ""),
                        "stage": n, "label": STAGE_LABEL.get(n, str(n)), "days": dd})
    return out


def compose(items: list[dict], today: dt.date) -> str:
    """단계별로 묶은 요약 텍스트. 건수 많아도 12줄 안에서 끝낸다."""
    if not items:
        return ""
    by_stage: dict[tuple, list[dict]] = {}
    for it in items:
        by_stage.setdefault((it["season"], it["stage"], it["label"]), []).append(it)
    lines = [f"*히어로 보드 단계 지연* ({today.isoformat()}) — 기준일 경과·미완료 {len(items)}건"]
    for (season, _n, label), grp in sorted(by_stage.items(), key=lambda x: (-len(x[1]), x[0][1])):
        grp.sort(key=lambda g: -g["days"])
        worst = grp[0]
        tracks = ", ".join(sorted({g["track"] for g in grp if g["track"]}))
        head = f"• [{season}] {label} — {len(grp)}건 (최대 D+{worst['days']}"
        head += f", {worst['key']})" if len(grp) == 1 else f", 최장 {worst['key']})"
        if tracks:
            head += f" · {tracks}"
        lines.append(head)
        if len(lines) >= MAX_LINES:
            break
    rest = len(by_stage) - (len(lines) - 1)
    if rest > 0:
        lines.append(f"…외 {rest}개 단계")
    lines.append("_상세는 히어로 마스터앱 보드에서 확인_ https://hero-master-app.vercel.app/app.html")
    return "\n".join(lines)


def send_if_any(items: list[dict], today: dt.date) -> int:
    """지연이 있으면 발송, 없으면 아무것도 안 함(=조용한 정상)."""
    msg = compose(items, today)
    if not msg:
        print("[stage_alerts] 지연 없음 — 발송 안 함")
        return 0
    from soo.hero_ops import notify
    print(f"[stage_alerts] 지연 {len(items)}건 — 슬랙 발송 시도")
    return notify.send(msg)
