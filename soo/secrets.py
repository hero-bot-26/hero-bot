"""비밀값 로더.

GitHub Actions: 환경변수 우선 (SLACK_BOT_TOKEN, SLACK_TARGET 등)
로컬: secrets.yaml fallback
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


# 환경변수 이름 ↔ secrets.yaml 키 매핑
ENV_KEYS = {
    "slack_bot_token": "SLACK_BOT_TOKEN",
    "slack_target": "SLACK_TARGET",
    "slack_webhook_url": "SLACK_WEBHOOK_URL",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}


def load_secrets(path: Path) -> dict:
    """파일 + 환경변수 병합. 환경변수가 우선."""
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        data = {}

    for key, env_name in ENV_KEYS.items():
        env_val = os.environ.get(env_name, "").strip()
        if env_val:
            data[key] = env_val
    return data


def require(secrets: dict, key: str) -> str:
    val = secrets.get(key)
    if not val:
        raise RuntimeError(f"'{key}' 비밀값이 없어요 (환경변수 또는 secrets.yaml).")
    return val
