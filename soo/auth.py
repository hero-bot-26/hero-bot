"""Google OAuth — Drive / Slides / Gmail 권한 받아오기.

최초 1회: 브라우저가 열려 사용자가 본인 Google 계정으로 동의.
이후: token.json에 저장된 refresh_token으로 자동 갱신.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive",              # 파일 사본/메타데이터 수정
    "https://www.googleapis.com/auth/presentations",      # Slides 수정
    "https://www.googleapis.com/auth/gmail.compose",      # Gmail Drafts 작성 (보내진 않음)
    "https://www.googleapis.com/auth/spreadsheets",       # Sheets 읽기+쓰기 (대시보드 + 랭킹 archive)
]


def _scope_mismatch(creds: Credentials | None) -> bool:
    """기존 토큰이 SCOPES 보다 부족한 권한이면 True (재인증 필요)."""
    if creds is None:
        return False
    have = set(creds.scopes or [])
    need = set(SCOPES)
    return not need.issubset(have)


def get_credentials(
    credentials_path: Path,
    token_path: Path,
) -> Credentials:
    """OAuth credentials를 로드 또는 새로 발급.

    스코프가 추가/변경된 경우 자동으로 token.json 폐기 후 재인증.
    """
    creds: Credentials | None = None

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None  # 손상된 토큰

    # 스코프가 부족하면 강제 재발급
    if _scope_mismatch(creds):
        token_path.unlink(missing_ok=True)
        creds = None

    # 만료된 경우 refresh 시도. 실패하면 (스코프 변경/거부 등) 토큰 폐기.
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            token_path.unlink(missing_ok=True)
            creds = None

    # 토큰이 없거나 invalid면 브라우저 플로우 시작
    if not creds or not creds.valid:
        if not credentials_path.exists():
            raise FileNotFoundError(
                f"{credentials_path} 가 없어요. Google Cloud Console에서 OAuth 클라이언트 만들고 "
                f"받은 credentials.json을 이 경로에 두세요. (README.md 참조)"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        # 로컬 임시 서버를 띄워 브라우저 콜백 받음 (포트 0 = 자동 할당)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_services(creds: Credentials) -> dict:
    """4개 API 서비스 객체를 한 번에 빌드."""
    return {
        "drive": build("drive", "v3", credentials=creds, cache_discovery=False),
        "slides": build("slides", "v1", credentials=creds, cache_discovery=False),
        "gmail": build("gmail", "v1", credentials=creds, cache_discovery=False),
        "sheets": build("sheets", "v4", credentials=creds, cache_discovery=False),
    }
