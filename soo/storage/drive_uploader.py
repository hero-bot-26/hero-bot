"""Google Drive에 PNG 업로드 + Slack image_block에서 임베드 가능한 URL 발급.

URL은 lh3.googleusercontent.com/d/{file_id} 형태 — Slack이 raw image로 인식.
"""

from __future__ import annotations

import io
from typing import Any

from googleapiclient.http import MediaIoBaseUpload


def upload_png(
    drive_service: Any,
    folder_id: str,
    filename: str,
    image_bytes: bytes,
) -> tuple[str, str]:
    """폴더에 PNG 업로드 + anyone-readable 권한 부여. (image_url, file_id) 반환.

    image_url: lh3.googleusercontent.com/d/{file_id} — Slack image_block에서 직접 임베드 가능.
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
    ).execute()
    file_id = file["id"]

    # anyone with link can view → Slack이 fetch 가능
    drive_service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        fields="id",
    ).execute()

    image_url = f"https://lh3.googleusercontent.com/d/{file_id}"
    return image_url, file_id


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
    ).execute()
    return folder["id"]
