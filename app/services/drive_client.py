"""Google Drive uploads for call recordings.

Same service account as sheets_client.py, requesting Drive's write scope
instead. Service accounts have no browsable personal Drive of their own, so
recordings need a human-owned folder shared with the service account
(Editor) to land in — see GOOGLE_DRIVE_RECORDINGS_FOLDER_ID in
app/config/settings.py. A file created inside a shared folder inherits that
folder's sharing, so no separate per-file permission call is needed here.

`google-api-python-client` is a blocking/synchronous client, not asyncio —
`upload_recording` must be awaited via `asyncio.to_thread(...)` from async
call sites (see app/pipeline/recording.py).
"""

from __future__ import annotations

import io
import re

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config.settings import settings

_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _folder_id() -> str:
    """Extract the bare folder ID whether the env var holds that or a full
    Drive URL (e.g. https://drive.google.com/drive/folders/<id>?usp=...) —
    pasting the full share-link URL is the natural thing to do, and the
    Drive API rejects anything but the bare ID as a parent."""
    raw = settings.google_drive_recordings_folder_id
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", raw)
    return match.group(1) if match else raw


def _client():
    info = {
        "type": "service_account",
        "client_email": settings.google_service_account_email,
        "private_key": settings.google_service_account_private_key,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_recording(filename: str, wav_bytes: bytes) -> str:
    """Upload a WAV recording to the configured folder, return its Drive view link."""
    service = _client()
    media = MediaIoBaseUpload(io.BytesIO(wav_bytes), mimetype="audio/wav", resumable=False)
    file = (
        service.files()
        .create(
            body={
                "name": filename,
                "parents": [_folder_id()],
            },
            media_body=media,
            fields="id, webViewLink",
        )
        .execute()
    )
    return file.get("webViewLink", "")
