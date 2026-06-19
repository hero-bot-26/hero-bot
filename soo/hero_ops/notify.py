"""daily 갱신 워크플로 실패/데이터 이상 시 슬랙 DM 알림.

CI에서 `python -m soo.hero_ops.notify "메시지"` 로 호출.
SLACK_BOT_TOKEN 없으면 조용히 스킵(로컬 등).
"""
from __future__ import annotations

import os
import sys

# sooyoung.moon DM (triggers.TEST_DM_SLACK_ID 와 동일)
DEFAULT_TARGET = "U09BU1F85TR"


def send(msg: str) -> int:
    tok = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not tok:
        print("SLACK_BOT_TOKEN 없음 — 알림 스킵")
        return 0
    target = os.environ.get("NOTIFY_TARGET", "").strip() or DEFAULT_TARGET
    try:
        from soo import persona
        ts = persona.send_slack(msg, bot_token=tok, target=target, persona=persona.RANKING_BOT)
        print("슬랙 알림 발송" if ts else "슬랙 알림 실패(응답 없음)")
    except Exception as e:
        print(f"슬랙 알림 예외: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    m = " ".join(sys.argv[1:]).strip() or (
        "⚠️ 히어로 마스터앱 daily 갱신 워크플로 실패 — GitHub Actions 로그 확인 필요.\n"
        "단골 원인: GOOGLE_OAUTH_TOKEN 만료(서비스 계정 전환 권장) / 시트 권한 / 시트 구조 변경."
    )
    raise SystemExit(send(m))
