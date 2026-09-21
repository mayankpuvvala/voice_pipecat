"""Aggregates fetch_calls() rows into dashboard numbers: minutes used this
month vs. the allowance, avg/min/max call length, topic breakdown, and
calls-by-hour. All derived from data already logged — no new capture.
Month resets on the calendar month, IST (matches app/admin/routes.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_TOPIC_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Reservation", ("reserv", "book", "table")),
    ("Cancellation", ("cancel",)),
    ("Menu & Cuisine", ("menu", "cuisine", "dish")),
    ("Hours & Location", ("hour", "location", "parking", "address")),
    ("Delivery & Takeout", ("deliver", "takeout")),
    ("Dietary & Allergen", ("dietary", "allerg", "ingredient", "vegan", "vegetarian", "gluten")),
    ("Pricing & Payment", ("price", "payment", "cost")),
    ("Escalation/Complaint", ("complain", "emergency", "manager", "owner")),
]


def categorize_topics(topics: list[str]) -> list[str]:
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


def _call_local_dt(call: dict[str, Any]) -> datetime | None:
    ts = call.get("timestamp") or ""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt.astimezone(IST)


def _call_minutes(call: dict[str, Any]) -> float | None:
    raw = call.get("duration_secs")
    if raw in (None, ""):
        return None
    try:
        return float(raw) / 60.0
    except ValueError:
        return None


def _week_start(now_ist: datetime) -> datetime:
    """Monday 00:00 IST of the current calendar week."""
    start_of_day = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day - timedelta(days=start_of_day.weekday())


def _previous_month(now_ist: datetime) -> tuple[int, int]:
    if now_ist.month == 1:
        return now_ist.year - 1, 12
    return now_ist.year, now_ist.month - 1


def _pct_change(current: float, previous: float) -> float:
    """Percentage change vs. the previous month. No previous-month data
    (e.g. this is the first month this restaurant has any calls at all) ->
    0.0, shown as a neutral "+0.0%" rather than an undefined/infinite jump."""
    if not previous:
        return 0.0
    return (current - previous) / previous * 100.0


def compute_stats(calls: list[dict[str, Any]], minutes_allowed_per_month: int) -> dict[str, Any]:
    """Everything the dashboard needs to render, for one restaurant's calls."""
    now_ist = datetime.now(IST)
    week_start = _week_start(now_ist)

    this_month_calls = [
        c for c in calls if (dt := _call_local_dt(c)) and dt.year == now_ist.year and dt.month == now_ist.month
    ]
    this_week_calls = [c for c in calls if (dt := _call_local_dt(c)) and dt >= week_start]

    prev_year, prev_month = _previous_month(now_ist)
    previous_month_calls = [
        c for c in calls if (dt := _call_local_dt(c)) and dt.year == prev_year and dt.month == prev_month
    ]
    previous_month_minutes = [m for c in previous_month_calls if (m := _call_minutes(c)) is not None]
    minutes_used_previous_month = sum(previous_month_minutes)
    followups_needed_previous_month = sum(1 for c in previous_month_calls if c.get("needs_followup"))

    all_minutes = [m for c in calls if (m := _call_minutes(c)) is not None]
    month_minutes = [m for c in this_month_calls if (m := _call_minutes(c)) is not None]

    topic_counts: dict[str, int] = {}
    for call in calls:
        for category in categorize_topics(call.get("topics") or []):
            topic_counts[category] = topic_counts.get(category, 0) + 1

    hour_counts = [0] * 24
    for call in calls:
        dt = _call_local_dt(call)
        if dt:
            hour_counts[dt.hour] += 1

    minutes_used_this_month = sum(month_minutes)
    minutes_remaining_this_month = (
        max(minutes_allowed_per_month - minutes_used_this_month, 0.0) if minutes_allowed_per_month else 0.0
    )
    followups_needed_this_month = sum(1 for c in this_month_calls if c.get("needs_followup"))

    return {
        "total_calls_all_time": len(calls),
        "total_calls_this_month": len(this_month_calls),
        "total_calls_this_week": len(this_week_calls),
        "followups_needed_this_month": followups_needed_this_month,
        "calls_pct_change": _pct_change(len(this_month_calls), len(previous_month_calls)),
        "followups_pct_change": _pct_change(followups_needed_this_month, followups_needed_previous_month),
        "minutes_pct_change": _pct_change(minutes_used_this_month, minutes_used_previous_month),
        "this_month_start_iso": now_ist.replace(day=1).strftime("%Y-%m-%d"),
        "today_iso": now_ist.strftime("%Y-%m-%d"),
        "minutes_used_this_month": minutes_used_this_month,
        "minutes_remaining_this_month": minutes_remaining_this_month,
        "minutes_allowed_per_month": minutes_allowed_per_month,
        "minutes_used_pct": (
            (minutes_used_this_month / minutes_allowed_per_month * 100)
            if minutes_allowed_per_month
            else 0.0
        ),
        "avg_call_minutes": (sum(all_minutes) / len(all_minutes)) if all_minutes else 0.0,
        "min_call_minutes": min(all_minutes) if all_minutes else 0.0,
        "max_call_minutes": max(all_minutes) if all_minutes else 0.0,
        "topic_counts": dict(sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "hour_counts": hour_counts,
        "peak_hour": max(range(24), key=lambda h: hour_counts[h]) if any(hour_counts) else None,
        "quiet_hour_with_calls": (
            min((h for h in range(24) if hour_counts[h] > 0), key=lambda h: hour_counts[h])
            if any(hour_counts)
            else None
        ),
    }
