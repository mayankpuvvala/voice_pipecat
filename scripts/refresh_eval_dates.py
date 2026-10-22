"""Rewrites the hardcoded dates in eval_scenarios/*.yaml so they're never stale.

The eval format has no templating, so every "book for 2026-10-21" fixture rots
the day after it was written. This rewrites each ISO date to a computed one,
in the active restaurant's timezone:

  - default:                         tomorrow
  - a file containing the comment
    `# eval-date: next-closed-day`   the next Monday after tomorrow (Spice Route
                                     is closed Mondays; scenario 04 needs that)

Free, offline, idempotent. Usage (repo root):

    python scripts/refresh_eval_dates.py            # rewrite files
    python scripts/refresh_eval_dates.py --check    # report only, exit 1 if stale
    python scripts/refresh_eval_dates.py --run      # rewrite, then run the suite
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "eval_scenarios"
TIMEZONE = "Asia/Kolkata"
_ISO_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
_CLOSED_DAY_MARKER = "eval-date: next-closed-day"
_MONDAY = 0


def tomorrow(now: datetime | None = None) -> date:
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    return now.date() + timedelta(days=1)


def next_closed_day(now: datetime | None = None) -> date:
    """First Monday strictly after tomorrow."""
    day = tomorrow(now) + timedelta(days=1)
    while day.weekday() != _MONDAY:
        day += timedelta(days=1)
    return day


def render(text: str, now: datetime | None = None) -> str:
    target = next_closed_day(now) if _CLOSED_DAY_MARKER in text else tomorrow(now)
    # Keep any "Monday, <date>" style wording true: the closed-day file names the weekday.
    return _ISO_DATE.sub(target.isoformat(), text)


def scenario_files() -> list[Path]:
    return sorted(p for p in SCENARIOS.glob("*.yaml") if p.name != "manifest.yaml")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--check", action="store_true", help="don't write; exit 1 if any file is stale")
    parser.add_argument("--run", action="store_true", help="refresh, then run the eval suite")
    args = parser.parse_args()

    stale = []
    for path in scenario_files():
        text = path.read_text(encoding="utf-8")
        new = render(text)
        if new != text:
            stale.append(path.name)
            if not args.check:
                path.write_text(new, encoding="utf-8", newline="")
    verb = "stale" if args.check else "updated"
    print(f"{len(stale)} scenario file(s) {verb}: {', '.join(stale) or '-'}")
    print(f"tomorrow={tomorrow()}  next-closed-day={next_closed_day()}")
    if args.check:
        return 1 if stale else 0

    if args.run:
        env_cmd = [sys.executable, "-m", "pipecat.cli.main", "eval", "suite", "eval_scenarios/manifest.yaml"]
        return subprocess.call(env_cmd, cwd=ROOT, env={**__import__("os").environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
