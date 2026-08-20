"""Reads interaction rows straight from the same Google Sheet n8n writes to.

This project has no local database — the sheet itself is the only record of
past calls, so the admin page's data source is a live read on every request
rather than a cached/synced copy.
"""

from __future__ import annotations

from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config.settings import settings

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_RANGE = "Sheet1"


def _client():
    info = {
        "type": "service_account",
        "client_email": settings.google_service_account_email,
        "private_key": settings.google_service_account_private_key,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def fetch_interactions() -> list[dict[str, Any]]:
    """Return every logged interaction row, newest first."""
    service = _client()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheet_id, range=_RANGE)
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        return []
    header, *data_rows = rows
    # Google's API drops trailing empty cells per row rather than padding —
    # zip() would silently misalign columns on short rows without this.
    padded_rows = [row + [""] * (len(header) - len(row)) for row in data_rows]
    interactions = [dict(zip(header, row)) for row in padded_rows]
    return list(reversed(interactions))
