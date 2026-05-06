"""MUSINSA STANDARD 히어로 봇 (랭킹봇) 페르소나 + 로그 + Slack 발송.

원본 SooBot/soo/persona.py 에서 RANKING_BOT 부분만 가져와서
Slack 발송을 Bot Token (chat.postMessage) 방식으로 수정한 버전.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Persona:
    name: str
    tagline: str
    slack_username: str | None = None
    slack_icon_emoji: str | None = None
    slack_icon_url: str | None = None


RANKING_BOT = Persona(
    name="MUSINSA STANDARD 히어로 봇",
    tagline="무탠다드 랭킹 추적봇",
    slack_username="MUSINSA STANDARD 히어로 봇",
    slack_icon_emoji=":superhero:",
)

# 하위 호환 — 기존 SooBot 코드가 NAME/TAGLINE/MINI_SOO 를 직접 import 하는 경우 대비
MINI_SOO = RANKING_BOT
NAME = RANKING_BOT.name
TAGLINE = RANKING_BOT.tagline


def setup_logger(log_dir: Path, dry_run: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hero_bot")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    if hasattr(sys.stdout, "buffer"):
        import io
        stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    else:
        stream = sys.stdout
    ch = logging.StreamHandler(stream)
    ch.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)

    from datetime import date
    log_file = log_dir / f"{date.today().isoformat()}.log"
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    if dry_run:
        logger.info("(DRY RUN — 실제 변경/발송 없음)")
    return logger


def greet(persona: Persona = RANKING_BOT) -> str:
    return f"안녕하세요, {persona.name}입니다 🦸  ({persona.tagline})"


def starting_task(task_name: str, persona: Persona = RANKING_BOT) -> str:
    return f"📋 {persona.name}, {task_name} 시작합니다."


def step(msg: str) -> str:
    return f"  ↳ {msg}"


def task_done_ok(summary: str) -> str:
    return f"✅ 완료. {summary}"


def task_done_skip(reason: str) -> str:
    return f"⏭️  오늘은 일 안 합니다. {reason}"


def task_failed(err: str) -> str:
    return f"❗ 죄송합니다, 중간에 막혔어요. {err}"


def send_slack(
    message: str,
    *,
    bot_token: str,
    target: str,
    persona: Persona | None = None,
    log: logging.Logger | None = None,
    blocks: list | None = None,
) -> bool:
    """Slack chat.postMessage 로 메시지 발송.

    target: 채널 ID (`C0XXX...`), 채널명 (`#general`), 또는 사용자 ID (`U0XXX...`).
    persona: 주어지면 username/icon override. (Bot 권한에 chat:write.customize 가 있어야 적용됨;
             없어도 메시지는 잘 가고 username/icon만 무시됨.)
    log: 주어지면 실패 사유를 로깅. CI 워크플로 디버깅에 필수.
    blocks: Slack Block Kit blocks (image_block 등). 주어지면 message는 fallback text로 사용.
    """
    if not bot_token or not target:
        if log:
            log.error(task_failed(
                f"Slack 발송 — token/target 누락 (token={'있음' if bot_token else '없음'}, "
                f"target={'있음' if target else '없음'})"
            ))
        return False
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        if log:
            log.error(task_failed("Slack 발송 — slack_sdk 미설치"))
        return False

    client = WebClient(token=bot_token)
    kwargs: dict = {"channel": target, "text": message, "mrkdwn": True}
    if blocks:
        kwargs["blocks"] = blocks
    if persona:
        if persona.slack_username:
            kwargs["username"] = persona.slack_username
        if persona.slack_icon_emoji:
            kwargs["icon_emoji"] = persona.slack_icon_emoji
        elif persona.slack_icon_url:
            kwargs["icon_url"] = persona.slack_icon_url
    try:
        client.chat_postMessage(**kwargs)
        return True
    except SlackApiError as e:
        if log:
            err = (e.response.get("error") if e.response else None) or str(e)
            log.error(task_failed(f"Slack API 에러 — {err}"))
        return False
    except Exception as e:
        if log:
            log.error(task_failed(f"Slack 발송 예외 — {type(e).__name__}: {e}"))
        return False
