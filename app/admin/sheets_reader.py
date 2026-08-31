"""Reads call data straight from the same Google Sheet the live-call tools
write to.

This project has no local database — the sheet itself is the only record of
past calls, so the admin page's data source is a live read on every request
rather than a cached/synced copy. Read-only scope, deliberately separate from
app.services.sheets_client's read+write client used by the live-call tools.
"""

from __future__ import annotations

from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config.settings import settings

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

_CONFIDENCE_RANK = {"high": 1, "medium": 2, "low": 3}


def _client():
    info = {
        "type": "service_account",
        "client_email": settings.google_service_account_email,
        "private_key": settings.google_service_account_private_key,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def read_rows(sheet_name: str) -> list[dict[str, Any]]:
    """Return every row in `sheet_name` as header-keyed dicts, sheet order
    (oldest first)."""
    service = _client()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheet_id, range=sheet_name)
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        return []
    header, *data_rows = rows
    # Google's API drops trailing empty cells per row rather than padding —
    # zip() would silently misalign columns on short rows without this.
    padded_rows = [row + [""] * (len(header) - len(row)) for row in data_rows]
    return [dict(zip(header, row)) for row in padded_rows]


def _new_call(session_id: str) -> dict[str, Any]:
    return {
        "call_session_id": session_id,
        "timestamp": "",
        "caller_name": "",
        "caller_phone": "",
        "topics": [],
        "escalated": False,
        "confidence_rank": 0,
        "reservation": None,
        "recording_url": "",
        "duration_secs": "",
        "transcript": "",
        "summary": "",
    }


def fetch_calls() -> list[dict[str, Any]]:
    """Join Sheet1 (per-topic interactions), Bookings, and Recordings into
    one row per call, keyed by CallSessionId — newest first.

    Rows that predate the CallSessionId column (blank id) all collapse into
    a single "" bucket and will look like one jumbled call; that's a known
    limitation for a handful of legacy rows, not worth special-casing.
    """
    interactions = read_rows("Sheet1")
    bookings = read_rows("Bookings")
    recordings = read_rows("Recordings")

    calls: dict[str, dict[str, Any]] = {}

    def get_call(session_id: str) -> dict[str, Any]:
        return calls.setdefault(session_id, _new_call(session_id))

    for row in interactions:
        call = get_call(row.get("CallSessionId", ""))
        ts = row.get("Timestamp", "")
        if ts and (not call["timestamp"] or ts < call["timestamp"]):
            call["timestamp"] = ts
        if row.get("CallerName") and not call["caller_name"]:
            call["caller_name"] = row["CallerName"]
        if row.get("CallerPhone") and not call["caller_phone"]:
            call["caller_phone"] = row["CallerPhone"]
        topic = row.get("Topic", "").strip()
        if topic:
            call["topics"].append(topic)
        if str(row.get("Drift", "")).strip().lower() == "true":
            call["escalated"] = True
        rank = _CONFIDENCE_RANK.get(str(row.get("CallConfidence", "")).strip().lower(), 0)
        if rank > call["confidence_rank"]:
            call["confidence_rank"] = rank

    for row in bookings:
        call = get_call(row.get("CallSessionId", ""))
        if row.get("CallerName") and not call["caller_name"]:
            call["caller_name"] = row["CallerName"]
        if row.get("CallerPhone") and not call["caller_phone"]:
            call["caller_phone"] = row["CallerPhone"]
        if str(row.get("Status", "")).strip().lower() == "confirmed":
            call["reservation"] = {
                "guests": row.get("GuestsCount", ""),
                "date": row.get("Date", ""),
                "time": row.get("Time", ""),
            }

    for row in recordings:
        call = get_call(row.get("CallSessionId", ""))
        if row.get("CallerPhone") and not call["caller_phone"]:
            call["caller_phone"] = row["CallerPhone"]
        ts = row.get("Timestamp", "")
        if ts and not call["timestamp"]:
            call["timestamp"] = ts
        call["recording_url"] = row.get("RecordingURL", "")
        call["duration_secs"] = row.get("DurationSecs", "")
        call["transcript"] = row.get("Transcript", "")
        call["summary"] = row.get("Summary", "")

    result = list(calls.values())
    result.sort(key=lambda c: c["timestamp"], reverse=True)
    return result
