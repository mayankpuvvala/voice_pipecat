"""One-off cleanup: remove calls whose Recordings row has no Transcript.

Investigation (see conversation) found these are NOT test artifacts — they
have real phone numbers, real durations, uploaded recordings, and some have
confirmed bookings attached. The empty Transcript is caused by a bug in
app/main.py: capture_transcript() only runs from the on_client_disconnected
handler, so any call that ends another way (idle timeout, pipeline error)
gets its Recordings row written before call_state["transcript"] is ever
populated.

Explicitly requested anyway: deletes the Recordings row AND every Sheet1 /
Bookings row sharing that CallSessionId, i.e. removes these calls entirely
from the dashboard — including any confirmed bookings tied to them.

Usage:
    python -m scripts.cleanup_no_transcript_calls            # dry run (default)
    python -m scripts.cleanup_no_transcript_calls --execute   # actually deletes
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from app.config.settings import settings
from app.services.sheets_client import _client

_TABS = ["Sheet1", "Bookings", "Recordings"]


def _tab_row_dicts(service, sheet_id: str, tab: str) -> list[dict[str, Any]]:
    result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=tab).execute()
    values = result.get("values", [])
    if not values:
        return []
    header, *data_rows = values
    rows = []
    for i, raw in enumerate(data_rows):
        padded = raw + [""] * (len(header) - len(raw))
        row = dict(zip(header, padded))
        row["_row_number"] = i + 2  # row 1 is the header
        rows.append(row)
    return rows


def _sheet_id_map(service, spreadsheet_id: str) -> dict[str, int]:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Actually delete the matching rows (default: dry run)"
    )
    args = parser.parse_args()

    service = _client()
    spreadsheet_id = settings.google_sheet_id
    if not spreadsheet_id:
        print("GOOGLE_SHEET_ID is not set — check .env", file=sys.stderr)
        sys.exit(1)

    tab_sheet_ids = _sheet_id_map(service, spreadsheet_id)

    tab_rows: dict[str, list[dict[str, Any]]] = {}
    for tab in _TABS:
        if tab not in tab_sheet_ids:
            print(f"[{tab}] tab not found in spreadsheet — skipping")
            continue
        tab_rows[tab] = _tab_row_dicts(service, spreadsheet_id, tab)

    recordings = tab_rows.get("Recordings", [])
    target_session_ids = {
        row["CallSessionId"]
        for row in recordings
        if not row.get("Transcript", "").strip() and row.get("CallSessionId", "")
    }
    print(f"Calls with a Recordings row but no Transcript: {len(target_session_ids)}")

    bookings_hit = [
        r for r in tab_rows.get("Bookings", []) if r.get("CallSessionId", "") in target_session_ids
    ]
    confirmed_bookings_hit = [
        r for r in bookings_hit if str(r.get("Status", "")).strip().lower() == "confirmed"
    ]
    if confirmed_bookings_hit:
        print(
            f"WARNING: {len(confirmed_bookings_hit)} of these calls have a CONFIRMED booking "
            "that will also be deleted."
        )

    total_matches = 0
    delete_requests = []
    for tab, rows in tab_rows.items():
        matches = [r for r in rows if r.get("CallSessionId", "") in target_session_ids]
        total_matches += len(matches)
        print(f"[{tab}] {len(matches)} row(s) to delete of {len(rows)} total")
        for row in matches[:5]:
            print(
                f"    row {row['_row_number']}: CallSessionId={row.get('CallSessionId', '')!r} "
                f"CallerPhone={row.get('CallerPhone', '')!r}"
            )
        if len(matches) > 5:
            print(f"    ... and {len(matches) - 5} more")

        for row in sorted(matches, key=lambda r: r["_row_number"], reverse=True):
            delete_requests.append(
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": tab_sheet_ids[tab],
                            "dimension": "ROWS",
                            "startIndex": row["_row_number"] - 1,
                            "endIndex": row["_row_number"],
                        }
                    }
                }
            )

    print(f"\nTotal rows to delete across all tabs: {total_matches}")

    if not args.execute:
        print("\nDry run only — nothing deleted. Re-run with --execute to delete these rows.")
        return

    if not delete_requests:
        print("Nothing to delete.")
        return

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": delete_requests}
    ).execute()
    print(f"Deleted {total_matches} row(s) across {_TABS}.")


if __name__ == "__main__":
    main()
