"""Process-lifetime active-call counter. Exists only to gate the idle
post-processing background job (app/pipeline/idle_post_processor.py) so it
never runs OpenAI/Sheets work while a real call is in progress — not a
per-call object, unlike everything else in app/pipeline. See app/main.py's
run_bot for where these are called.
"""

from __future__ import annotations

import time

_active = 0
_last_ended_at = time.monotonic()


def call_started() -> None:
    global _active
    _active += 1


def call_ended() -> None:
    global _active, _last_ended_at
    _active = max(0, _active - 1)
    _last_ended_at = time.monotonic()


def is_idle(min_idle_secs: float) -> bool:
    """True once there are zero active calls AND it's stayed that way for at
    least `min_idle_secs` — avoids triggering in the brief gap between two
    back-to-back calls."""
    return _active == 0 and (time.monotonic() - _last_ended_at) >= min_idle_secs
