"""Google Drive uploads for call recordings, authorized as an actual human
Google account (OAuth) rather than a service account — service accounts
have had zero Drive storage quota since 2021, so they can never own a file.

`drive.file` scope only grants access to what this OAuth grant itself
created. One-time setup: see drive_oauth_setup.py.

`google-api-python-client` is blocking, not asyncio — `upload_recording`
must be awaited via `asyncio.to_thread(...)` from call sites.
"""

from __future__ import annotations

import io

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config.settings import settings

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_FOLDER_NAME = "Call Recordings"

# Cached for the life of the process — avoids a Drive search on every single
# upload; a fresh process just re-finds the same folder by name (idempotent).
_folder_id_cache: str | None = None


def _credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=settings.google_oauth_refresh_token,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def _client():
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def _get_or_create_folder(service) -> str:
    global _folder_id_cache
    if _folder_id_cache:
        return _folder_id_cache

    query = (
        f"name = '{_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    results = service.files().list(q=query, fields="files(id)", pageSize=1).execute()
    existing = results.get("files", [])
    if existing:
        _folder_id_cache = existing[0]["id"]
        return _folder_id_cache

    folder = (
        service.files()
        .create(
            body={"name": _FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        )
        .execute()
    )
    _folder_id_cache = folder["id"]
    return _folder_id_cache


def upload_recording(filename: str, wav_bytes: bytes) -> str:
    """Upload a WAV recording to a "Call Recordings" folder in the
    authorized account's own Drive (created on first use), return its
    Drive view link."""
    service = _client()
    folder_id = _get_or_create_folder(service)
    media = MediaIoBaseUpload(io.BytesIO(wav_bytes), mimetype="audio/wav", resumable=False)
    file = (
        service.files()
        .create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id, webViewLink",
        )
        .execute()
    )
    return file.get("webViewLink", "")
