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

from app.config.restaurants import ACTIVE_RESTAURANT as RESTAURANT
from app.services import sheets_client

_INTERACTIONS_SHEET = "Sheet1"

# The model sometimes re-calls this 2-3x in quick succession for what's
# really one moment (e.g. a reservation confirmed three times, ~1s apart,
# reworded each time) — separate from LogInteractionEnforcer's own bounded
# nudge, this is the model second-guessing itself. An in-process cache is
# fine since one process handles a call's full duration; no need to
# survive restarts or share across processes.
#
# Entries are overwritten on a repeat call_session_id, never deleted at
# call-end (there's no teardown hook) — _PRUNE_AFTER_SECS instead drops
# anything too stale to matter for the dedupe window, on the next write.
_DEDUPE_WINDOW_SECS = 10.0
_PRUNE_AFTER_SECS = 3600.0
_recent_topics: dict[str, tuple[str, float]] = {}


def _prune_stale_topics(now_mono: float) -> None:
    stale = [
        session_id
        for session_id, (_, last_seen) in _recent_topics.items()
        if now_mono - last_seen > _PRUNE_AFTER_SECS
    ]
    for session_id in stale:
        del _recent_topics[session_id]


async def write_interaction_row(
    *,
    call_session_id: str,
    caller_phone: str,
    topic: str,
    resolved: bool,
    caller_name: str = "",
    details: str = "",
    guests_count: str = "",
    drift: bool = False,
    call_confidence: str = "medium",
) -> bool:
    """The actual dedupe + Sheets write behind the `log_interaction` tool below.

    Split out so `app/pipeline/logging_enforcer.py`'s background backfill call
    (a real LLM classifying a reply the model itself didn't log) can write a
    row the same way, without needing to fake a `FunctionCallParams`. Returns
    whether a row was actually written -- False on a Sheets error; treated as
    success (True) for a deduped repeat, since the topic is already logged.
    """
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(RESTAURANT.timezone))

    if call_session_id:
        now_mono = time.monotonic()
        _prune_stale_topics(now_mono)
        last = _recent_topics.get(call_session_id)
        if last and last[0] == topic and (now_mono - last[1]) < _DEDUPE_WINDOW_SECS:
            logger.debug(
                "Skipping duplicate logInteraction for topic '{}' on call {} ({}s after the last one)",
                topic,
                call_session_id,
                round(now_mono - last[1], 1),
            )
            return True
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
        return False

    return True


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
    app_resources = params.app_resources or {}
    call_session_id = app_resources.get("call_session_id", "")
    # Caller ID from the telephony provider (see app/services/twilio_client.py
    # and run_bot()) — only used when the caller didn't give a number
    # themselves; never overrides what they actually said.
    caller_phone = caller_phone or app_resources.get("caller_phone", "")

    ok = await write_interaction_row(
        call_session_id=call_session_id,
        caller_phone=caller_phone,
        topic=topic,
        resolved=resolved,
        caller_name=caller_name,
        details=details,
        guests_count=guests_count,
        drift=drift,
        call_confidence=call_confidence,
    )
    await params.result_callback({"logged": ok})
