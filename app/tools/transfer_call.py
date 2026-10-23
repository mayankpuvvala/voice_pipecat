"""The `transfer_call` tool: hands the caller to a real person, live.

Only offered on calls where it can actually work — real Plivo or Vobiz
with REST credentials and a transfer number configured (see
app/main.py's run_bot); everywhere else the bot keeps the existing
take-a-message flow. Same structural gate as end_call: refuse if nothing
was spoken yet this turn, so the caller always hears a handoff line first.

Ordering matters here more than in end_call. Once the provider's Transfer
API moves the caller's leg, our <Stream> is gone, so this (1) waits for the
handoff line to actually finish playing, (2) disarms real Plivo's
hang-up-on-pipeline-end so tearing down the pipeline can't hang up the
transferred caller, and only then (3) requests the transfer. The provider
closes our WebSocket itself once the leg moves, which ends the pipeline
via on_client_disconnected; a fallback timer ends it anyway if that never
comes.

Never raises. Any failure returns transferred: false with a reason, after
speaking a short apology directly — the post-tool LLM continuation that
would normally apologise is swallowed by LogInteractionEnforcer/
turn_taking_guard once the handoff line has already been spoken this turn,
which would otherwise leave the caller in dead air.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

from app.services.call_transfer import TransferLine, request_transfer
from app.tools.log_interaction import write_interaction_row

# If the provider hasn't closed our WebSocket this long after accepting the
# transfer, end the pipeline ourselves (auto hang-up is already disarmed,
# so this only drops our side of a stream the provider already left).
_PROVIDER_TEARDOWN_GRACE_SECS = 10.0

FAILURE_APOLOGY = (
    "Sorry, I couldn't connect you right now. Can I take your name and "
    "number so the owner can call you back?"
)

# Background tasks this module started — held so they aren't garbage
# collected mid-flight (asyncio only keeps weak references to tasks).
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _log_escalation(app_resources: dict, reason: str, caller_name: str) -> None:
    """Counts the transfer as an escalation through the same row-write that
    fires the owner's SMS + WhatsApp — never a second alert path."""
    try:
        await write_interaction_row(
            call_session_id=app_resources.get("call_session_id", ""),
            caller_phone=app_resources.get("caller_phone", ""),
            topic="live transfer to staff",
            resolved=False,
            caller_name=caller_name,
            details=reason,
        )
    except Exception:
        logger.exception("transfer_call: failed to log the escalation row")


async def _end_pipeline_if_still_running(worker_handle) -> None:
    await asyncio.sleep(_PROVIDER_TEARDOWN_GRACE_SECS)
    worker = getattr(worker_handle, "worker", None)
    if worker is not None and not worker.has_finished():
        logger.info(
            "transfer_call: provider hasn't closed the stream {}s after transfer — "
            "ending the pipeline ourselves",
            _PROVIDER_TEARDOWN_GRACE_SECS,
        )
        await worker.queue_frames([EndWorkerFrame()])


async def _fail(params: FunctionCallParams, reason: str, *, apology_spoken: bool) -> None:
    app_resources = params.app_resources or {}
    if apology_spoken:
        worker_handle = app_resources.get("worker_handle")
        if worker_handle is not None and worker_handle.worker is not None:
            await worker_handle.worker.queue_frames([TTSSpeakFrame(FAILURE_APOLOGY)])
        await params.result_callback(
            {
                "transferred": False,
                "reason": reason,
                "next_step": (
                    "The caller is still on the line with you, and has already heard "
                    "you apologise and offer to take their name and number for the "
                    "owner — don't repeat that. Wait for their answer, then continue "
                    "the take-a-message flow."
                ),
            }
        )
    else:
        await params.result_callback(
            {
                "transferred": False,
                "reason": reason,
                "next_step": (
                    "Live transfer isn't possible. Apologise briefly and offer to take "
                    "their name and number so the owner can call them back."
                ),
            }
        )


