"""One-off cleanup: remove eval/test rows from the live Google Sheet.

Eval scenarios (eval_scenarios/*.yaml) script the caller as "ZZ-EVALTEST
<name>" precisely so these rows are easy to find and delete afterward (see
README.md); ad hoc stress testing has used "ZZ-STRESSTEST" directly as the
CallSessionId. Both are instances of a "ZZ-" prefix convention for marking
synthetic calls, matched generically here (not as two hardcoded strings) so
this also catches any future ZZ-<whatever> test convention. Nothing ever
automated the deletion, so these piled up in Sheet1/Bookings/Recordings and
inflated the admin dashboard's call counts and charts — see also the
matching runtime filter in admin_service/sheets_reader.py and
app/admin/sheets_reader.py, which now excludes these from the dashboard
going forward regardless of whether this script has been run.

Usage:
    python -m scripts.cleanup_eval_test_rows            # dry run (default)
    python -m scripts.cleanup_eval_test_rows --execute   # actually deletes

Run from the repo root so `app.*` imports resolve and .env is picked up.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from app.config.settings import settings
from app.services.sheets_client import _client

_TABS = ["Sheet1", "Bookings", "Recordings"]
_TEST_PREFIX = "ZZ-"


def _is_test_row(row: dict[str, Any]) -> bool:
    return (
        row.get("CallerName", "").strip().upper().startswith(_TEST_PREFIX)
        or row.get("CallerPhone", "").strip().upper().startswith(_TEST_PREFIX)
        or row.get("CallSessionId", "").strip().upper().startswith(_TEST_PREFIX)
    )


def _tab_row_dicts(service, sheet_id: str, tab: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (header, rows) — rows are header-keyed dicts plus a
    '_row_number' (1-indexed, matching the sheet's own row numbering)."""
    result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=tab).execute()
    values = result.get("values", [])
    if not values:
        return [], []
    header, *data_rows = values
    rows = []
    for i, raw in enumerate(data_rows):
        padded = raw + [""] * (len(header) - len(raw))
        row = dict(zip(header, padded))
        row["_row_number"] = i + 2  # row 1 is the header
        rows.append(row)
    return header, rows


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
        _header, rows = _tab_row_dicts(service, spreadsheet_id, tab)
        tab_rows[tab] = rows

    # A row counts as test data if it carries the marker itself, OR if it
    # shares a CallSessionId with a row that does elsewhere (e.g. a
    # Recordings row for a test call that doesn't itself repeat the
    # marker) — otherwise that row would be orphaned and still show up as
    # a nameless "real" call on the dashboard.
    test_session_ids = {
        row["CallSessionId"]
        for rows in tab_rows.values()
        for row in rows
        if _is_test_row(row) and row.get("CallSessionId", "")
    }

    total_matches = 0
    delete_requests = []
    for tab, rows in tab_rows.items():
        matches = [
            r
            for r in rows
            if _is_test_row(r) or r.get("CallSessionId", "") in test_session_ids
        ]
        total_matches += len(matches)
        print(f"[{tab}] {len(matches)} test row(s) of {len(rows)} total")
        for row in matches[:5]:
            print(
                f"    row {row['_row_number']}: "
                f"CallerName={row.get('CallerName', '')!r} "
                f"CallerPhone={row.get('CallerPhone', '')!r} "
                f"CallSessionId={row.get('CallSessionId', '')!r}"
            )
        if len(matches) > 5:
            print(f"    ... and {len(matches) - 5} more")

        # Delete highest row numbers first within this tab so earlier
        # deletions in the same batchUpdate call don't shift the indices of
        # rows still queued for deletion below them.
        for row in sorted(matches, key=lambda r: r["_row_number"], reverse=True):
            delete_requests.append(
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": tab_sheet_ids[tab],
                            "dimension": "ROWS",
                            "startIndex": row["_row_number"] - 1,  # 0-indexed
                            "endIndex": row["_row_number"],
                        }
                    }
                }
            )

    print(f"\nTotal matching rows: {total_matches}")

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
