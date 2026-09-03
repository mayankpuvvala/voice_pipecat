"""check_availability / book_table: the two time-critical reservation tools.

Both call Google Sheets directly (via app.services.sheets_client), never
n8n — see that module's docstring for why. Both also independently
re-validate kitchen hours in code rather than trusting the LLM to have
called check_availability first or to have done the date math correctly —
same "structurally enforce it" approach as LogInteractionEnforcer, applied
here to "never book outside operating hours" instead of "never skip
logging."

No seat/order cap is enforced yet, by design at this stage — any request
inside operating hours is bookable. check_availability therefore only
checks hours; it used to also read the whole Bookings sheet for an
existing-same-day-count that nothing (no prompt instruction, no eval
scenario) ever consumed — pure latency on the live-call path for a number
that never changed the answer, so that read was removed. Reintroduce a
real read here only once there's an actual capacity check to base it on.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams

from app.config.restaurants import ACTIVE_RESTAURANT as RESTAURANT
from app.pipeline.hours import is_within_hours
from app.services import sheets_client

_BOOKINGS_SHEET = "Bookings"


def _confirmed_since_last_availability_check(context: LLMContext) -> bool:
    """Whether the caller has spoken since the most recent successful check_availability.

    Structural backup for the prompt's read-back-and-confirm instruction —
    confirmed live from a real call that prompt wording alone isn't
    reliable here: a caller said "8 PM," Sarvam STT transcribed it as "at
    ATM," and the model booked a guessed time (not even one it had itself
    considered) without ever giving the caller a chance to catch it. A
    caller's own reply is the only actual evidence they heard the read-back
    and had a chance to correct it, so this checks for a real "user"
    message in context after the last check_availability succeeded —
    not just that the model *said* something, which it might do without
    waiting for a reply.
    """
    messages = context.messages
    tool_call_names: dict[str, str] = {}
    last_available_check_index: int | None = None

    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                tool_call_names[tool_call.get("id")] = function.get("name")
        elif message.get("role") == "tool":
            name = tool_call_names.get(message.get("tool_call_id"))
            content = (message.get("content") or "").replace(" ", "")
            if name == "check_availability" and '"available":true' in content:
                last_available_check_index = index

    if last_available_check_index is None:
        return False

    return any(
        message.get("role") == "user" for message in messages[last_available_check_index + 1 :]
    )


async def check_availability(
    params: FunctionCallParams,
    date: str,
    time: str,
    guests_count: str,
) -> None:
    """Check whether a reservation slot is bookable before promising anything to the caller.

    Always call this before confirming a reservation — never confirm a table from
    memory, guesswork, or what "sounds reasonable."

    Args:
        date: The reservation date as YYYY-MM-DD. Resolve relative dates like
            "today"/"tomorrow"/"this Friday" against the current date given to
            you in your instructions before calling this.
        time: The reservation time as 24-hour HH:MM (e.g. "21:00" for 9 PM).
        guests_count: Number of guests, as a string, e.g. "5".
    """
    ok, reason = is_within_hours(RESTAURANT, date, time)
    if not ok:
        await params.result_callback({"available": False, "reason": reason})
        return

    await params.result_callback({"available": True, "reason": ""})


async def book_table(
    params: FunctionCallParams,
    date: str,
    time: str,
    guests_count: str,
    caller_name: str,
    caller_phone: str = "",
) -> None:
    """Write a confirmed reservation to the real booking sheet.

    Only call this after check_availability has just returned available: true
    for this same date/time, AND after you've read the date/time/guest count
    back to the caller and they've explicitly said yes. Never speak a
    confirmation to the caller until this tool has returned booked: true — if
    it returns booked: false, do not tell them the table is set; explain the
    reason and offer to take a message instead.

    Args:
        date: The reservation date as YYYY-MM-DD.
        time: The reservation time as 24-hour HH:MM.
        guests_count: Number of guests, as a string, e.g. "5".
        caller_name: The name to hold the table under. Required — ask the caller
            for their name if they haven't given one; never pass a blank or
            placeholder value just to get the call through. Pass it exactly as
            the caller said it — never shorten it, "clean it up," or drop any
            part of it because it sounds unusual; an unfamiliar-sounding name
            is still the caller's real name, not a transcription artifact to
            correct.
        caller_phone: Caller's callback number if given, otherwise empty string.
    """
    if not caller_name.strip():
        await params.result_callback(
            {"booked": False, "reason": "caller name is required — ask for it before booking"}
        )
        return

    if not _confirmed_since_last_availability_check(params.context):
        await params.result_callback(
            {
                "booked": False,
                "reason": (
                    "not yet confirmed with the caller — read the date, time, "
                    "and guest count back to them and wait for an explicit yes "
                    "before calling book_table"
                ),
            }
        )
        return

    ok, reason = is_within_hours(RESTAURANT, date, time)
    if not ok:
        await params.result_callback({"booked": False, "reason": reason})
        return

    app_resources = params.app_resources or {}
    # Caller ID from the telephony provider — only fills in when the caller
    # didn't give a callback number themselves; never overrides it.
    caller_phone = caller_phone or app_resources.get("caller_phone", "")
    row = {
        "Timestamp": datetime.now(timezone.utc).isoformat(),
        "BookingId": uuid.uuid4().hex[:12],
        "CallSessionId": app_resources.get("call_session_id", ""),
        "Date": date,
        "Time": time,
        "GuestsCount": guests_count,
        "CallerName": caller_name,
        "CallerPhone": caller_phone,
        "Status": "confirmed",
    }

    try:
        await asyncio.to_thread(sheets_client.append_row, _BOOKINGS_SHEET, row)
    except Exception:
        logger.exception("book_table: failed to write to Bookings sheet")
        await params.result_callback(
            {"booked": False, "reason": "internal error saving the reservation"}
        )
        return

    await params.result_callback({"booked": True, "reason": ""})
