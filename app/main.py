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

Vobiz is also wired up (`/answer` + `/hangup` below) independently of `-t`:
its WebSocket protocol is wire-compatible with Plivo's, so it needs no CLI
transport flag of its own — a Vobiz call auto-detects as "plivo" and gets
picked up by `_create_vobiz_transport()` in `bot()` regardless of which
`-t` value the process was started with. Point a Vobiz Application's
"Primary answer URL" at `<this server>/answer` and "Hangup URL" at
`<this server>/hangup`; it can run alongside Exotel on the same deployment.
"""

from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response
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
from pipecat.runner.types import RunnerArguments, WebSocketRunnerArguments
from pipecat.runner.utils import create_transport, parse_telephony_websocket
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.evals.transport import EvalTransportParams
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from app.admin.routes import register_admin_routes
from app.config.restaurants import ACTIVE_RESTAURANT
from app.config.settings import settings
from app.pipeline.logging_enforcer import LogInteractionEnforcer, WorkerHandle
from app.pipeline.prompts import build_system_prompt
from app.pipeline.recording import save_call_recording
from app.pipeline.second_paragraph_filter import SecondParagraphFilter
from app.pipeline.tracing import setup_call_tracing
from app.pipeline.transcript import build_transcript
from app.pipeline.turn_taking_guard import OneUtterancePerTurnGuard
from app.services.rumik_tts import RumikTTSService
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
        "service": f"{ACTIVE_RESTAURANT.name} voice agent",
        "note": "Telephony only, no browser test client — see /admin for call logs.",
        # Railway injects this at runtime; lets us confirm which commit is
        # actually live without dashboard access (see RAILWAY_GIT_COMMIT_SHA
        # in Railway's docs).
        "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"),
    }


register_admin_routes(runner_app)


@runner_app.post("/answer", include_in_schema=False)
async def vobiz_answer(request: Request) -> Response:
    """Vobiz's primary answer URL: return XML pointing it at our WS stream.

    Vobiz's ``<Stream>`` protocol is Plivo's Audio Streaming API — same XML
    attributes, same playAudio/clearAudio/media JSON shape (confirmed against
    github.com/vobiz-ai/Vobiz-Python-Voice-API-Example) — but its handshake
    doesn't carry the caller's number, so we thread it through as a query
    param instead of relying on pipecat's provider-detected call_data. See
    `bot()` below for how the stream is actually picked up.
    """
    form = await request.form()
    caller = str(form.get("From", "") or "")
    called = str(form.get("To", "") or "")
    ws_url = f"wss://{request.headers.get('host')}/ws?from={quote(caller)}&to={quote(called)}"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Stream bidirectional="true" keepCallAlive="false" '
        f'contentType="audio/x-mulaw;rate=8000">{ws_url}</Stream>'
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")


@runner_app.post("/hangup", include_in_schema=False)
async def vobiz_hangup(request: Request) -> Response:
    """Vobiz's hangup notification. keepCallAlive="false" already ends the
    call when we close the WebSocket, so this just logs for visibility."""
    form = await request.form()
    logger.info(
        "Vobiz call ended: call_uuid={} duration={} cause={}",
        form.get("CallUUID"),
        form.get("Duration"),
        form.get("HangupCause"),
    )
    return Response(content="OK", media_type="text/plain")


# Once per process, not per call — a second setup_tracing() call can't
# override the global TracerProvider the first one already installed.
_TRACING_ENABLED = setup_call_tracing()


@runner_app.on_event("startup")
async def _configure_log_verbosity() -> None:
    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"))


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
    logger.info("Starting bot for {}", ACTIVE_RESTAURANT.name)

    call_session_id = getattr(runner_args, "session_id", None) or ""
    call_data = getattr(runner_args, "call_data", None)
    caller_phone = getattr(call_data, "from_number", None) or ""
    if not caller_phone and getattr(runner_args, "transport_type", None) == "twilio" and call_data:
        caller_phone = await lookup_caller_number(getattr(call_data, "call_id", "") or "")

    def _build_stt() -> SarvamSTTService:
        return SarvamSTTService(
            api_key=settings.sarvam_api_key,
            mode="codemix",
            settings=SarvamSTTService.Settings(
                model="saaras:v3",
                language=Language.HI_IN,
                start_speech_volume_threshold=-40.0,
            ),
        )

    def _build_llm() -> OpenAILLMService:
        return OpenAILLMService(
            api_key=settings.openai_api_key,
            settings=OpenAILLMService.Settings(
                model=settings.openai_model,
                system_instruction=build_system_prompt(ACTIVE_RESTAURANT),
            ),
        )

    def _build_tts() -> RumikTTSService:
        # mulberry: the only Rumik model with male voice presets, and it
        # natively handles Hindi/English/code-mixed text in one request —
        # see app/services/rumik_tts.py. "table"-style loanword pronunciation
        # was tested across several spellings (2026-09-18) and plain English
        # spelling read fine; no special-casing needed for now.
        return RumikTTSService(
            api_key=settings.rumik_api_key,
            speaker="adam",
            description=(
                "a male, 30s, indian accent voice, normal pitch, smooth timbre, "
                "conversational pacing, professional register, like a restaurant host"
            ),
        )

    context = LLMContext(tools=[log_interaction, check_availability, book_table, end_call])

    def _build_user_aggregators():
        return LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
        )

    stt, llm, tts, (user_aggregator, assistant_aggregator) = await asyncio.gather(
        asyncio.to_thread(_build_stt),
        asyncio.to_thread(_build_llm),
        asyncio.to_thread(_build_tts),
        asyncio.to_thread(_build_user_aggregators),
    )

    worker_handle = WorkerHandle()
    logging_enforcer = LogInteractionEnforcer(context, worker_handle)
    turn_taking_guard = OneUtterancePerTurnGuard(context)
    second_paragraph_filter = SecondParagraphFilter()

    audiobuffer = AudioBufferProcessor(auto_start_recording=True)
    call_state = {"transcript": ""}

    def capture_transcript() -> None:
        call_state["transcript"] = build_transcript(context, ACTIVE_RESTAURANT.bot_name)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            logging_enforcer,
            turn_taking_guard,
            second_paragraph_filter,
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
        enable_tracing=_TRACING_ENABLED,
        conversation_id=call_session_id or None,
        app_resources={
            "call_session_id": call_session_id,
            "caller_phone": caller_phone,
            "worker_handle": worker_handle,
            "capture_transcript": capture_transcript,
            "turn_taking_guard": turn_taking_guard,
        },
    )
    worker_handle.worker = worker

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Caller connected")
        await worker.queue_frames([TTSSpeakFrame(ACTIVE_RESTAURANT.first_message)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Caller disconnected")
        capture_transcript()
        await runner.cancel()

    await runner.run()


def _create_vobiz_transport(runner_args: WebSocketRunnerArguments, call_data) -> BaseTransport:
    """Build the transport for a Vobiz call by hand.

    pipecat's create_transport() auto-detects Vobiz's stream as "plivo" (the
    wire protocols match) and would build a PlivoFrameSerializer with
    auto_hang_up=True, which raises unless real PLIVO_AUTH_ID/PLIVO_AUTH_TOKEN
    creds are set — this app has no Plivo account, so that always fails. We
    don't need Plivo's REST hangup call anyway: our /answer XML sets
    keepCallAlive="false", so Vobiz ends the call as soon as we close this
    WebSocket (EndFrame does that already, same as the Exotel/Twilio paths).
    """
    query = runner_args.websocket.query_params
    call_data.from_number = query.get("from") or call_data.from_number
    call_data.to_number = query.get("to") or call_data.to_number
    runner_args.call_data = call_data

    params = FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True)
    params.add_wav_header = False
    params.serializer = PlivoFrameSerializer(
        stream_id=call_data.stream_id,
        call_id=call_data.call_id,
        params=PlivoFrameSerializer.InputParams(auto_hang_up=False),
    )
    return FastAPIWebsocketTransport(websocket=runner_args.websocket, params=params)


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the Pipecat dev runner looks for."""
    if isinstance(runner_args, WebSocketRunnerArguments) and runner_args.transport_type != "websocket":
        # Peek the handshake ourselves so Vobiz calls (which auto-detect as
        # "plivo") can be routed to _create_vobiz_transport before
        # create_transport() gets a chance to build a real PlivoFrameSerializer.
        detected_type, call_data = await parse_telephony_websocket(runner_args.websocket)
        if detected_type == "plivo":
            runner_args.transport_type = detected_type
            transport = _create_vobiz_transport(runner_args, call_data)
            await run_bot(transport, runner_args)
            return

    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    run_dev_server()
