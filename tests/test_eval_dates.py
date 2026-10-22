from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "refresh_eval_dates", Path(__file__).resolve().parent.parent / "scripts" / "refresh_eval_dates.py"
)
red = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(red)

# Thursday 2026-10-22 22:00 IST
NOW = datetime(2026, 10, 22, 22, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def test_default_files_get_tomorrow():
    out = red.render('user: "Tomorrow, 2026-01-01, at 8 PM" args: { date: "2025-05-05" }', NOW)
    assert out.count("2026-10-23") == 2 and "2026-01-01" not in out


def test_closed_day_marker_gets_a_monday_after_tomorrow():
    out = red.render("# eval-date: next-closed-day\nuser: Monday, 2026-10-26", NOW)
    assert "2026-10-26" in out  # Thursday -> next Monday
    assert red.next_closed_day(NOW).weekday() == 0


def test_closed_day_skips_tomorrow_if_tomorrow_is_monday():
    sunday = datetime(2026, 10, 25, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert red.next_closed_day(sunday).isoformat() == "2026-11-02"


def test_render_is_idempotent():
    once = red.render("date: 2020-02-02", NOW)
    assert red.render(once, NOW) == once
