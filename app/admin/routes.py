"""Registers /admin on Pipecat's shared runner FastAPI app.

`pipecat.runner.run` exports its FastAPI `app` instance specifically so other
modules can add routes before calling `main()` (see that module's own
docstring) — this is that extension point, not a workaround.

One row per CALL (joined across Sheet1/Bookings/Recordings by
CallSessionId — see sheets_reader.fetch_calls), not one row per logged
topic like the original version. Topic is a small set of derived category
badges rather than raw free-text, since a call can touch several topics and
the raw logInteraction topic strings aren't a controlled vocabulary.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from loguru import logger

from app.admin.auth import require_admin
from app.admin.sheets_reader import fetch_calls
from app.config.restaurants import ACTIVE_RESTAURANT

_IST = ZoneInfo("Asia/Kolkata")

_TOPIC_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Reservation", ("reserv", "book", "table")),
    ("Cancellation", ("cancel",)),
    (
        "Menu & Info",
        ("menu", "hour", "location", "parking", "deliver", "takeout", "payment", "dietary", "cuisine", "address"),
    ),
    ("Pricing/Allergen", ("price", "allerg", "ingredient")),
    ("Escalation", ("complain", "emergency", "manager", "owner")),
]

_CONFIDENCE_BADGES = {
    0: ("—", "badge-neutral"),
    1: ("High", "badge-green"),
    2: ("Medium", "badge-yellow"),
    3: ("Low", "badge-red"),
}

_COLUMNS = [
    "Call Time", "Caller", "Phone", "Topic", "Summary", "Outcome",
    "Transcript", "Recording", "Reservation", "Confidence", "Escalation",
]


def _format_datetime(iso_ts: str) -> tuple[str, str]:
    """Returns (date as dd:mm:yy, time as hh:mm:ss) in IST, or ("—", "—")."""
    if not iso_ts:
        return "—", "—"
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "—", "—"
    local = dt.astimezone(_IST)
    return local.strftime("%d:%m:%y"), local.strftime("%H:%M:%S")


def _categorize_topics(topics: list[str]) -> list[str]:
    matched: set[str] = set()
    for topic in topics:
        topic_lower = topic.lower()
        hit = False
        for name, keywords in _TOPIC_CATEGORIES:
            if any(kw in topic_lower for kw in keywords):
                matched.add(name)
                hit = True
        if not hit:
            matched.add("Other")
    return sorted(matched)


def _outcome_html(call: dict[str, Any]) -> str:
    """Whether the caller's need was handled directly vs. needs the owner to
    follow up, plus the specifics logInteraction recorded for the owner —
    both already written to Sheet1 on every call, just not surfaced before."""
    badge = (
        '<span class="badge badge-red">⚠ Follow-up needed</span>'
        if call["needs_followup"]
        else '<span class="badge badge-green">✓ Resolved</span>'
    )
    details = "; ".join(call["details"])
    if not details:
        return badge
    return f'{badge}<br><span class="muted">{escape(details)}</span>'


def _reservation_text(reservation: dict[str, Any] | None) -> str:
    if not reservation:
        return "—"
    guests = reservation.get("guests") or "?"
    date = reservation.get("date") or "?"
    time = reservation.get("time") or "?"
    return f"{guests} guests · {date} {time}"


def _call_row_html(call: dict[str, Any]) -> str:
    date_str, time_str = _format_datetime(call["timestamp"])
    caller_name = escape(call["caller_name"] or "—")
    caller_phone = escape(call["caller_phone"] or "—")

    categories = _categorize_topics(call["topics"])
    topic_html = (
        "".join(f'<span class="badge badge-topic">{escape(c)}</span>' for c in categories)
        or "—"
    )

    summary = escape(call["summary"]) if call["summary"] else "—"
    outcome_html = _outcome_html(call)

    transcript = call["transcript"]
    if transcript:
        transcript_html = (
            f'<button class="link-btn" onclick="showTranscript(this)" '
            f'data-transcript="{escape(transcript)}" '
            f'data-caller="{escape(call["caller_name"] or "Unknown caller")}">View</button>'
        )
    else:
        transcript_html = "—"

    if call["recording_url"]:
        recording_html = (
            f'<a class="link-btn" href="{escape(call["recording_url"])}" '
            f'target="_blank" rel="noopener">▶ Listen</a>'
        )
    else:
        recording_html = "—"

    reservation_html = escape(_reservation_text(call["reservation"]))

    conf_label, conf_class = _CONFIDENCE_BADGES.get(call["confidence_rank"], _CONFIDENCE_BADGES[0])
    confidence_html = f'<span class="badge {conf_class}">{conf_label}</span>'

    escalation_html = (
        '<span class="badge badge-red">⚠ Escalated</span>'
        if call["escalated"]
        else '<span class="badge badge-neutral">—</span>'
    )

    return f"""<tr>
    <td class="nowrap">{date_str}<br><span class="muted">{time_str}</span></td>
    <td>{caller_name}</td>
    <td class="nowrap">{caller_phone}</td>
    <td>{topic_html}</td>
    <td class="summary-cell">{summary}</td>
    <td class="summary-cell">{outcome_html}</td>
    <td>{transcript_html}</td>
    <td>{recording_html}</td>
    <td class="nowrap">{reservation_html}</td>
    <td>{confidence_html}</td>
    <td>{escalation_html}</td>
  </tr>"""


def register_admin_routes(app: FastAPI) -> None:
    @app.get("/admin", dependencies=[Depends(require_admin)])
    async def admin_page() -> HTMLResponse:
        try:
            calls = fetch_calls()
            error: str | None = None
        except Exception:  # noqa: BLE001 - show a generic message, don't leak internals or 500
            calls = []
            error = "Could not load call data right now — try refreshing in a moment."
            logger.exception("admin_page: fetch_calls() failed")

        header_html = "".join(f"<th>{col}</th>" for col in _COLUMNS)
        rows_html = "".join(_call_row_html(c) for c in calls) or (
            f"<tr><td colspan='{len(_COLUMNS)}'>No calls logged yet.</td></tr>"
        )
        error_html = f"<p class='error'>{escape(error)}</p>" if error else ""

        html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{escape(ACTIVE_RESTAURANT.name)} — Call Log</title>
<style>
  :root {{
    --border: #e2e2e2;
    --bg-alt: #fafafa;
    --text-muted: #6b7280;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    margin: 0;
    padding: 2rem;
    color: #1a1a1a;
    background: #f5f6f8;
  }}
  h2 {{ margin: 0 0 0.25rem; font-size: 1.4rem; }}
  .meta {{ color: var(--text-muted); font-size: 0.85rem; margin: 0 0 1.25rem; }}
  .error {{ color: #b00020; }}
  .table-wrap {{
    background: #fff;
    border-radius: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    overflow-x: auto;
  }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; min-width: 1100px; }}
  th, td {{ border-bottom: 1px solid var(--border); padding: 10px 12px; text-align: left; vertical-align: top; }}
  th {{
    background: var(--bg-alt);
    position: sticky; top: 0;
    font-weight: 600; color: #374151;
    white-space: nowrap;
  }}
  tr:hover td {{ background: #fbfbfd; }}
  .nowrap {{ white-space: nowrap; }}
  .muted {{ color: var(--text-muted); font-size: 0.78rem; }}
  .summary-cell {{ max-width: 260px; }}

  .badge {{
    display: inline-block;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    margin: 1px 3px 1px 0;
  }}
  .badge-topic {{ background: #eef2ff; color: #3730a3; }}
  .badge-neutral {{ background: #f0f0f0; color: #6b7280; }}
  .badge-green {{ background: #dcfce7; color: #166534; }}
  .badge-yellow {{ background: #fef9c3; color: #854d0e; }}
  .badge-red {{ background: #fee2e2; color: #991b1b; }}

  .link-btn {{
    background: none; border: none; padding: 0; cursor: pointer;
    color: #2563eb; text-decoration: none; font-size: 0.85rem; font-weight: 500;
  }}
  .link-btn:hover {{ text-decoration: underline; }}

  #modal-overlay {{
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.45);
    align-items: center; justify-content: center; z-index: 100; padding: 2rem;
  }}
  #modal-box {{
    background: #fff; border-radius: 10px; max-width: 640px; width: 100%;
    max-height: 80vh; display: flex; flex-direction: column;
    box-shadow: 0 10px 40px rgba(0,0,0,0.25);
  }}
  #modal-header {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 14px 18px; border-bottom: 1px solid var(--border);
  }}
  #modal-header h3 {{ margin: 0; font-size: 1rem; }}
  #modal-close {{
    background: none; border: none; font-size: 1.3rem; line-height: 1; cursor: pointer;
    color: var(--text-muted); padding: 4px 8px;
  }}
  #modal-close:hover {{ color: #1a1a1a; }}
  #modal-body {{
    padding: 16px 18px; overflow-y: auto; white-space: pre-wrap;
    font-size: 0.88rem; line-height: 1.5;
  }}
</style>
</head>
<body>
  <h2>{escape(ACTIVE_RESTAURANT.name)} — Call Log</h2>
  <p class="meta">{len(calls)} call(s) logged, newest first. Reads live from the Google Sheet on every request — no caching.</p>
  {error_html}
  <div class="table-wrap">
    <table>
      <thead><tr>{header_html}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div id="modal-overlay" onclick="if (event.target === this) closeModal()">
    <div id="modal-box">
      <div id="modal-header">
        <h3 id="modal-title">Transcript</h3>
        <button id="modal-close" onclick="closeModal()" aria-label="Close">&times;</button>
      </div>
      <div id="modal-body"></div>
    </div>
  </div>

  <script>
    function showTranscript(btn) {{
      document.getElementById('modal-title').textContent = 'Transcript — ' + btn.getAttribute('data-caller');
      document.getElementById('modal-body').textContent = btn.getAttribute('data-transcript');
      document.getElementById('modal-overlay').style.display = 'flex';
    }}
    function closeModal() {{
      document.getElementById('modal-overlay').style.display = 'none';
    }}
    document.addEventListener('keydown', function(e) {{
      if (e.key === 'Escape') closeModal();
    }});
  </script>
</body>
</html>"""
        return HTMLResponse(html)
