"""The `logInteraction` tool: logs each resolved/unresolved call topic
directly to the same Google Sheet tab n8n used to write to.

Moved off the n8n webhook it originally POSTed to: that webhook ran live, in
the middle of a call, and Railway's free-tier n8n can cold-start — dead air
on a real call. This writes straight to Sheets from this same process
instead (see app.services.sheets_client), no extra hop. n8n's remaining jobs
are non-time-critical and read this sheet after the fact (end-of-day digest,
escalation alert) — see n8n/restaurant_reception_workflow.json; its
`1a`-`1f` live-webhook branch is dead now and should be removed next time
that workflow is touched.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

from app.config.restaurants.spice_route_kitchen import SPICE_ROUTE_KITCHEN as RESTAURANT
from app.services import sheets_client

_INTERACTIONS_SHEET = "Sheet1"

# Live calls have shown the model re-calling this tool 2-3x in quick
# succession for what's really one moment (e.g. a reservation confirmation
# logged three times, ~1s apart, with near-identical reworded details) —
# separate from LogInteractionEnforcer's nudge, which is already bounded to
# one extra round; this is the model itself second-guessing whether it
# already logged something. In-process cache, not a Sheets read, so it adds
# no latency to a live call: one process handles a call's full duration, so
# this doesn't need to survive restarts or be shared across processes.
_DEDUPE_WINDOW_SECS = 10.0
_recent_topics: dict[str, tuple[str, float]] = {}


async def log_interaction(
    params: FunctionCallParams,
    topic: str,
    resolved: bool,
    caller_name: str = "",
    caller_phone: str = "",
    details: str = "",
    guests_count: str = "",
    drift: bool = False,
    call_confidence: str = "medium",
) -> None:
    """Log what this caller asked about, whether it was answered directly or needs the owner's attention.

    Call this immediately after resolving each topic, not at the end of the call — callers
    often hang up abruptly with no goodbye.

    Args:
        topic: Short label for what they asked about, e.g. "delivery hours", "large party reservation".
        resolved: True if answered directly from the restaurant facts (including taking a
            reservation), false if the owner needs to follow up. This is about *which path*
            you took, not about whether it went well.
        caller_name: Caller's name if given, otherwise empty string.
        caller_phone: Caller's callback phone number if given, otherwise empty string.
        details: One short sentence with specifics the owner needs.
        guests_count: Number of guests, e.g. "3", only when this topic is a reservation.
            Empty string otherwise.
        drift: True if this topic's query did NOT actually get solved — you talked about it
            but the caller's underlying need wasn't met (they seemed confused, you couldn't
            pin down what they wanted, or you answered something adjacent rather than what
            they actually asked). False if you cleanly landed on what they needed, even if
            the answer was "the owner will call you back."
        call_confidence: Your own honest read on how well this topic went: "high" if you're
            confident you understood the caller and handled it correctly, "medium" if there
            was some ambiguity (unclear speech, a guess on intent) but you think you got it
            right, "low" if you're genuinely unsure you understood them correctly or the
            caller seemed unsatisfied/confused by your response.
    """
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(RESTAURANT.timezone))
    app_resources = params.app_resources or {}
    call_session_id = app_resources.get("call_session_id", "")
    # Caller ID from the telephony provider (see app/services/twilio_client.py
    # and run_bot()) — only used when the caller didn't give a number
    # themselves; never overrides what they actually said.
    caller_phone = caller_phone or app_resources.get("caller_phone", "")

    if call_session_id:
        now_mono = time.monotonic()
        last = _recent_topics.get(call_session_id)
        if last and last[0] == topic and (now_mono - last[1]) < _DEDUPE_WINDOW_SECS:
            logger.debug(
                "Skipping duplicate logInteraction for topic '{}' on call {} ({}s after the last one)",
                topic,
                call_session_id,
                round(now_mono - last[1], 1),
            )
            await params.result_callback({"logged": True})
            return
        _recent_topics[call_session_id] = (topic, now_mono)

    row = {
        "Timestamp": now_utc.isoformat(),
        "CallDate": now_local.date().isoformat(),
        "CallSessionId": call_session_id,
        "CallerName": caller_name,
        "CallerPhone": caller_phone,
        "Topic": topic,
        "Resolved": resolved,
        "Details": details,
        "GuestsCount": guests_count,
        "Drift": drift,
        "CallConfidence": call_confidence,
    }

    try:
        await asyncio.to_thread(sheets_client.append_row, _INTERACTIONS_SHEET, row)
    except Exception:
        logger.exception("Failed to log interaction to Sheets")
        await params.result_callback({"logged": False})
        return

    await params.result_callback({"logged": True})
