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
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.evals.transport import EvalTransportParams
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from app.admin.routes import register_admin_routes
from app.config.restaurants import ACTIVE_RESTAURANT
from app.config.settings import settings
from app.live_client import register_live_test_client
from app.pipeline.call_health import CallHealthMonitor
from app.pipeline.dynamic_prompt import DynamicPromptInjector
from app.pipeline.logging_enforcer import LogInteractionEnforcer, WorkerHandle
from app.pipeline.prompts import build_system_prompt
from app.pipeline.recording import save_call_recording
from app.pipeline.second_paragraph_filter import SecondParagraphFilter
from app.pipeline.tracing import setup_call_tracing
from app.pipeline.transcript import build_transcript
from app.pipeline.tts_language_switcher import TTSLanguageSwitcher
from app.pipeline.turn_taking_guard import OneUtterancePerTurnGuard
from app.services.resilient_stt import ResilientSarvamSTTService
from app.services.twilio_client import lookup_caller_number
from app.tools.end_call import end_call
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
        "note": "See /admin for call logs, /live for a browser test call.",
        # Railway injects this at runtime; lets us confirm which commit is
        # actually live without dashboard access (see RAILWAY_GIT_COMMIT_SHA
        # in Railway's docs).
        "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"),
    }


register_admin_routes(runner_app)
register_live_test_client(runner_app)


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
    # Browser test client at /live (app/live_client.py) -- talks straight to
    # pipecat's own POST /api/offer, which create_transport() routes here via
    # SmallWebRTCRunnerArguments. Requires the `webrtc` extra (aiortc) to be
    # installed, see requirements.txt.
    "webrtc": lambda: TransportParams(
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

    worker_handle = WorkerHandle()
    # logInteraction is deliberately NOT a live tool: the model isn't asked to
    # call it (see _LOGGING_TIMING_INSTRUCTION in prompts.py) — every reply
    # calling it inline turned out to force a silent tool-call-only round
    # before the actual spoken reply, doubling LLM round-trip latency on
    # nearly every turn (confirmed via Langfuse trace 2026-09-19, still
    # happening on the current model, not just older ones). LogInteractionEnforcer's
    # background backfill logs every reply instead, off the live call's critical path.
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

    def _build_stt() -> ResilientSarvamSTTService:
        return ResilientSarvamSTTService(
            api_key=settings.sarvam_api_key,
            mode="codemix",
            settings=SarvamSTTService.Settings(
                model="saaras:v3",
                language=Language.HI_IN,
                start_speech_volume_threshold=-40.0,
            ),
            on_connect_exhausted=lambda reason: call_health.degrade_with_apology("stt", reason),
        )

    def _build_llm() -> OpenAILLMService:
        # Groq (Llama on LPU inference hardware) in place of OpenAI for the
        # conversational LLM when a key is configured — cuts the 1-3s
        # gpt-4o-mini completion time that's paid on every turn. GroqLLMService
        # is a thin OpenAILLMService subclass (different base_url/model, same
        # settings shape, same tool-calling), so retry_on_timeout, the
        # isinstance(..., OpenAILLMService) check in on_pipeline_error below,
        # and everything else here needs no further changes either way.
        # openai_api_key/openai_model stay OpenAI-only regardless — see their
        # comment in app/config/settings.py.
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
            # One extra retry on top of the OpenAI SDK's own default retries
            # for transient/5xx errors — see BaseOpenAILLMService.get_chat_
            # completions. A failure surviving both becomes a non-fatal
            # ErrorFrame; on_pipeline_error below is what reacts to it.
            retry_on_timeout=True,
            settings=OpenAILLMService.Settings(
                model=settings.openai_model,
                system_instruction=build_system_prompt(ACTIVE_RESTAURANT),
                # gpt-5.6-luna 400s on /v1/chat/completions with function
                # tools attached unless reasoning_effort is explicit --
                # confirmed live 2026-09-19, see TROUBLESHOOTING.md's
                # "Conversational LLM model choice" section. "none" matches
                # this bot's actual usage (no multi-step reasoning needed
                # for check_availability/book_table/log_interaction calls).
                # Harmless passthrough if OPENAI_MODEL is ever overridden
                # back to a non-reasoning model like gpt-5.4-nano -- but if
                # switching to a different reasoning-capable model, re-check
                # this still applies.
                extra={"reasoning_effort": "none"},
            ),
        )

    def _build_tts() -> SarvamTTSService:
        # Trying bulbul:v3 in place of Rumik (2026-09-18): it streams audio
        # over its own WebSocket as each chunk is synthesized (see
        # SarvamTTSService._receive_messages), instead of Rumik's one-shot
        # HTTP call that waits for the whole utterance before anything plays
        # — see app/services/rumik_tts.py, still here if this doesn't pan out.
        # "shubh" is bulbul:v3's default speaker; listen via /live and swap
        # for another name in SarvamTTSSpeakerV3 if it doesn't fit the persona.
        #
        # No retry/OpenAI-fallback wrapper here yet (unlike Rumik's) — a
        # failure still ends the call cleanly via on_pipeline_error below,
        # just without a second TTS vendor to fall back to first.
        return SarvamTTSService(
            api_key=settings.sarvam_api_key,
            settings=SarvamTTSService.Settings(
                model="bulbul:v3",
                # Starting locale only -- app/pipeline/tts_language_switcher.py
                # flips this per reply to match whatever language the model
                # actually replied in. Was hardcoded to Language.HI_IN for the
                # whole call: confirmed live that pinned every reply's
                # pronunciation to Hindi phonetics even for plain English
                # replies (guest counts/numbers came out Hindi-accented) no
                # matter what language the caller actually used.
                language=Language.EN_IN,
                # Default is 50 -- for a reply like "Sure! What name should I
                # put the reservation under?" (52 chars) that's the entire
                # sentence buffered before any audio starts, which throws
                # away most of the streaming benefit over Rumik. Lower value
                # means Sarvam starts synthesizing after fewer words, at some
                # risk to prosody smoothness -- listen via /live and raise
                # this back up if sentences sound choppy.
                #
                # 30 is Sarvam's actual server-side floor, not just a
                # sensible-sounding number: anything below it makes the
                # server reject the *entire* config message with a generic
                # 422 "Input parameters has to be a valid dictionary" (no
                # mention of which field), which kills the TTS connection
                # on every reconnect attempt and silently ends the call.
                # Confirmed 2026-09-18 by sending the raw config directly
                # to wss://api.sarvam.ai/text-to-speech/ws — 15 reproduces
                # the error every time, 30 does not.
                min_buffer_size=30,
            ),
        )

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
        # ErrorFrames travel upstream (push_error) and land here once they
        # reach the pipeline's start. STT connect failures are handled by
        # ResilientSarvamSTTService's own callback instead — Sarvam's
        # per-chunk send/receive errors also surface as plain ErrorFrames and
        # are common transient noise, not a reliable "STT is dead" signal.
        if isinstance(frame.processor, OpenAILLMService):
            # An ErrorFrame here already means both the OpenAI SDK's own
            # retries and retry_on_timeout above were exhausted for that turn.
            await call_health.note_llm_failure(frame.error)
        elif isinstance(frame.processor, SarvamTTSService):
            # No retry/fallback wrapper for TTS yet (unlike Rumik's) — any
            # error here ends the call immediately, no threshold. If this
            # proves trigger-happy on transient Sarvam TTS blips, add a
            # counter here the way note_llm_failure does for the LLM.
            await call_health.degrade_silently("tts", frame.error)

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
