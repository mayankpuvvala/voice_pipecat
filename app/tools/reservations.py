"""check_availability / book_table: the two time-critical reservation tools.

Both call Google Sheets directly (via app.services.sheets_client), never
n8n — see that module's docstring for why. Both also independently
re-validate kitchen hours in code rather than trusting the LLM to have
called check_availability first or to have done the date math correctly —
same "structurally enforce it" approach as LogInteractionEnforcer, applied
here to "never book outside operating hours" instead of "never skip
logging."

No seat/order cap is enforced yet, by design at this stage — any request
inside operating hours is bookable; existing same-day booking count is
returned for visibility only, not used to reject anything.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

from app.config.restaurants.spice_route_kitchen import SPICE_ROUTE_KITCHEN as RESTAURANT
from app.pipeline.hours import is_within_hours
from app.services import sheets_client

_BOOKINGS_SHEET = "Bookings"


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

    try:
        existing = await asyncio.to_thread(sheets_client.read_rows, _BOOKINGS_SHEET)
        same_day_count = sum(1 for r in existing if r.get("Date") == date)
    except Exception:
        logger.exception("check_availability: failed to read Bookings sheet")
        # No cap is enforced yet, so a read failure doesn't block booking —
        # only kitchen hours do (checked above). Still report it happened.
        await params.result_callback(
            {"available": True, "reason": "", "existing_bookings_today": None}
        )
        return

    await params.result_callback(
        {"available": True, "reason": "", "existing_bookings_today": same_day_count}
    )


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
    for this same date/time. Never speak a confirmation to the caller until
    this tool has returned booked: true — if it returns booked: false, do not
    tell them the table is set; explain the reason and offer to take a message
    instead.

    Args:
        date: The reservation date as YYYY-MM-DD.
        time: The reservation time as 24-hour HH:MM.
        guests_count: Number of guests, as a string, e.g. "5".
        caller_name: The name to hold the table under. Required — ask the caller
            for their name if they haven't given one; never pass a blank or
            placeholder value just to get the call through.
        caller_phone: Caller's callback number if given, otherwise empty string.
    """
    if not caller_name.strip():
        await params.result_callback(
            {"booked": False, "reason": "caller name is required — ask for it before booking"}
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
