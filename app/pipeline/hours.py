"""Deterministic kitchen-hours validation, used by the reservation tools.

Checked in code rather than trusted to the LLM's own date/time reasoning —
a rule this concrete shouldn't depend on the model's arithmetic.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config.restaurants import Restaurant

MAX_DAYS_AHEAD = 365


def normalize_slot(date_str: str, time_str: str) -> tuple[str, str]:
    """Canonical YYYY-MM-DD / HH:MM for storage ("2026-1-5", "9:5" -> padded).
    Returns the inputs unchanged if they don't parse; is_within_hours
    reports that error itself."""
    try:
        date_str = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
        time_str = datetime.strptime(time_str, "%H:%M").strftime("%H:%M")
    except ValueError:
        pass
    return date_str, time_str


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def is_within_hours(restaurant: Restaurant, date_str: str, time_str: str) -> tuple[bool, str]:
    """Check whether `date_str` (YYYY-MM-DD) + `time_str` (24-hour HH:MM)
    falls inside one of the restaurant's open blocks that day.
    Returns (True, "") if bookable, else (False, reason). Rejects a past
    date, a same-day time that has already passed, and a date more than
    MAX_DAYS_AHEAD out.
    """
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False, f"'{date_str}' isn't a valid date (expected YYYY-MM-DD)"

    try:
        time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return False, f"'{time_str}' isn't a valid time (expected 24-hour HH:MM)"

    now = datetime.now(ZoneInfo(restaurant.timezone))
    today = now.date()
    if date < today:
        return False, f"'{date_str}' is in the past"
    if date == today and time < now.time().replace(second=0, microsecond=0):
        return False, f"{time_str} has already passed today"
    if (date - today).days > MAX_DAYS_AHEAD:
        return False, f"'{date_str}' is too far ahead to book"

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
