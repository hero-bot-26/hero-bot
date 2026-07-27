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

# 서비스 계정은 sheets/drive만 (gmail/slides는 도메인 위임 없으면 호출 불가 — 생성기는 미사용).
_SA_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _service_account_credentials():
    """서비스 계정 자격증명 — 있으면 OAuth보다 우선.
    refresh_token 만료가 없어 daily CI가 토큰 만료로 멈추는 문제를 근본 해결한다.
    env GOOGLE_SA_JSON(JSON 문자열) 또는 hero_bot/service_account.json 파일에서 로드.
    대상 시트들을 이 SA 이메일(client_email)에 '뷰어'로 공유해야 읽을 수 있다."""
    from google.oauth2 import service_account
    raw = os.environ.get("GOOGLE_SA_JSON", "").strip()
    info = None
    if raw:
        try:
            info = json.loads(raw)
        except Exception:
            info = None
    else:
        p = Path(__file__).resolve().parent.parent / "service_account.json"
        if p.exists():
            try:
                info = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                info = None
    if not info:
        return None
    try:
        return service_account.Credentials.from_service_account_info(info, scopes=_SA_SCOPES)
    except Exception:
        return None


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
    # 서비스 계정이 있으면 최우선 (토큰 만료 없음). 없으면 기존 OAuth 사용자 토큰 플로우.
    sa = _service_account_credentials()
    if sa is not None:
        return sa

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


def _install_http_retry(num_retries: int = 6) -> None:
    """Sheets 429(분당 60읽기 초과)·5xx 일시 오류를 지수 백오프로 자동 재시도.

    ★2026-07-27: daily CI가 429 연쇄로 발매/DASHBOARD/PMKT 소스를 통째로 놓쳐
    IMC 발매 107건이 0으로 덮인 사고. googleapiclient는 execute(num_retries=N)을
    줘야만 재시도(429·5xx 대상)하므로 기본값을 주입해 모든 호출에 적용한다.
    (분당 쿼터라 수십 초 대기면 대개 회복 — 실패 시 기존 예외 경로 그대로.)
    """
    try:
        from googleapiclient import http as _ghttp
    except ImportError:
        return
    if getattr(_ghttp.HttpRequest, "_soo_retry_installed", False):
        return
    _orig_execute = _ghttp.HttpRequest.execute

    def _execute(self, http=None, num_retries=num_retries):
        return _orig_execute(self, http=http, num_retries=num_retries)

    _ghttp.HttpRequest.execute = _execute
    _ghttp.HttpRequest._soo_retry_installed = True


def build_services(creds: Credentials) -> dict:
    _install_http_retry()
    return {
        "drive": build("drive", "v3", credentials=creds, cache_discovery=False),
        "slides": build("slides", "v1", credentials=creds, cache_discovery=False),
        "gmail": build("gmail", "v1", credentials=creds, cache_discovery=False),
        "sheets": build("sheets", "v4", credentials=creds, cache_discovery=False),
    }
