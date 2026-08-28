"""Google Drive uploads for call recordings, authorized as an actual human
Google account (OAuth) rather than a service account.

Service accounts have had zero Drive storage quota since 2021 — confirmed
live via storageQuotaExceeded on an actual upload attempt, not just docs —
so a service account can never own a file in a regular Drive no matter what
a folder shares with it. Uploading as a real account uses that account's own
quota instead, works on a plain free Gmail account, no Workspace needed.

`drive.file` scope (not full `drive`) — narrowest scope that still lets this
create and later reuse its own "Call Recordings" folder; it doesn't grant
access to anything the user didn't create through this same OAuth grant.

One-time setup (see also drive_oauth_setup.py):
1. In the same GCP project as the service account, configure the OAuth
   consent screen if not already done (App name, your email as test user —
   Testing publishing status is fine, only your own account needs to grant
   consent) and create an OAuth Client ID of type "Desktop app".
2. Set GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET.
3. Run `python -m app.services.drive_oauth_setup` — opens a browser for a
   one-time consent grant, prints a refresh token. Set that as
   GOOGLE_OAUTH_REFRESH_TOKEN. After this, uploads are fully unattended —
   the refresh token doesn't expire under normal use.

`google-api-python-client` is a blocking/synchronous client, not asyncio —
`upload_recording` must be awaited via `asyncio.to_thread(...)` from async
call sites (see app/pipeline/recording.py).
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
