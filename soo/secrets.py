"""비밀값(API 키, webhook URL 등) 로더.

secrets.yaml에서 읽어와 dict로 반환. 파일이 없으면 빈 dict.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_secrets(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def require(secrets: dict, key: str) -> str:
    """필수 비밀값 가져오기. 없으면 친절한 에러."""
    val = secrets.get(key)
    if not val:
        raise RuntimeError(
            f"secrets.yaml 에 '{key}' 가 없어요. README 또는 SooBot 폴더의 secrets.yaml 확인."
        )
    return val
