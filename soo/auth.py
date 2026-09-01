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


def _install_http_retry(num_retries: int = 6, dns_tries: int = 5,
                        dns_max_sleep: float = 60.0) -> None:
    """Sheets 429(분당 60읽기 초과)·5xx 일시 오류를 지수 백오프로 자동 재시도.

    ★2026-07-27: daily CI가 429 연쇄로 발매/DASHBOARD/PMKT 소스를 통째로 놓쳐
    IMC 발매 107건이 0으로 덮인 사고. googleapiclient는 execute(num_retries=N)을
    줘야만 재시도(429·5xx 대상)하므로 기본값을 주입해 모든 호출에 적용한다.
    (분당 쿼터라 수십 초 대기면 대개 회복 — 실패 시 기존 예외 경로 그대로.)

    ★2026-09-01: 사내망(VDI)에서 DNS 가 통째로 빠지면
    `httplib2.error.ServerNotFoundError: Unable to find the server at
    sheets.googleapis.com` 로 죽는다. googleapiclient 의 내부 재시도는 이걸 잡긴
    하지만 백오프 창이 ~1분이라 그보다 긴 끊김은 못 버틴다(실제로 밟음).
    → **이름 해석 실패만** 바깥에서 한 겹 더 재시도한다(최대 ~2분).
      DNS 실패는 요청이 **아예 나가지 않은** 상태라 재시도해도 쓰기가 중복되지
      않는다. 연결 리셋(WinError 10054)처럼 '나갔는지 모르는' 예외는 여기서
      다시 던지지 않는다 — 배치 쓰기가 두 번 적용될 수 있어서다.
    """
    try:
        from googleapiclient import http as _ghttp
    except ImportError:
        return
    if getattr(_ghttp.HttpRequest, "_soo_retry_installed", False):
        return

    import random
    import socket
    import sys
    import time
    try:
        from httplib2.error import ServerNotFoundError
    except ImportError:  # httplib2 구버전
        from httplib2 import ServerNotFoundError

    _NAME_ERRORS = (ServerNotFoundError, socket.gaierror)
    _orig_execute = _ghttp.HttpRequest.execute

    def _execute(self, http=None, num_retries=num_retries):
        last = None
        for attempt in range(dns_tries):
            try:
                return _orig_execute(self, http=http, num_retries=num_retries)
            except _NAME_ERRORS as exc:
                last = exc
                if attempt == dns_tries - 1:
                    break
                delay = min(dns_max_sleep, 3.0 * (2 ** attempt)) + random.uniform(0, 1.0)
                sys.stderr.write(
                    f"[retry] DNS 해석 실패({type(exc).__name__}) — "
                    f"{delay:.0f}s 후 재시도 {attempt + 1}/{dns_tries - 1}\n")
                sys.stderr.flush()
                time.sleep(delay)
        raise last

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
