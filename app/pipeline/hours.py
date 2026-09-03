"""Deterministic kitchen-hours validation, used by the reservation tools.

Checked in code rather than trusted to the LLM's own date/time reasoning —
same "structurally enforce it, don't just rely on the model getting it
right" approach as LogInteractionEnforcer. A rule this concrete (does 9 PM
fall inside the 7-11 PM block?) shouldn't depend on the model's arithmetic
being correct on every single call.
"""

from __future__ import annotations

from datetime import datetime

from app.config.restaurants import Restaurant

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def is_within_hours(restaurant: Restaurant, date_str: str, time_str: str) -> tuple[bool, str]:
    """Check whether `date_str` (YYYY-MM-DD) + `time_str` (24-hour HH:MM) falls
    inside one of the restaurant's open blocks for that day of the week.

    Returns (True, "") if bookable, else (False, reason).

    Deliberately only checks the requested date/time against the posted
    hours calendar — never against the current date/time. A call at 3 AM
    asking for a table at 9 PM that same day is completely normal; "already
    passed relative to when the call happened" isn't a concept this function
    applies, only "does the kitchen have that slot open."
    """
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False, f"'{date_str}' isn't a valid date (expected YYYY-MM-DD)"

    try:
        time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return False, f"'{time_str}' isn't a valid time (expected 24-hour HH:MM)"

    blocks = restaurant.hours.get(date.weekday(), [])
    if not blocks:
        return False, f"closed on {_DAY_NAMES[date.weekday()]}s"

    for open_str, close_str in blocks:
        open_time = datetime.strptime(open_str, "%H:%M").time()
        close_time = datetime.strptime(close_str, "%H:%M").time()
        if open_time <= time <= close_time:
            return True, ""

    windows = " and ".join(f"{o}–{c}" for o, c in blocks)
    return False, f"outside operating hours on {_DAY_NAMES[date.weekday()]}s ({windows})"
