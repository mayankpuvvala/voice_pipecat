"""Entrypoint. Run with `python -m app.main -t <provider>` where <provider>
is one of exotel/twilio/telnyx/plivo/vobiz — see README.md for the
TELEPHONY_TRANSPORT env var and the Jio → Exotel → this server call path.
Vobiz auto-detects as "plivo" and is routed via `_create_vobiz_transport()`.
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
from aiortc import RTCIceServer
from pipecat.runner.types import RunnerArguments, WebSocketRunnerArguments
from pipecat.runner.utils import create_transport, parse_telephony_websocket
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.evals.transport import EvalTransportParams
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from app.admin.routes import register_admin_routes
from app.config.restaurants import ACTIVE_RESTAURANT
from app.config.settings import settings
from app.live_client import register_live_test_client
from app.pipeline import active_calls
from app.pipeline.call_health import CallHealthMonitor
from app.pipeline.dynamic_prompt import DynamicPromptInjector
from app.pipeline.idle_post_processor import idle_post_processing_loop
from app.pipeline.logging_enforcer import LogInteractionEnforcer, WorkerHandle
from app.pipeline.prompts import build_system_prompt
from app.pipeline.recording import save_call_recording
from app.pipeline.second_paragraph_filter import SecondParagraphFilter
from app.pipeline.tracing import setup_call_tracing
from app.pipeline.transcript import build_transcript
from app.pipeline.tts_language_switcher import TTSLanguageSwitcher
from app.pipeline.turn_taking_guard import OneUtterancePerTurnGuard
from app.services.stt_factory import build_stt
from app.services.tts_factory import build_tts
from app.services.twilio_client import fetch_ice_servers_sync, lookup_caller_number
from app.tools.end_call import end_call
from app.tools.reservations import book_table, check_availability

# pipecat.runner.run exports `app` as an extension point for adding
# routes before main() — see that module's docstring.
import pipecat.runner.run as pipecat_runner
from pipecat.runner.run import app as runner_app
from pipecat.runner.run import main as run_dev_server

pipecat_runner._setup_frontend_routes = lambda app: None

# /live (browser WebRTC test client) ICE servers: pipecat's dev runner builds
# its SmallWebRTCRequestHandler with ice_servers=None (no CLI/env hook to
# change this), so the bot's server-side aiortc peer only ever gathers host
# candidates -- its own private container IP. On Railway that's unreachable
# from any browser, on any network; that's the actual cause of /live's
# "Connection state: failed", not a client-side NAT issue. Patched here since
# there's no other extension point, same spirit as _setup_frontend_routes
# above. Twilio TURN credentials (already using this app's own account) are
# fetched once, synchronously, before any event loop exists yet -- see
# fetch_ice_servers_sync's docstring for the ~24h token lifetime tradeoff.
_WEBRTC_ICE_SERVERS = [
    RTCIceServer(urls=s["urls"], username=s.get("username"), credential=s.get("credential"))
    for s in fetch_ice_servers_sync()
]
_original_webrtc_handler_init = SmallWebRTCRequestHandler.__init__


def _webrtc_handler_init_with_ice_servers(self, *args, ice_servers=None, **kwargs) -> None:
    _original_webrtc_handler_init(self, *args, ice_servers=ice_servers or _WEBRTC_ICE_SERVERS, **kwargs)


SmallWebRTCRequestHandler.__init__ = _webrtc_handler_init_with_ice_servers


def _detect_reasoning_effort_required() -> bool:
    """One-time startup probe against this process's OWN configured
    OPENAI_API_KEY/OPENAI_MODEL: does it need reasoning_effort='none' to
    accept function tools, or does it reject reasoning_effort outright as an
    unrecognized argument?

    Confirmed live BOTH ways at different points (see TROUBLESHOOTING.md and
    SESSION_ISSUES.txt) -- most likely explanation is this deployment's real
    OPENAI_API_KEY/account resolves this model differently than whatever key
    was used for earlier local testing, not that the API is flaky turn to
    turn. Rather than hardcode one behavior and find out live on a real call
    (confirmed: two separate production calls both hung up on this), ask the
    actual configured key once at boot and cache the answer for the life of
    this process. Defaults to True (the historically documented behavior) if
    the probe itself is inconclusive (network blip, some other error) --
    _build_llm's own retry_on_timeout and call_health's failure threshold
    still backstop a wrong guess either way.
    """
    if not settings.openai_api_key:
        return True

    from openai import OpenAI as _SyncOpenAI

    probe_tools = [
        {
            "type": "function",
            "function": {
                "name": "_startup_probe",
                "description": "probe",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    client = _SyncOpenAI(api_key=settings.openai_api_key)
    try:
        client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": "hi"}],
            tools=probe_tools,
            reasoning_effort="none",
        )
        logger.info(
            "Startup probe: {} accepts reasoning_effort='none' -- using it for this process",
            settings.openai_model,
        )
        return True
    except Exception as e:
        if "Unrecognized request argument supplied: reasoning_effort" in str(e):
            logger.warning(
                "Startup probe: {} rejects reasoning_effort on this account -- "
                "omitting it for this process (see TROUBLESHOOTING.md)",
                settings.openai_model,
            )
            return False
        logger.warning(
            "Startup reasoning_effort probe inconclusive ({}) -- defaulting to "
            "required (current documented behavior)",
            e,
        )
        return True


_REASONING_EFFORT_REQUIRED = _detect_reasoning_effort_required()


@runner_app.get("/", include_in_schema=False)
async def root_status() -> dict:
    return {
        "status": "ok",
        "service": f"{ACTIVE_RESTAURANT.name} voice agent",
        "note": "See /admin for call logs, /live for a browser test call.",
        # Railway injects this at runtime so we can confirm the live commit
        # without dashboard access.
        "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"),
    }


register_admin_routes(runner_app)
register_live_test_client(runner_app)


@runner_app.post("/answer", include_in_schema=False)
async def vobiz_answer(request: Request) -> Response:
    """Vobiz's primary answer URL: return XML pointing it at our WS stream.

    Vobiz's <Stream> protocol is wire-compatible with Plivo's, but its
    handshake doesn't carry the caller's number, so it's threaded through
    as a query param instead. See `bot()` below for how the stream is picked up.
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


