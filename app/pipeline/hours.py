"""Deterministic kitchen-hours validation, used by the reservation tools.

Checked in code rather than trusted to the LLM's own date/time reasoning —
a rule this concrete shouldn't depend on the model's arithmetic.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config.restaurants import Restaurant

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def is_within_hours(restaurant: Restaurant, date_str: str, time_str: str) -> tuple[bool, str]:
    """Check whether `date_str` (YYYY-MM-DD) + `time_str` (24-hour HH:MM)
    falls inside one of the restaurant's open blocks that day.
    Returns (True, "") if bookable, else (False, reason). Rejects a past
    date, but not a same-day time already "passed" today.
    """
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False, f"'{date_str}' isn't a valid date (expected YYYY-MM-DD)"

    try:
        time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return False, f"'{time_str}' isn't a valid time (expected 24-hour HH:MM)"

    today = datetime.now(ZoneInfo(restaurant.timezone)).date()
    if date < today:
        return False, f"'{date_str}' is in the past"

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
