"""Reads + joins one restaurant's Sheet1/Bookings/Recordings tabs into one
row per call. Self-contained rather than importing app.admin.sheets_reader,
which would pull app/ into this container. Cached per sheet_id.
"""

from __future__ import annotations

import os
import time
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from loguru import logger

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_CONFIDENCE_RANK = {"high": 1, "medium": 2, "low": 3}
_CACHE_TTL_SECONDS = 20.0

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _client():
    info = {
        "type": "service_account",
        "client_email": os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL", ""),
        "private_key": os.environ.get("GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", ""),
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _read_rows(sheet_id: str, sheet_name: str) -> list[dict[str, Any]]:
    service = _client()
    result = (
        service.spreadsheets().values().get(spreadsheetId=sheet_id, range=sheet_name).execute()
    )
    rows = result.get("values", [])
    if not rows:
        return []
    header, *data_rows = rows
    padded_rows = [row + [""] * (len(header) - len(row)) for row in data_rows]
    return [dict(zip(header, row)) for row in padded_rows]


def _read_rows_safe(sheet_id: str, sheet_name: str) -> list[dict[str, Any]]:
    try:
        return _read_rows(sheet_id, sheet_name)
    except Exception:
        logger.exception("fetch_calls: failed to read '{}' tab for sheet {}", sheet_name, sheet_id)
        return []


def _new_call(session_id: str) -> dict[str, Any]:
    return {
        "call_session_id": session_id,
        "timestamp": "",
        "caller_name": "",
        "caller_phone": "",
        "topics": [],
        "escalated": False,
        "confidence_rank": 0,
        "needs_followup": False,
        "details": [],
        "reservation": None,
        "recording_url": "",
        "duration_secs": "",
        "transcript": "",
        "summary": "",
    }


def fetch_calls(sheet_id: str) -> list[dict[str, Any]]:
    """One row per call for this sheet, newest first. Cached per sheet_id."""
    now = time.monotonic()
    cached = _cache.get(sheet_id)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]
    calls = _fetch_calls_uncached(sheet_id)
    _cache[sheet_id] = (now, calls)
    return calls


def _fetch_calls_uncached(sheet_id: str) -> list[dict[str, Any]]:
    interactions = _read_rows_safe(sheet_id, "Sheet1")
    bookings = _read_rows_safe(sheet_id, "Bookings")
    recordings = _read_rows_safe(sheet_id, "Recordings")

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
        detail = row.get("Details", "").strip()
        if detail:
            call["details"].append(detail)
        if str(row.get("Resolved", "")).strip().lower() == "false":
            call["needs_followup"] = True
        if str(row.get("Drift", "")).strip().lower() == "true":
            call["escalated"] = True
        rank = _CONFIDENCE_RANK.get(str(row.get("CallConfidence", "")).strip().lower(), 0)
        call["confidence_rank"] = max(call["confidence_rank"], rank)

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
        # Idle-triggered whole-transcript analysis (app/pipeline/
        # idle_post_processor.py, runs in the voice-agent process, not this
        # one) — more reliable than the live per-topic self-report above,
        # but only exists once a call's actually been processed. Falls back
        # to the live-derived values above until then.
        if row.get("PostProcessedAt", "").strip():
            post_rank = _CONFIDENCE_RANK.get(str(row.get("PostConfidence", "")).strip().lower(), 0)
            if post_rank:
                call["confidence_rank"] = post_rank
            call["escalated"] = str(row.get("Escalated", "")).strip().lower() == "true"

    result = list(calls.values())
    result.sort(key=lambda c: c["timestamp"], reverse=True)
    return result