@runner_app.on_event("startup")
async def _start_idle_post_processing() -> None:
    # Fire-and-forget for the life of the process — never awaited, so it
    # can't delay startup or block a real call. Gated by active_calls.is_idle
    # inside the loop itself, so this is safe to always start.
    if settings.idle_post_processing_enabled:
        asyncio.create_task(idle_post_processing_loop())


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
    # Browser test client at /live (app/live_client.py). Requires the
    # `webrtc` extra (aiortc) — see requirements.txt.
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Tracks this call in active_calls (see that module's docstring) for
    the idle post-processing gate, then hands off to the real bot logic.
    try/finally around the whole call, not just the happy path, so a setup
    failure can never leak a permanently "active" call and wedge the idle
    gate shut."""
    active_calls.call_started()
    try:
        await _run_bot_impl(transport, runner_args)
    finally:
        active_calls.call_ended()


async def _run_bot_impl(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    logger.info("Starting bot for {}", ACTIVE_RESTAURANT.name)

    call_session_id = getattr(runner_args, "session_id", None) or ""
    call_data = getattr(runner_args, "call_data", None)
    caller_phone = getattr(call_data, "from_number", None) or ""
    if not caller_phone and getattr(runner_args, "transport_type", None) == "twilio" and call_data:
        caller_phone = await lookup_caller_number(getattr(call_data, "call_id", "") or "")

    worker_handle = WorkerHandle()
    # logInteraction is deliberately NOT a live tool — calling it inline
    # doubled LLM latency per turn. LogInteractionEnforcer backfills it
    # in the background instead, off the call's critical path.
    context = LLMContext(tools=[check_availability, book_table, end_call])
    call_state = {"transcript": ""}

    def capture_transcript() -> None:
        call_state["transcript"] = build_transcript(context, ACTIVE_RESTAURANT.bot_name)

    # One per call — see its own module docstring for how this fits into
    # the STT/LLM/TTS retry/fallback layering.
    call_health = CallHealthMonitor(
        worker_handle=worker_handle,
        capture_transcript=capture_transcript,
        call_session_id=call_session_id,
        caller_phone=caller_phone,
        restaurant_name=ACTIVE_RESTAURANT.name,
    )

    def _build_stt() -> STTService:
        # Vendor picked per-restaurant via ACTIVE_RESTAURANT.stt_provider —
        # see app/services/stt_factory.py, the one place that switches it.
        return build_stt(
            ACTIVE_RESTAURANT,
            settings,
            on_connect_exhausted=lambda reason: call_health.degrade_with_apology("stt", reason),
        )

    def _build_llm() -> OpenAILLMService:
        # Groq (Llama on LPU hardware) replaces OpenAI when configured, to
        # cut the 1-3s gpt-5-mini completion time paid per turn. It's a thin
        # OpenAILLMService subclass, so nothing else here needs to change.
        if settings.groq_api_key and settings.groq_enabled:
            return GroqLLMService(
                api_key=settings.groq_api_key,
                retry_on_timeout=True,
                settings=GroqLLMService.Settings(
                    model=settings.groq_model,
                    system_instruction=build_system_prompt(ACTIVE_RESTAURANT),
                ),
            )
        return OpenAILLMService(
            api_key=settings.openai_api_key,
            # One extra retry on top of the OpenAI SDK's own retries; a
            # failure surviving both becomes an ErrorFrame (see on_pipeline_error).
            retry_on_timeout=True,
            settings=OpenAILLMService.Settings(
                model=settings.openai_model,
                system_instruction=build_system_prompt(ACTIVE_RESTAURANT),
                # See _detect_reasoning_effort_required's docstring above --
                # this account/model combo has been confirmed to need this
                # explicit at some points and reject it outright at others,
                # so it's decided by a real probe against the actual
                # configured key at process startup, not hardcoded.
                extra=({"reasoning_effort": "none"} if _REASONING_EFFORT_REQUIRED else {}),
            ),
        )

    def _build_tts() -> TTSService:
        # Vendor picked per-restaurant via ACTIVE_RESTAURANT.tts_provider —
        # see app/services/tts_factory.py, the one place that switches it.
        return build_tts(ACTIVE_RESTAURANT, settings)

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

    logging_enforcer = LogInteractionEnforcer(
        context,
        worker_handle,
        call_session_id=call_session_id,
        caller_phone=caller_phone,
    )
    turn_taking_guard = OneUtterancePerTurnGuard(context)
    second_paragraph_filter = SecondParagraphFilter()
    tts_language_switcher = TTSLanguageSwitcher(tts)
    dynamic_prompt_injector = DynamicPromptInjector(
        context=context, restaurant=ACTIVE_RESTAURANT, llm=llm
    )

    audiobuffer = AudioBufferProcessor(auto_start_recording=True)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            dynamic_prompt_injector,
            llm,
            logging_enforcer,
            turn_taking_guard,
            second_paragraph_filter,
            tts_language_switcher,
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

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(worker, frame):
        # STT connect failures are handled by the resilient STT wrapper's
        # own callback instead — per-chunk STT errors here are common noise.
        if isinstance(frame.processor, OpenAILLMService):
            # Means both the SDK's own retries and retry_on_timeout were exhausted.
            await call_health.note_llm_failure(frame.error)
        elif frame.processor is tts:
            # No retry/fallback wrapper for TTS yet — any error ends the call.
            # Checked by instance (not isinstance of one provider) since tts
            # is whichever service ACTIVE_RESTAURANT.tts_provider picked.
            await call_health.degrade_silently("tts", frame.error)

    @worker.event_handler("on_idle_timeout")
    async def on_idle_timeout(worker):
        # pipecat cancels the worker itself right after this handler runs
        # (cancel_on_idle_timeout defaults to True) — none of the app's own
        # call-ending paths (on_client_disconnected, end_call, CallHealth
        # degrade) run for this one, so without this handler
        # save_call_recording() logs the call with an empty Transcript
        # despite a real recording/duration.
        logger.info("Pipeline idle timeout — capturing transcript before worker cancels")
        capture_transcript()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    greeted = False

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        nonlocal greeted
        logger.info("Caller connected")
        if greeted:
            # SmallWebRTC's underlying ICE connection can pass through
            # "connected" more than once during a restart (e.g. after a TURN
            # relay rejection — aioice.stun.TransactionFailed: "Forbidden
            # IP" — forces a renegotiation), re-firing this event for the
            # same call. Telephony transports (Exotel/Twilio) have no ICE
            # concept and can't hit this. Without the guard, the bot's
            # opening line gets queued and spoken twice.
            logger.debug("on_client_connected fired again for this call — not re-greeting")
            return
        greeted = True
        await worker.queue_frames([TTSSpeakFrame(ACTIVE_RESTAURANT.first_message)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Caller disconnected")
        capture_transcript()
        await runner.cancel()

    await runner.run()


def _create_vobiz_transport(runner_args: WebSocketRunnerArguments, call_data) -> BaseTransport:
    """Build the transport for a Vobiz call by hand.

    create_transport() would build a PlivoFrameSerializer with
    auto_hang_up=True, which requires Plivo creds this app doesn't have.
    Not needed anyway — /answer's keepCallAlive="false" already ends the
    call when we close the WebSocket.
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
