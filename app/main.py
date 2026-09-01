"""Entrypoint. Run with `python -m app.main -t <provider>` where <provider>
is one of exotel/twilio/telnyx/plivo — see README.md / Dockerfile's
TELEPHONY_TRANSPORT env var for how this is picked without editing code.

The Pipecat dev runner (`pipecat.runner.run.main`) starts a local FastAPI
server. Every supported provider connects over a plain WebSocket (`/ws`)
speaking that provider's own media-streaming protocol — Exotel's is the
production target (configured as the Voicebot Applet in Exotel's App
Bazaar); the others exist so testing/dev can use a free-trial or
pay-as-you-go number while Exotel/TRAI setup is still in progress. See
README.md for the call path from Jio → Exotel → this server.
"""

from __future__ import annotations

import os
import sys

from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.evals.transport import EvalTransportParams
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

from app.admin.routes import register_admin_routes
from app.config.restaurants.spice_route_kitchen import SPICE_ROUTE_KITCHEN
from app.config.settings import settings
from app.pipeline.logging_enforcer import LogInteractionEnforcer, WorkerHandle
from app.pipeline.prompts import build_system_prompt
from app.pipeline.recording import save_call_recording
from app.pipeline.transcript import build_transcript
from app.services.twilio_client import lookup_caller_number
from app.tools.end_call import end_call
from app.tools.log_interaction import log_interaction
from app.tools.reservations import book_table, check_availability

# pipecat.runnroutes before calling main() — see that module's docstring.
import pipecat.runner.run as pipecat_runner
from pipecat.runner.run import app as runner_app
from pipecat.runner.run import main as run_dev_server

pipecat_runner._setup_frontend_routes = lambda app: None


@runner_app.get("/", include_in_schema=False)
async def root_status() -> dict:
    return {
        "status": "ok",
        "service": f"{SPICE_ROUTE_KITCHEN.name} voice agent",
        "note": "Telephony only, no browser test client — see /admin for call logs.",
        # Railway injects this at runtime; lets us confirm which commit is
        # actually live without dashboard access (see RAILWAY_GIT_COMMIT_SHA
        # in Railway's docs).
        "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"),
    }


register_admin_routes(runner_app)


@runner_app.on_event("startup")
async def _reduce_log_verbosity() -> None:
    # pipecat's dev runner (run_dev_server, called below) hardcodes its own
    # DEBUG-level sink during arg parsing — this replaces it once the app is
    # actually up, so Railway's logs aren't flooded with per-frame TTFB/TTS
    # debug lines on every call. Our own logger.info/.exception calls stay
    # visible either way.
    logger.remove()
    logger.add(sys.stderr, level="INFO")


transport_params = {
    "exotel": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "twilio": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "telnyx": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "plivo": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "eval": lambda: EvalTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    logger.info("Starting bot for {}", SPICE_ROUTE_KITCHEN.name)

    call_session_id = getattr(runner_args, "session_id", None) or ""
    call_data = getattr(runner_args, "call_data", None)
    caller_phone = getattr(call_data, "from_number", None) or ""
    if not caller_phone and getattr(runner_args, "transport_type", None) == "twilio" and call_data:
        caller_phone = await lookup_caller_number(getattr(call_data, "call_id", "") or "")

    stt = SarvamSTTService(
        api_key=settings.sarvam_api_key,
        # Pinned to Hindi rather than left on auto-detect: saaras:v3's
        # auto-detect ("unknown") picks among all 12 Indian languages Sarvam
        # supports, and was misfiring as Tamil/Telugu on callers who only
        # spoke English/Hindi/Hinglish. Sarvam only accepts one fixed
        # language per connection, not a restricted subset — hi-IN is the
        # closest fit for a Hindi/English/Hinglish caller base since Hindi
        # STT models handle code-switched English words natively.
        settings=SarvamSTTService.Settings(model="saaras:v3", language=Language.HI_IN),
    )

    llm = OpenAILLMService(
        api_key=settings.openai_api_key,
        settings=OpenAILLMService.Settings(
            model=settings.openai_model,
            system_instruction=build_system_prompt(SPICE_ROUTE_KITCHEN),
        ),
    )

    tts = OpenAITTSService(
        api_key=settings.openai_api_key,
        voice=settings.openai_tts_voice,
    )

    context = LLMContext(tools=[log_interaction, check_availability, book_table, end_call])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    worker_handle = WorkerHandle()
    logging_enforcer = LogInteractionEnforcer(context, worker_handle)

    audiobuffer = AudioBufferProcessor(auto_start_recording=True)
    call_state = {"transcript": ""}

    def capture_transcript() -> None:
        # Shared by caller-hangup (on_client_disconnected, below) and a
        # bot-initiated hangup (the end_call tool): the websocket transport
        # only fires on_client_disconnected when the *caller* closes the
        # connection (see FastAPIWebsocketClient._receive_messages' `if not
        # self._client.is_closing` guard) — it does NOT fire when we close
        # it ourselves via EndWorkerFrame. Without this, a call the bot ends
        # itself would save a recording with no transcript.
        call_state["transcript"] = build_transcript(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            logging_enforcer,
            tts,
            audiobuffer,
            transport.output(),
            assistant_aggregator,
        ]
    )

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        await save_call_recording(
            call_session_id,
            caller_phone,
            audio,
            sample_rate,
            num_channels,
            transcript=call_state["transcript"],
        )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        app_resources={
            "call_session_id": call_session_id,
            "caller_phone": caller_phone,
            "worker_handle": worker_handle,
            "capture_transcript": capture_transcript,
        },
    )
    worker_handle.worker = worker

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Caller connected")
        await worker.queue_frames([TTSSpeakFrame(SPICE_ROUTE_KITCHEN.first_message)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Caller disconnected")
        capture_transcript()
        await runner.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the Pipecat dev runner looks for."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    run_dev_server()
