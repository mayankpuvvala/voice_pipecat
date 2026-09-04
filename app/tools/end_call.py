"""The `end_call` tool: hangs up once the conversation is actually over.

Without this, a call just sits connected after a "goodbye" exchange until
the caller hangs up or the platform's own idle timeout kicks in. Queues an
EndWorkerFrame — pipecat flushes whatever's still queued (the bot's own
farewell reply, mid-flight) before actually closing the connection, so this
never cuts the bot off mid-sentence.

_END_CALL_INSTRUCTION in prompts.py already tells the model never to call
this before its own goodbye has been spoken — but that's prompt-only, and
was confirmed live to not hold 100% of the time: a real eval run had the
model call book_table -> logInteraction -> end_call in one response with
ZERO spoken text anywhere in the sequence, hanging up on a caller who'd
just confirmed a reservation with no idea whether it was actually booked.
Same lesson as book_table's own _confirmed_since_last_availability_check
gate (reservations.py) -- prompt wording alone isn't structurally
enforceable, so this checks turn_taking_guard's own record of whether
anything has actually reached the caller this turn before allowing the
call to actually end.

A second, worse pattern showed up re-verifying that fix: refused by the
gate above, the model responded by *narrating a fabricated booking
confirmation* ("You're all set, Vikram!... table for two...") instead of
actually calling book_table — reproduced live twice. A caller told their
table is booked when it never was is a worse outcome than the silent
hangup this file was originally built to prevent, so this also checks the
actual spoken text against book_table's own real result before letting the
call end, not just "was something said."
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame
from pipecat.services.llm_service import FunctionCallParams

from app.tools.reservations import book_table_succeeded_this_call

# Deliberately over-triggers rather than under-triggers: the failure mode on
# a false positive is a wasted refusal-and-retry (mildly annoying, the model
# just tries again), while the failure mode on a false negative is a caller
# told they have a table that doesn't exist. Covers the exact phrasings
# confirmed live ("Your table is confirmed...", "it's all set!", "You're all
# set, Vikram!") plus the obvious neighbors.
_CLAIMS_BOOKING_CONFIRMED = re.compile(
    r"\b(it'?s all set|you'?re all set|"
    r"table(?:'s| is)? (?:confirmed|booked|(?:all )?set|held|ready)|"
    r"reservation(?:'s| is)? (?:confirmed|booked|set))\b",
    re.IGNORECASE,
)


async def end_call(params: FunctionCallParams) -> None:
    """End the call now that the conversation is over.

    Only call this after BOTH of these are true: you have already said your
    own goodbye out loud in this same response, AND the caller has indicated
    they're done (said bye, thanks, that's all, etc.). Never call this
    before your own farewell has been spoken.

    If this returns ended: false, the call is still connected. Read the
    reason and act on it: if nothing has been said yet, speak your goodbye
    out loud first, then call end_call again. If you were told you claimed a
    booking without one, call book_table now (never fabricate a
    confirmation) and only tell the caller it's booked once that actually
    returns booked: true — then call end_call again.
    """
    app_resources = params.app_resources or {}

    turn_taking_guard = app_resources.get("turn_taking_guard")
    if turn_taking_guard is not None:
        if not turn_taking_guard.has_spoken_this_turn():
            logger.warning(
                "end_call: refused — no spoken text has reached the caller this turn yet"
            )
            await params.result_callback(
                {
                    "ended": False,
                    "reason": (
                        "nothing has been said to the caller yet this turn — speak your "
                        "goodbye out loud before calling end_call. If a reservation is "
                        "involved, only confirm it out loud once book_table has actually "
                        "returned booked: true — never say a table is booked before that."
                    ),
                }
            )
            return

        spoken = turn_taking_guard.spoken_text_this_turn()
        if _CLAIMS_BOOKING_CONFIRMED.search(spoken) and not book_table_succeeded_this_call(
            params.context
        ):
            logger.warning(
                "end_call: refused — spoken text claims a booking that book_table never "
                "confirmed (spoken={!r})",
                spoken,
            )
            await params.result_callback(
                {
                    "ended": False,
                    "reason": (
                        "you just told the caller their table is booked or confirmed, but "
                        "book_table has not actually returned booked: true this call — "
                        "never claim a reservation is confirmed without that. Call "
                        "book_table now if you haven't successfully done so yet. If the "
                        "caller doesn't actually want the table, don't claim one is held; "
                        "just say goodbye normally."
                    ),
                }
            )
            return

    # A bot-initiated close doesn't fire on_client_disconnected (the
    # transport only raises that for a caller-initiated close), so the
    # transcript has to be captured here instead of relying on that handler.
    capture_transcript = app_resources.get("capture_transcript")
    if capture_transcript is not None:
        capture_transcript()

    await params.result_callback({"ended": True})

    worker_handle = app_resources.get("worker_handle")
    if worker_handle is not None and worker_handle.worker is not None:
        await worker_handle.worker.queue_frames([EndWorkerFrame()])
    else:
        logger.warning("end_call: no worker available to end the call")
