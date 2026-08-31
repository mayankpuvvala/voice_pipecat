"""The `end_call` tool: hangs up once the conversation is actually over.

Without this, a call just sits connected after a "goodbye" exchange until
the caller hangs up or the platform's own idle timeout kicks in. Queues an
EndWorkerFrame — pipecat flushes whatever's still queued (the bot's own
farewell reply, mid-flight) before actually closing the connection, so this
never cuts the bot off mid-sentence.
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
    """
    app_resources = params.app_resources or {}

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
