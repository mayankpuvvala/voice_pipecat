"""Direct Google Sheets access for the live call path — no n8n in between,
avoiding the cold-start dead air a webhook hop risked mid-call.

`google-api-python-client` is blocking, not asyncio — every public
function here must be awaited via `asyncio.to_thread(...)` from call sites
so it never stalls audio on a live call.
"""

from __future__ import annotations

import threading
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config.settings import settings

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_credentials = None
# googleapiclient's built Resource wraps one httplib2.Http connection, which
# isn't safe to share across threads (confirmed live: real SSL errors under
# concurrent writes when this was a single shared service). But a Resource
# is also expensive to throw away -- build() dynamically constructs it from
# the API's discovery doc into a web of bound methods that reference the
# Resource itself, forming reference cycles only the cyclic GC can reclaim
# (confirmed live 2026-09-30: a background job that called this module
# directly on the event loop thread, once a minute, forever, left ~10k
# unreachable-until-collected objects per call; production's low, sparse
# call volume didn't trigger automatic gen-2 collection often enough to
# keep up, and memory climbed to the container's OOM limit within minutes
# -- see TROUBLESHOOTING.md. That job is gone now, but every remaining
# caller here goes through asyncio.to_thread, i.e. Python's default
# ThreadPoolExecutor, so the same churn-per-call risk still applies). A
# thread-local cache gets both: every call from the same pool thread
# reuses one Resource (no repeated cycle-churn), while different threads
# still never share a connection -- and since the pool is small and
# reused, not one-thread-per-call, this stays a small, fixed number of
# cached Resource objects.
_thread_local = threading.local()


def _client():
    global _credentials
    if _credentials is None:
        info = {
            "type": "service_account",
            "client_email": settings.google_service_account_email,
            "private_key": settings.google_service_account_private_key,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        _credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    if not hasattr(_thread_local, "service"):
        _thread_local.service = build("sheets", "v4", credentials=_credentials, cache_discovery=False)
    return _thread_local.service


def append_row(sheet_name: str, row: dict[str, Any]) -> None:
    """Append one row to `sheet_name`, ordered by that sheet's own header
    row (the single source of truth for column layout). Keys in `row` not
    in the header are dropped; missing header columns are written blank.
    """
    service = _client()
    header_result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheet_id, range=f"{sheet_name}!1:1")
        .execute()
    )
    header = header_result.get("values", [[]])
    header = header[0] if header else []
    if not header:
        raise RuntimeError(
            f"Sheet tab '{sheet_name}' has no header row — add one before writing rows"
        )

    values = [str(row.get(col, "")) for col in header]
    service.spreadsheets().values().append(
        spreadsheetId=settings.google_sheet_id,
        range=f"{sheet_name}!A1",
        # RAW, not USER_ENTERED: the latter parses cells like manual keyboard
        # entry, silently mangling data we need byte-for-byte — confirmed
        # live, caller_phone="0000000000" came back read as "0".
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()


def read_rows(sheet_name: str) -> list[dict[str, Any]]:
    """Return every row in `sheet_name` as header-keyed dicts."""
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
    padded_rows = [r + [""] * (len(header) - len(r)) for r in data_rows]
    return [dict(zip(header, r)) for r in padded_rows]


def _column_letter(index: int) -> str:
    """0-indexed column number -> spreadsheet column letter (0 -> A, 25 ->
    Z, 26 -> AA)."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def update_cells(sheet_name: str, row_number: int, updates: dict[str, Any]) -> None:
    """Update specific columns of one existing row. `row_number` is the
    sheet's own 1-indexed row number (row 1 is the header, so the first data
    row is 2) — callers that got the row via read_rows() can recover this as
    `index_in_read_rows_result + 2`.

    Same "header is the single source of truth for column layout" contract
    as append_row: a key in `updates` that isn't in the sheet's header row is
    silently skipped rather than raising, so callers can ask for optional
    columns (e.g. a post-processing column a tab hasn't had added yet)
    without crashing.
    """
    service = _client()
    header_result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheet_id, range=f"{sheet_name}!1:1")
        .execute()
    )
    header = header_result.get("values", [[]])
    header = header[0] if header else []

    data = []
    for col, value in updates.items():
        if col not in header:
            continue
        col_letter = _column_letter(header.index(col))
        data.append({"range": f"{sheet_name}!{col_letter}{row_number}", "values": [[str(value)]]})
    if not data:
        return

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=settings.google_sheet_id,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()
