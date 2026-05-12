"""Google Drive에 PNG 업로드 (archive 용도) + 다운로드 헬퍼.

무신사 Workspace 정책상 anyone-with-link 외부 공개가 막혀 있어 (publishOutNotPermitted),
Slack 미리보기는 daily가 file_id로 PNG를 다운받아 files_upload_v2로 채널에 직접 업로드.
Drive는 archive 역할.

⚠️ Shared Drive 호환: 모든 files API 호출에 supportsAllDrives=True 명시.
"""

from __future__ import annotations

import io
from typing import Any

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload


def upload_png(
    drive_service: Any,
    folder_id: str,
    filename: str,
    image_bytes: bytes,
) -> tuple[str, str]:
    """폴더에 PNG 업로드. (drive_view_url, file_id) 반환.

    drive_view_url: 도메인 내 사용자가 클릭 시 Drive에서 열림. 슬랙 미리보기엔 안 씀.
    """
    metadata = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(
        io.BytesIO(image_bytes),
        mimetype="image/png",
        resumable=False,
    )
    file = drive_service.files().create(
        body=metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    file_id = file["id"]
    return f"https://drive.google.com/file/d/{file_id}/view", file_id


def download_png(drive_service: Any, file_id: str) -> bytes:
    """file_id로 Drive PNG 다운로드. daily가 슬랙 업로드 직전 호출."""
    buf = io.BytesIO()
    request = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def ensure_subfolder(drive_service: Any, parent_id: str, name: str) -> str:
    """parent 안에 name 폴더가 없으면 만들고 ID 반환. 있으면 첫 번째 매칭 ID."""
    safe_name = name.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"'{parent_id}' in parents and trashed = false"
    )
    resp = drive_service.files().list(
        q=query,
        fields="files(id,name)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]

    folder = drive_service.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        },
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]
