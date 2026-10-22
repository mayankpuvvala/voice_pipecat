from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config.restaurants import Restaurant
from app.pipeline import hours
from app.pipeline.hours import is_within_hours, normalize_slot

TZ = "Asia/Kolkata"
# 2026-10-22 is a Thursday. Mon closed, other days 12:00-23:00.
RESTAURANT = Restaurant(
    name="T",
    bot_name="B",
    first_message="hi",
    end_call_message="bye",
    system_prompt="p",
    timezone=TZ,
    hours={d: [("12:00", "23:00")] for d in range(1, 7)},
)


@pytest.fixture
def clock(monkeypatch):
    """Freeze 'now' at Thu 2026-10-22 18:30 IST."""

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 22, 18, 30, tzinfo=ZoneInfo(TZ))

    monkeypatch.setattr(hours, "datetime", FakeDT)


def test_open_slot_ok(clock):
    assert is_within_hours(RESTAURANT, "2026-10-23", "20:00") == (True, "")


def test_closed_day(clock):
    ok, reason = is_within_hours(RESTAURANT, "2026-10-26", "20:00")  # Monday
    assert not ok and "Monday" in reason


def test_before_open_and_after_close(clock):
    assert not is_within_hours(RESTAURANT, "2026-10-23", "11:59")[0]
    assert not is_within_hours(RESTAURANT, "2026-10-23", "23:01")[0]


def test_past_date(clock):
    ok, reason = is_within_hours(RESTAURANT, "2026-10-21", "20:00")
    assert not ok and "past" in reason


def test_same_day_time_already_passed(clock):
    ok, reason = is_within_hours(RESTAURANT, "2026-10-22", "13:00")
    assert not ok and "already passed" in reason


def test_same_day_later_time_ok(clock):
    assert is_within_hours(RESTAURANT, "2026-10-22", "21:00")[0]


def test_too_far_ahead(clock):
    ok, reason = is_within_hours(RESTAURANT, "2099-01-01", "20:00")
    assert not ok and "too far" in reason


@pytest.mark.parametrize("date,time", [("tomorrow", "20:00"), ("2026-10-23", "8pm"), ("", "")])
def test_garbage_input_is_rejected_not_raised(clock, date, time):
    assert is_within_hours(RESTAURANT, date, time)[0] is False


def test_normalize_slot_pads_and_passes_garbage_through():
    assert normalize_slot("2026-1-5", "9:5") == ("2026-01-05", "09:05")
    assert normalize_slot("tomorrow", "8pm") == ("tomorrow", "8pm")
