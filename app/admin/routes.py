"""Registers /admin on Pipecat's shared runner FastAPI app.

Uses `pipecat.runner.run`'s exported `app` extension point. One row per
CALL (joined across Sheet1/Bookings/Recordings by CallSessionId), with
topics collapsed into a small set of derived category badges.
"""

from __future__ import annotations

import asyncio
import io
import wave
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from app.admin.auth import require_admin
from app.admin.sheets_reader import fetch_calls
from app.config.restaurants import ACTIVE_RESTAURANT
from app.config.settings import settings
from app.pipeline.logging_enforcer import LOG_INTERACTION_STATS
from app.services import drive_oauth_client

_IST = ZoneInfo("Asia/Kolkata")

_PROBE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "_health_probe_tool",
            "description": "Call this immediately, regardless of what the user said.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


async def _openai_chat_completion(client: Any, **kwargs: Any) -> Any:
    """Tries with reasoning_effort='none' first, retries without it if this
    account/model rejects the argument outright -- same account-dependent
    behavior app/main.py's own startup probe exists to handle (see
    TROUBLESHOOTING.md), replicated locally rather than importing
    app.main's private probe result (app.main imports this module, so the
    reverse import would be circular)."""
    try:
        return await client.chat.completions.create(reasoning_effort="none", **kwargs)
    except Exception as e:
        if "Unrecognized request argument supplied: reasoning_effort" in str(e):
            return await client.chat.completions.create(**kwargs)
        raise


async def _check_llm() -> dict[str, Any]:
    """Tiny real completion (not just a key-presence check) -- confirms the
    configured OPENAI_API_KEY/OPENAI_MODEL actually resolves and responds
    on this deployment specifically."""
    if not settings.openai_api_key:
        return {"ok": False, "error": "OPENAI_API_KEY not set"}
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await _openai_chat_completion(
            client,
            model=settings.openai_model,
            messages=[{"role": "user", "content": "Reply with exactly one word: ok"}],
            max_completion_tokens=5,
        )
        return {"ok": True, "model": settings.openai_model, "reply": resp.choices[0].message.content}
    except Exception as e:
        return {"ok": False, "model": settings.openai_model, "error": f"{type(e).__name__}: {e}"}


