"""The `end_call` tool: hangs up once the conversation is actually over.

Queues an EndWorkerFrame — pipecat flushes any already-queued farewell
reply before closing the connection, so this never cuts the bot off
mid-sentence.

Prompt wording alone doesn't reliably stop the model from ending calls
wrong (confirmed live twice), so this enforces two gates structurally via
turn_taking_guard:
1. Refuse if nothing has been spoken to the caller this turn yet (a real
   run called book_table -> logInteraction -> end_call with zero spoken
   text, hanging up with the caller unsure if they had a table).
2. Refuse if the spoken text claims a booking that book_table never
   actually confirmed (the model narrated a fake "you're all set"
   instead of calling book_table — worse than the silent hangup above).
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame
from pipecat.services.llm_service import FunctionCallParams

from app.tools.reservations import book_table_succeeded_this_call

# Deliberately over-triggers: a false positive just costs a retry, a false
# negative tells a caller they have a table that doesn't exist.
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

    # on_client_disconnected only fires for a caller-initiated close, so a
    # bot-initiated one has to capture the transcript here instead.
    capture_transcript = app_resources.get("capture_transcript")
    if capture_transcript is not None:
        capture_transcript()

    await params.result_callback({"ended": True})

    worker_handle = app_resources.get("worker_handle")
    if worker_handle is not None and worker_handle.worker is not None:
        await worker_handle.worker.queue_frames([EndWorkerFrame()])
    else:
        logger.warning("end_call: no worker available to end the call")
