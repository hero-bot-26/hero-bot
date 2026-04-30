"""Google OAuth — refresh_token 기반 자동 갱신.

GitHub Actions: 환경변수 GOOGLE_OAUTH_CREDENTIALS / GOOGLE_OAUTH_TOKEN 우선
로컬: 파일(credentials.json / token.json) 사용
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _load_token_from_env_or_file(token_path: Path) -> Credentials | None:
    raw = os.environ.get("GOOGLE_OAUTH_TOKEN", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            return Credentials.from_authorized_user_info(data, SCOPES)
        except Exception:
            return None
    if token_path.exists():
        try:
            return Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            return None
    return None


def get_credentials(
    credentials_path: Path,
    token_path: Path,
) -> Credentials:
    creds = _load_token_from_env_or_file(token_path)

    # 만료된 경우 refresh 시도
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # 파일이 있는 환경(로컬)에서는 갱신된 토큰 저장. CI에서는 token_path가 없을 수 있음.
            if token_path.parent.exists():
                try:
                    token_path.write_text(creds.to_json(), encoding="utf-8")
                except Exception:
                    pass
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    # CI 환경(GOOGLE_OAUTH_TOKEN 있는데 invalid)이면 브라우저 플로우 못 함 → 에러
    if os.environ.get("GOOGLE_OAUTH_TOKEN"):
        raise RuntimeError(
            "GOOGLE_OAUTH_TOKEN 환경변수가 있는데 인증에 실패했어요. "
            "토큰이 폐기됐거나 손상됐을 수 있습니다. 로컬에서 새 token.json을 발급받아 "
            "GitHub Secret을 갱신해주세요."
        )

    # 로컬 fallback — 브라우저 플로우
    if not credentials_path.exists():
        raise FileNotFoundError(
            f"{credentials_path} 가 없어요. Google Cloud Console에서 OAuth 클라이언트 만들고 "
            f"받은 credentials.json을 이 경로에 두세요."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def build_services(creds: Credentials) -> dict:
    return {
        "drive": build("drive", "v3", credentials=creds, cache_discovery=False),
        "slides": build("slides", "v1", credentials=creds, cache_discovery=False),
        "gmail": build("gmail", "v1", credentials=creds, cache_discovery=False),
        "sheets": build("sheets", "v4", credentials=creds, cache_discovery=False),
    }