async def transfer_call(params: FunctionCallParams, reason: str, caller_name: str = "") -> None:
    """Transfer this call to a real staff member, live.

    Use when the caller explicitly asks for a human, a real person, a
    manager, or the owner; when they're making a complaint; or when they
    need something you can't do yourself and want to speak to someone now.
    Only call this after you have said a short handoff line out loud in
    this same response (e.g. "Sure, let me connect you to the manager —
    one moment."). Never call it before that line is spoken.

    If this returns transferred: false, the caller is still on the line
    with you. Follow its next_step exactly.

    Args:
        reason: One short sentence for the owner on why the caller is being
            transferred, e.g. "complaint about a cold delivery order".
        caller_name: Caller's name if they gave it, otherwise empty string.
            Always in English/Latin letters.
    """
    app_resources = params.app_resources or {}
    transfer_line: TransferLine | None = app_resources.get("transfer_line")
    turn_taking_guard = app_resources.get("turn_taking_guard")
    spoken = turn_taking_guard is not None and turn_taking_guard.has_spoken_this_turn()

    # Checked before the spoken-line gate on purpose: if transfer can't
    # happen at all, better the model hears that now than first announcing
    # a handoff that will never come.
    if transfer_line is None:
        logger.warning("transfer_call: called on a call without live transfer")
        await _fail(params, "live transfer isn't available on this call", apology_spoken=spoken)
        return
    if not transfer_line.target_number:
        logger.warning("transfer_call: no transfer_number/owner_phone configured")
        await _fail(params, "no transfer number is configured", apology_spoken=spoken)
        return

    if turn_taking_guard is not None and not spoken:
        logger.warning("transfer_call: refused — no handoff line has reached the caller yet")
        await params.result_callback(
            {
                "transferred": False,
                "reason": (
                    "nothing has been said to the caller yet this turn — say a short "
                    "handoff line out loud first (e.g. \"Sure, let me connect you — "
                    "one moment.\"), then call transfer_call again in that same response."
                ),
            }
        )
        return

    # Fired now, not awaited: the Sheets write + SMS/WhatsApp take seconds
    # and must not delay the handoff. A failed transfer still needs the
    # owner's follow-up, so this is right either way.
    _spawn(_log_escalation(app_resources, reason, caller_name))

    bot_speech_tracker = app_resources.get("bot_speech_tracker")
    if bot_speech_tracker is not None:
        await bot_speech_tracker.wait_until_quiet()

    if transfer_line.set_auto_hang_up is not None:
        transfer_line.set_auto_hang_up(False)
    succeeded = False
    try:
        if transfer_line.provider == "simulated":
            logger.info("transfer_call: simulated transfer (eval) — not dialing")
            succeeded = True
        else:
            result = await request_transfer(
                transfer_line.provider, transfer_line.call_uuid, transfer_line.aleg_url
            )
            succeeded = result.ok
            if not succeeded:
                await _fail(params, result.reason, apology_spoken=True)
                return
    except Exception as e:
        # request_transfer never raises; this is pure belt-and-braces.
        logger.exception("transfer_call: unexpected error")
        await _fail(params, f"unexpected error ({type(e).__name__})", apology_spoken=True)
        return
    finally:
        # Re-arm on any failure (or interruption-cancel) so a normal hang-up
        # later in this call still ends it on the provider's side.
        if not succeeded and transfer_line.set_auto_hang_up is not None:
            transfer_line.set_auto_hang_up(True)

    capture_transcript = app_resources.get("capture_transcript")
    if capture_transcript is not None:
        capture_transcript()

    await params.result_callback({"transferred": True})

    worker_handle = app_resources.get("worker_handle")
    if transfer_line.provider == "simulated":
        # Nothing on the other end will close the stream for us.
        if worker_handle is not None and worker_handle.worker is not None:
            await worker_handle.worker.queue_frames([EndWorkerFrame()])
    else:
        _spawn(_end_pipeline_if_still_running(worker_handle))
