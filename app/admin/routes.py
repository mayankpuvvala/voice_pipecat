"""Registers /admin on Pipecat's shared runner FastAPI app.

`pipecat.runner.run` exports its FastAPI `app` instance specifically so other
modules can add routes before calling `main()` (see that module's own
docstring) — this is that extension point, not a workaround.
"""

from __future__ import annotations

from html import escape

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from app.admin.auth import require_admin
from app.admin.sheets_reader import fetch_interactions

_COLUMNS = [
    "Timestamp", "CallDate", "CallerName", "CallerPhone", "Topic",
    "Resolved", "Details", "GuestsCount", "Drift", "CallConfidence",
]


def _row_html(row: dict) -> str:
    cells = "".join(f"<td>{escape(str(row.get(col, '')))}</td>" for col in _COLUMNS)
    return f"<tr>{cells}</tr>"


def register_admin_routes(app: FastAPI) -> None:
    @app.get("/admin", dependencies=[Depends(require_admin)])
    async def admin_page() -> HTMLResponse:
        try:
            interactions = fetch_interactions()
            error: str | None = None
        except Exception as exc:  # noqa: BLE001 - show the error on the page, don't 500
            interactions = []
            error = str(exc)

        header_html = "".join(f"<th>{col}</th>" for col in _COLUMNS)
        rows_html = "".join(_row_html(r) for r in interactions) or (
            f"<tr><td colspan='{len(_COLUMNS)}'>No interactions logged yet.</td></tr>"
        )
        error_html = (
            f"<p style='color:#b00020'>Could not load the sheet: {escape(error)}</p>"
            if error
            else ""
        )

        html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Spice Route Kitchen — Call Log</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #f5f5f5; position: sticky; top: 0; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  .meta {{ color: #666; font-size: 0.9rem; }}
</style>
</head>
<body>
  <h2>Spice Route Kitchen — Call Log</h2>
  <p class="meta">{len(interactions)} interaction(s) logged, newest first. Reads live from the Google Sheet on every request — no caching.</p>
  {error_html}
  <table>
    <thead><tr>{header_html}</tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</body>
</html>"""
        return HTMLResponse(html)
