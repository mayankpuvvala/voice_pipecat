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
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame
from pipecat.services.llm_service import FunctionCallParams


async def end_call(params: FunctionCallParams) -> None:
    """End the call now that the conversation is over.

    Only call this after BOTH of these are true: you have already said your
    own goodbye out loud in this same response, AND the caller has indicated
    they're done (said bye, thanks, that's all, etc.). Never call this
    before your own farewell has been spoken.

    If this returns ended: false, the call is still connected — nothing has
    been said to the caller yet this turn. Say your goodbye (and, if you
    just booked a table, the confirmation read-back) out loud first, then
    call end_call again.
    """
    app_resources = params.app_resources or {}

    turn_taking_guard = app_resources.get("turn_taking_guard")
    if turn_taking_guard is not None and not turn_taking_guard.has_spoken_this_turn():
        logger.warning(
            "end_call: refused — no spoken text has reached the caller this turn yet"
        )
        await params.result_callback(
            {
                "ended": False,
                "reason": (
                    "nothing has been said to the caller yet this turn — speak your "
                    "goodbye (and reservation confirmation, if applicable) out loud "
                    "before calling end_call"
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