async def _check_tool_calling() -> dict[str, Any]:
    """Confirms the model actually EMITS a tool call when given one and
    told to use it -- not just that a tools= request doesn't error (that
    alone doesn't prove the model will call log_interaction/check_availability/
    book_table on a real call; see TROUBLESHOOTING.md's logInteraction
    reliability finding for why this distinction mattered before)."""
    if not settings.openai_api_key:
        return {"ok": False, "error": "OPENAI_API_KEY not set"}
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await _openai_chat_completion(
            client,
            model=settings.openai_model,
            messages=[{"role": "user", "content": "call the tool now"}],
            tools=_PROBE_TOOLS,
            tool_choice="required",
            max_completion_tokens=50,
        )
        tool_calls = resp.choices[0].message.tool_calls or []
        called = any(tc.function.name == "_health_probe_tool" for tc in tool_calls)
        return {"ok": called, "tool_calls_returned": len(tool_calls)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _silent_wav_bytes(duration_secs: float = 0.3, sample_rate: int = 16000) -> bytes:
    """A tiny valid WAV file (silence) for the STT connectivity check below
    -- proves the API key/account works and the service actually responds,
    not that transcription quality is good. Silence transcribing to empty
    text is the correct, expected result here."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(b"\x00\x00" * int(sample_rate * duration_secs))
    return buf.getvalue()


async def _check_stt() -> dict[str, Any]:
    """Sarvam's batch REST transcribe endpoint, not the WebSocket stream
    ResilientSarvamSTTService actually uses on live calls (see
    app/services/resilient_stt.py) -- that's connection-oriented and needs
    a running pipeline context to exercise meaningfully. The batch endpoint
    shares the same API key/account, so it validates auth + reachability
    without standing up a fake call."""
    if not settings.sarvam_api_key:
        return {"ok": False, "error": "SARVAM_API_KEY not set"}
    from sarvamai import AsyncSarvamAI

    client = AsyncSarvamAI(api_subscription_key=settings.sarvam_api_key)
    try:
        resp = await client.speech_to_text.transcribe(
            file=("health.wav", _silent_wav_bytes(), "audio/wav"),
            model="saarika:v2.5",
        )
        return {"ok": True, "transcript": getattr(resp, "transcript", None)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _check_tts() -> dict[str, Any]:
    """Both TTS providers this deployment actually uses, in priority order
    (see app/services/rumik_tts.py's docstring): Rumik primary, OpenAI as
    the in-call fallback. Each does a real tiny synthesis, not just a key
    check."""
    results: dict[str, dict[str, Any]] = {}

    if settings.rumik_api_key:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://silk-api.rumik.ai/v1/tts",
                    headers={"Authorization": f"Bearer {settings.rumik_api_key}"},
                    json={
                        "model": "mulberry",
                        "text": "test",
                        "speaker": "lucas",
                        "description": "neutral, brief",
                    },
                )
            results["rumik"] = (
                {"ok": True, "bytes": len(resp.content)}
                if resp.status_code == 200
                else {"ok": False, "status": resp.status_code, "error": resp.text[:200]}
            )
        except Exception as e:
            results["rumik"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    else:
        results["rumik"] = {"ok": False, "error": "RUMIK_API_KEY not set"}

    if settings.openai_api_key:
        try:
            from openai import AsyncOpenAI

            oai = AsyncOpenAI(api_key=settings.openai_api_key)
            async with oai.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice=settings.openai_tts_voice,
                input="test",
                response_format="pcm",
            ) as resp:
                nbytes = sum([len(chunk) async for chunk in resp.iter_bytes()])
            results["openai_fallback"] = {"ok": nbytes > 0, "bytes": nbytes}
        except Exception as e:
            results["openai_fallback"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    else:
        results["openai_fallback"] = {"ok": False, "error": "OPENAI_API_KEY not set"}

    return results

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


def _format_datetime(iso_ts: str) -> tuple[str, str, str]:
    """Returns (date as dd/mm/yy, time as h:mm:ss am/pm, date as yyyy-mm-dd
    for filtering) in IST, or ("—", "—", ""). dd/mm/yy with slashes (not
    colons) and 12-hour time so the date doesn't read as a second clock
    time stacked under the real one."""
    if not iso_ts:
        return "—", "—", ""
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "—", "—", ""
    local = dt.astimezone(_IST)
    date_str = local.strftime("%d/%m/%y")
    time_str = local.strftime("%I:%M:%S %p").lstrip("0").lower()
    return date_str, time_str, local.strftime("%Y-%m-%d")


def _format_duration(duration_secs: Any) -> str:
    """Returns "1min 42secs" style, or "—" if unknown/unparseable."""
    if duration_secs in (None, ""):
        return "—"
    try:
        total = int(round(float(duration_secs)))
    except (TypeError, ValueError):
        return "—"
    minutes, seconds = divmod(max(total, 0), 60)
    return f"{minutes}min {seconds}secs"


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
    date_str, time_str, iso_date = _format_datetime(call["timestamp"])
    duration_str = _format_duration(call.get("duration_secs"))
    outcome_key = "followup" if call["needs_followup"] else "resolved"
    confidence_key = call["confidence_rank"]
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

    return f"""<tr data-date="{iso_date}" data-outcome="{outcome_key}" data-confidence="{confidence_key}">
    <td class="nowrap">{duration_str}<br><span class="muted">{date_str} {time_str}</span></td>
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
    @app.get("/admin/health", dependencies=[Depends(require_admin)])
    async def admin_health() -> JSONResponse:
        """Live diagnostics for whatever's actually deployed — built because
        this deployment's own env vars (API keys, OAuth tokens) have already
        been confirmed to sometimes differ from local .env (see
        TROUBLESHOOTING.md's reasoning_effort incident), so a passing local
        test proves nothing about prod. Actually exercises the Drive upload
        credential path (read-only: refresh + folder lookup, no file
        written), plus the LLM, tool-calling, STT, and TTS paths, rather
        than just checking that keys are *present*.

        Every call to this endpoint makes real, billed requests to OpenAI,
        Sarvam, and Rumik (a handful of tiny completions/syntheses each
        time) — admin-gated for that reason, don't put it behind automated
        polling.
        """
        drive_ok = True
        drive_error: str | None = None
        try:
            service = drive_oauth_client._client()
            drive_oauth_client._get_or_create_folder(service)
        except Exception as e:  # noqa: BLE001 - the exception message IS the diagnostic
            drive_ok = False
            drive_error = f"{type(e).__name__}: {e}"
            logger.exception("admin_health: Drive credential check failed")

        llm_result, tool_calling_result, stt_result, tts_result = await asyncio.gather(
            _check_llm(), _check_tool_calling(), _check_stt(), _check_tts()
        )

        total = LOG_INTERACTION_STATS["inline"] + LOG_INTERACTION_STATS["backfilled"]
        inline_rate = (LOG_INTERACTION_STATS["inline"] / total) if total else None

        return JSONResponse(
            {
                "restaurant": ACTIVE_RESTAURANT.name,
                "restaurant_id": settings.restaurant_id,
                "openai_model": settings.openai_model,
                "drive_recording": {
                    "ok": drive_ok,
                    "error": drive_error,
                },
                "llm": llm_result,
                "tool_calling": tool_calling_result,
                "stt": stt_result,
                "tts": tts_result,
                "log_interaction_stats": {
                    **LOG_INTERACTION_STATS,
                    "inline_rate": inline_rate,
                },
            }
        )

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

  .filters {{
    display: flex; flex-wrap: wrap; align-items: center; gap: 18px;
    background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    padding: 12px 16px; margin-bottom: 1.25rem; font-size: 0.85rem;
  }}
  .filter-group {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .filter-group label {{ display: flex; align-items: center; gap: 4px; white-space: nowrap; cursor: pointer; }}
  .filter-label {{ font-weight: 600; color: #374151; margin-right: 2px; }}
  .filters input[type="date"] {{
    border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px; font-size: 0.85rem;
  }}
  .filters button {{
    border: 1px solid var(--border); background: #f5f6f8; border-radius: 6px;
    padding: 5px 10px; font-size: 0.8rem; cursor: pointer;
  }}
  .filters button:hover {{ background: #eceef1; }}

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
  <p class="meta">{len(calls)} call(s) logged, newest first. Data may be up to 20s stale (short cache to avoid re-reading the Sheet on every refresh).</p>
  {error_html}

  <div class="filters">
    <div class="filter-group">
      <label>From <input type="date" id="filter-from"></label>
      <label>To <input type="date" id="filter-to"></label>
    </div>
    <div class="filter-group">
      <span class="filter-label">Outcome</span>
      <label><input type="checkbox" class="f-outcome" value="resolved" checked> Resolved</label>
      <label><input type="checkbox" class="f-outcome" value="followup" checked> Follow-up needed</label>
    </div>
    <div class="filter-group">
      <span class="filter-label">Confidence</span>
      <label><input type="checkbox" class="f-confidence" value="1" checked> High</label>
      <label><input type="checkbox" class="f-confidence" value="2" checked> Medium</label>
      <label><input type="checkbox" class="f-confidence" value="3" checked> Low</label>
      <label><input type="checkbox" class="f-confidence" value="0" checked> Unrated</label>
    </div>
    <button type="button" id="filter-clear">Clear filters</button>
    <span class="muted" id="filter-count"></span>
  </div>

  <div class="table-wrap">
    <table id="call-table">
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

    (function() {{
      var fromEl = document.getElementById('filter-from');
      var toEl = document.getElementById('filter-to');
      var countEl = document.getElementById('filter-count');
      var rows = Array.prototype.slice.call(
        document.querySelectorAll('#call-table tbody tr[data-date]')
      );

      function checkedValues(selector) {{
        return Array.prototype.slice.call(document.querySelectorAll(selector + ':checked'))
          .map(function(el) {{ return el.value; }});
      }}

      function applyFilters() {{
        var from = fromEl.value;
        var to = toEl.value;
        var outcomes = checkedValues('.f-outcome');
        var confidences = checkedValues('.f-confidence');
        var shown = 0;
        rows.forEach(function(row) {{
          var date = row.getAttribute('data-date');
          var ok = true;
          if (date) {{
            if (from && date < from) ok = false;
            if (to && date > to) ok = false;
          }}
          if (outcomes.indexOf(row.getAttribute('data-outcome')) === -1) ok = false;
          if (confidences.indexOf(row.getAttribute('data-confidence')) === -1) ok = false;
          row.style.display = ok ? '' : 'none';
          if (ok) shown++;
        }});
        countEl.textContent = shown + ' of ' + rows.length + ' shown';
      }}

      document.querySelectorAll('.f-outcome, .f-confidence').forEach(function(el) {{
        el.addEventListener('change', applyFilters);
      }});
      fromEl.addEventListener('change', applyFilters);
      toEl.addEventListener('change', applyFilters);
      document.getElementById('filter-clear').addEventListener('click', function() {{
        fromEl.value = '';
        toEl.value = '';
        document.querySelectorAll('.f-outcome, .f-confidence').forEach(function(el) {{ el.checked = true; }});
        applyFilters();
      }});

      if (rows.length) applyFilters();
    }})();
  </script>
</body>
</html>"""
        return HTMLResponse(html)
