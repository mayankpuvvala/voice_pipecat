"""One-off migration + backfill: add LLMProvider/STTProvider/TTSProvider/
ProvidersBackfilled columns to the Recordings tab and fill them in for calls
logged before app.pipeline.recording started capturing this per-call (see
app/main.py's _describe_llm/describe_stt/describe_tts wiring).

Backfill values for pre-existing rows are NOT guesses -- they're what every
call before this feature actually used, confirmed from git history:
  - stt_provider/tts_provider only became configurable per-restaurant in
    commit 962c53d ("Add Deepgram as an alternative STT vendor, per-restaurant
    STT/TTS provider factories"); before that AND still today, neither
    restaurant config overrides the Restaurant dataclass's own defaults
    (stt_provider="sarvam", tts_provider="rumik" -- see
    app/config/restaurants/__init__.py), so every historical call used Sarvam
    saaras:v3 STT and Rumik mulberry TTS.
  - Groq only exists behind GROQ_ENABLED, which defaults to (and has always
    defaulted to) "false" -- so every historical call went through OpenAI.
    The exact OpenAI model per call isn't recoverable (OPENAI_MODEL is an env
    var, not tracked in git, and could have changed over deployments), so
    that one field is backfilled as "unrecorded" rather than asserting today's
    model applied retroactively.

Backfilled rows get ProvidersBackfilled=true so the admin dashboards' "⋮"
provider dialog can flag them as inferred rather than captured live.

Usage:
    python -m scripts.backfill_provider_columns            # dry run (default)
    python -m scripts.backfill_provider_columns --execute   # actually writes

Run from the repo root so `app.*` imports resolve and .env is picked up.
"""

from __future__ import annotations

import argparse
import sys

from app.config.settings import settings
from app.services.sheets_client import _client, _column_letter

_SHEET_NAME = "Recordings"
_NEW_COLUMNS = ["LLMProvider", "STTProvider", "TTSProvider", "ProvidersBackfilled"]

# See module docstring for why these three (and only these three) are safe
# to assert for every row that predates live capture.
_BACKFILL_STT = "Sarvam (saaras:v3)"
_BACKFILL_TTS = "Rumik (mulberry)"
_BACKFILL_LLM = "OpenAI (model unrecorded — pre-tracking)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Actually write the header + backfilled cells (default: dry run)"
    )
    args = parser.parse_args()

    spreadsheet_id = settings.google_sheet_id
    if not spreadsheet_id:
        print("GOOGLE_SHEET_ID is not set — check .env", file=sys.stderr)
        sys.exit(1)

    service = _client()
    header_result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{_SHEET_NAME}!1:1")
        .execute()
    )
    header = header_result.get("values", [[]])
    header = header[0] if header else []
    if not header:
        print(f"'{_SHEET_NAME}' tab has no header row — nothing to migrate", file=sys.stderr)
        sys.exit(1)

    missing_columns = [c for c in _NEW_COLUMNS if c not in header]
    new_header = header + missing_columns
    print(f"Current header ({len(header)} cols): {header}")
    if missing_columns:
        print(f"Will append columns: {missing_columns}")
    else:
        print("All provider columns already present in the header.")

    data_result = (
        service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=_SHEET_NAME).execute()
    )
    all_rows = data_result.get("values", [])
    data_rows = all_rows[1:] if all_rows else []
    padded_rows = [r + [""] * (len(header) - len(r)) for r in data_rows]
    dict_rows = [dict(zip(header, r)) for r in padded_rows]

    # Only backfill rows that don't already carry a value (idempotent — safe
    # to re-run after new live-captured rows exist alongside old blank ones).
    pending = [
        (i + 2, row)  # 1-indexed sheet row number; row 1 is the header
        for i, row in enumerate(dict_rows)
        if not row.get("LLMProvider", "").strip() and not row.get("STTProvider", "").strip()
    ]
    print(f"{len(pending)} of {len(dict_rows)} row(s) need backfilling")

    if not args.execute:
        print("\nDry run only — nothing written. Re-run with --execute to apply.")
        return

    if missing_columns:
        start_col = _column_letter(len(header))
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{_SHEET_NAME}!{start_col}1",
            valueInputOption="RAW",
            body={"values": [missing_columns]},
        ).execute()
        print(f"Header updated: {new_header}")

    if pending:
        col_index = {col: new_header.index(col) for col in _NEW_COLUMNS}
        data = []
        for row_number, _row in pending:
            for col, value in (
                ("LLMProvider", _BACKFILL_LLM),
                ("STTProvider", _BACKFILL_STT),
                ("TTSProvider", _BACKFILL_TTS),
                ("ProvidersBackfilled", "true"),
            ):
                col_letter = _column_letter(col_index[col])
                data.append({"range": f"{_SHEET_NAME}!{col_letter}{row_number}", "values": [[value]]})
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        print(f"Backfilled {len(pending)} row(s).")
    else:
        print("Nothing to backfill.")


if __name__ == "__main__":
    main()
