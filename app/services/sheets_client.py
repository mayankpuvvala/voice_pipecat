"""Direct Google Sheets access for the live call path — no n8n in between.

Time-critical in-call actions (availability checks, booking writes, and
interaction logging) call this module directly instead of going through an
n8n webhook mid-call. Routing a live tool call through n8n risked dead air
on a real call if Railway's free-tier n8n cold-started; a direct API call
from this same process has no such hop. n8n's role is limited to
non-time-critical jobs that read the sheet afterward (end-of-day digest,
escalation alert) — see n8n/restaurant_reception_workflow.json.

Reuses the same service account already granted Editor on the sheet for
n8n's own credential (see README) — just requesting the read/write scope
instead of admin's read-only one (app/admin/sheets_reader.py).

`google-api-python-client` is a blocking/synchronous client, not asyncio —
every public function here does a real network call and must be awaited via
`asyncio.to_thread(...)` from async call sites (see app/tools/), so it never
blocks the event loop and stalls audio on a live call.
"""

from __future__ import annotations

from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config.settings import settings

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _client():
    info = {
        "type": "service_account",
        "client_email": settings.google_service_account_email,
        "private_key": settings.google_service_account_private_key,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def append_row(sheet_name: str, row: dict[str, Any]) -> None:
    """Append one row to `sheet_name`, ordered by that sheet's own header row.

    Reads the header first rather than trusting a hardcoded column order, so
    the sheet stays the single source of truth for column layout — same
    assumption admin/sheets_reader.py already makes on the read side. Keys in
    `row` that aren't in the header are silently dropped; header columns
    missing from `row` are written blank.
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
        # RAW, not USER_ENTERED: USER_ENTERED parses each cell the way Sheets
        # parses manual keyboard entry, which silently mangles data we need
        # byte-for-byte — confirmed live, writing caller_phone="0000000000"
        # came back read as "0" (Sheets treats it as the number 0 and drops
        # the leading zeros). RAW stores every value as literal text.
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
