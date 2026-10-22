from __future__ import annotations

import asyncio
import json

import pytest

from app.pipeline import hours
from app.tools import reservations
from app.tools.reservations import (
    _already_booked_this_call,
    _confirmed_since_last_availability_check,
    book_table,
    book_table_succeeded_this_call,
    check_availability,
    parse_guests,
)
from tests.conftest import FakeParams


def _call(cid, name, **args):
    return {
        "role": "assistant",
        "tool_calls": [{"id": cid, "function": {"name": name, "arguments": json.dumps(args)}}],
    }


def _result(cid, **content):
    return {"role": "tool", "tool_call_id": cid, "content": json.dumps(content)}


ARGS = dict(date="2026-11-01", time="20:00", guests_count="4", caller_name="Priya")


@pytest.fixture(autouse=True)
def always_open(monkeypatch):
    """Hours are covered in test_hours.py; here every slot is bookable."""
    monkeypatch.setattr(reservations, "is_within_hours", lambda *_: (True, ""))


@pytest.fixture
def rows(monkeypatch):
    written = []
    monkeypatch.setattr(reservations.sheets_client, "append_row", lambda sheet, row: written.append(row))
    return written


@pytest.mark.parametrize("value,expected", [("4", 4), (" 10 ", 10), ("0", None), ("-3", None), ("abc", None), ("", None), ("501", None), ("2.5", None)])
def test_parse_guests(value, expected):
    assert parse_guests(value) == expected


def test_availability_rejects_bad_guest_count():
    p = FakeParams()
    asyncio.run(check_availability(p, "2026-11-01", "20:00", "0"))
    assert p.results[0]["available"] is False


def test_availability_ok():
    p = FakeParams()
    asyncio.run(check_availability(p, "2026-11-01", "20:00", "4"))
    assert p.results[0]["available"] is True


def test_confirmed_requires_user_turn_after_available_check():
    msgs = [_call("1", "check_availability"), _result("1", available=True)]
    assert not _confirmed_since_last_availability_check(FakeParams(msgs).context)
    msgs.append({"role": "user", "content": "yes"})
    assert _confirmed_since_last_availability_check(FakeParams(msgs).context)


def test_book_refuses_without_confirmation(rows):
    p = FakeParams([_call("1", "check_availability"), _result("1", available=True)])
    asyncio.run(book_table(p, "2026-11-01", "20:00", "4", "Priya"))
    assert p.results[0]["booked"] is False and not rows


def _confirmed_msgs():
    return [
        _call("1", "check_availability"),
        _result("1", available=True),
        {"role": "user", "content": "yes"},
    ]


def test_book_writes_normalized_row(rows):
    p = FakeParams(_confirmed_msgs(), {"call_session_id": "s1", "caller_phone": "+911"})
    asyncio.run(book_table(p, "2026-11-1", "9:05", " 4 ", "  Priya  "))
    assert p.results[0]["booked"] is True
    assert rows[0]["Date"] == "2026-11-01" and rows[0]["Time"] == "09:05"
    assert rows[0]["GuestsCount"] == "4" and rows[0]["CallerName"] == "Priya"
    assert rows[0]["CallerPhone"] == "+911"


def test_book_rejects_blank_name_and_bad_guests(rows):
    p = FakeParams(_confirmed_msgs())
    asyncio.run(book_table(p, "2026-11-01", "20:00", "4", "   "))
    asyncio.run(book_table(p, "2026-11-01", "20:00", "zero", "Priya"))
    assert [r["booked"] for r in p.results] == [False, False] and not rows


def test_book_name_is_length_capped(rows):
    p = FakeParams(_confirmed_msgs())
    asyncio.run(book_table(p, "2026-11-01", "20:00", "4", "A" * 500))
    assert len(rows[0]["CallerName"]) == 80


def test_second_identical_book_does_not_write_again(rows):
    msgs = _confirmed_msgs() + [_call("2", "book_table", **ARGS), _result("2", booked=True)]
    p = FakeParams(msgs)
    asyncio.run(book_table(p, "2026-11-01", "20:00", "4", "priya"))
    assert p.results[0]["booked"] is True and not rows


def test_already_booked_matches_only_identical_slot():
    msgs = [_call("2", "book_table", **ARGS), _result("2", booked=True)]
    ctx = FakeParams(msgs).context
    assert _already_booked_this_call(ctx, "2026-11-01", "20:00", "4", "Priya")
    assert not _already_booked_this_call(ctx, "2026-11-01", "21:00", "4", "Priya")
    assert not _already_booked_this_call(ctx, "2026-11-01", "20:00", "5", "Priya")


def test_failed_booking_is_not_counted_as_booked():
    msgs = [_call("2", "book_table", **ARGS), _result("2", booked=False)]
    ctx = FakeParams(msgs).context
    assert not _already_booked_this_call(ctx, "2026-11-01", "20:00", "4", "Priya")
    assert not book_table_succeeded_this_call(ctx)
