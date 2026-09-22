"""Rumik Silk (`mulberry` model) text-to-speech, as a pipecat TTSService.

pipecat has no first-party Rumik integration in this project's dependency
set, so this wraps Rumik's per-call streaming WebSocket protocol directly.
Design verified against Rumik's own maintained pipecat package source
(github.com/ira-rumik/pipecat-rumik, MIT, `src/pipecat_rumik/services/rumik/
tts.py`) rather than guessed from the HTTP docs alone — see history below.

Protocol: POST /v1/tts/ws-connect mints a one-shot `{ws_url, token}` pair,
then the WebSocket delivers raw PCM chunks as Rumik generates them, ending
each utterance with a `{"type": "done"}` control frame (or `"cancelled"` on
barge-in, `"error"`/`"timeout"` on failure) — see
https://docs.rumik.ai/streaming.md. Critically, **the socket stays open
across utterances**: mint once at call start, send a new synthesis message
on the same warm socket for every subsequent turn. Minting a fresh session
per utterance (this file's first streaming attempt, 2026-09-22) measured
~0.8-1.8s time-to-first-audio because it pays a session-mint HTTP round trip
plus a WebSocket handshake before every single utterance; reusing the socket
(this version) only pays that once per call.

`mulberry` is the only Rumik model with male voice presets and native
Hindi/English code-mixed support — see https://docs.rumik.ai/mulberry.md.
mulberry-1.6 was checked too (https://docs.rumik.ai/mulberry-1-6.md): female
voices only (ira/aisha/siya/zoya, no male preset) and no Hindi/English
code-mixed support (22 separate-script languages instead) — not usable here.

History: original integration (2026-09-18) used Rumik's one-shot `POST
/v1/tts` — fully buffered the whole utterance before yielding any audio,
confirmed a real problem for longer replies (TROUBLESHOOTING.md, SESSION_
ISSUES.txt item 1). TTS moved to Sarvam (streaming WS, persistent
connection) the same day over this. Revisited 2026-09-22 since Rumik is
~6x cheaper per character than Sarvam (₹0.50 vs ₹3.00 per 1k chars) — first
pass added streaming but minted a session per utterance (still slow, see
above); this version reuses one warm connection for the whole call, matching
Sarvam's own architecture in this codebase and Rumik's own recommended
pattern.

Resilience note — read before wiring this into production: unlike the old
one-shot version, this does NOT fall back to OpenAI TTS mid-call. Rumik's
persistent-connection protocol streams audio via a background receive loop
decoupled from `run_tts`'s own return path, so there's no single point to
catch "no audio arrived for this utterance" and re-request from OpenAI
without risking audio duplication or silently swallowing a real utterance.
This matches Sarvam's own current posture in this codebase exactly (same
"no retry/fallback wrapper, a TTS failure just ends the call" tradeoff,
same error-reporting path: `push_error` -> upstream ErrorFrame -> app/
main.py's `on_pipeline_error` -> CallHealthMonitor.degrade_silently).
Reconnection on a dropped connection IS automatic (inherited from
pipecat's `InterruptibleTTSService`/`WebsocketService`, exponential
backoff) — `push_error` only fires once the initial connect (or a later
reconnect) is exhausted.

Measured 2026-09-22 (real Rumik API, 10 distinct realistic bot phrases
through a real pipecat pipeline, one call): turn 1 (pays the mint+handshake)
609ms TTFA; turns 2-10 on the reused socket: min=296ms, median=313ms,
max=422ms — close to Sarvam's own measured ~260ms median. Not yet tested
against a real phone call end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from loguru import logger
from websockets.protocol import State

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.settings import NOT_GIVEN, TTSSettings, _NotGiven
from pipecat.services.tts_service import InterruptibleTTSService
from pipecat.utils.tracing.service_decorators import traced_tts

# Rumik's streaming endpoint returns raw PCM (no audio_format requested):
# int16 little-endian, mono, 24000 Hz — see docs.rumik.ai/streaming.md.
RUMIK_SAMPLE_RATE = 24000
RUMIK_MODEL = "mulberry"

# How many times _connect_websocket may fail outright (mint request or
# handshake) before giving up and reporting the error upstream via
# push_error. Separate from pipecat's own WebsocketService reconnect-with-
# backoff, which handles a connection that drops after having worked.
_MAX_CONNECT_ATTEMPTS = 2
_CONNECT_RETRY_DELAY_SECS = 0.3


@dataclass
class RumikTTSSettings(TTSSettings):
    """Runtime-updatable Rumik `mulberry` settings, beyond the base model/voice/language.

    Parameters:
        description: Required by Rumik whenever `voice` (sent as `speaker`) is
            a preset — natural-language delivery-style steering.
    """

    description: str | None | _NotGiven = field(default_factory=lambda: NOT_GIVEN)


def _settings_value(value):
    return None if value is NOT_GIVEN else value


def _build_synthesis_payload(
    settings: RumikTTSSettings, text: str, *, include_model: bool
) -> dict[str, Any]:
    """Rumik's synthesis message shape. `model` is only needed on the very
    first message that mints the session — every later message on the same
    warm socket omits it (matches Rumik's own pipecat package's convention)."""
    payload: dict[str, Any] = {"text": text}
    if include_model:
        payload["model"] = _settings_value(settings.model) or RUMIK_MODEL
    voice = _settings_value(settings.voice)
    if voice is not None:
        payload["speaker"] = voice
    description = _settings_value(settings.description)
    if description is not None:
        payload["description"] = description
    return payload


class RumikTTSService(InterruptibleTTSService):
    """Silk `mulberry` model TTS over Rumik's persistent per-call WebSocket.

    One session is minted and kept open for the lifetime of the call; every
    turn's `run_tts` sends its text on that same warm socket instead of
    reconnecting. Voice is a named speaker preset (male: lucas, noah, theo,
    adam) plus a `description` steering delivery style.
    """

    Settings = RumikTTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        speaker: str,
        description: str,
        base_url: str = "https://silk-api.rumik.ai",
        **kwargs,
    ):
        """Args (beyond the Rumik-specific ones above):
            base_url: Rumik API root, no path suffix — /v1/tts/ws-connect is
                appended to mint the call's streaming session.
        """
        default_settings = RumikTTSSettings(
            model=RUMIK_MODEL, voice=speaker, language=None, description=description
        )

        super().__init__(
            push_stop_frames=False,
            push_start_frame=True,
            pause_frame_processing=True,
            sample_rate=RUMIK_SAMPLE_RATE,
            settings=default_settings,
            **kwargs,
        )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

        self._receive_task: asyncio.Task | None = None
        self._active_context_id: str | None = None
        # Rumik allows exactly one in-flight synthesis request per socket;
        # this serializes run_tts calls onto that one active request.
        self._synthesis_lock = asyncio.Lock()

    def can_generate_metrics(self) -> bool:
        return True

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._disconnect()

    async def cleanup(self):
        try:
            await self._disconnect()
        finally:
            await super().cleanup()

    async def _connect(self):
        await super()._connect()
        if self._receive_task and self._receive_task.done():
            self._receive_task = None
        await self._connect_websocket()
        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(self._receive_task_handler(self._report_error))

    async def _disconnect(self):
        await super()._disconnect()
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        await self._disconnect_websocket()
        self._clear_active_context()

    async def _mint_websocket_session(self, text: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{self._base_url}/v1/tts/ws-connect",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": RUMIK_MODEL, "text": text},
            )
        response.raise_for_status()
        data = response.json()
        for key in ("ws_url", "token"):
            if key not in data:
                raise ValueError(f"Rumik ws-connect response missing {key!r}")
        return data

    async def _connect_websocket(self):
        if self._websocket and self._websocket.state is State.OPEN:
            return

        for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
            try:
                # The mint request needs *some* text; "." is thrown away —
                # only the session credentials matter here. Real text goes
                # out per-turn over the resulting warm socket.
                session = await self._mint_websocket_session(".")
                separator = "&" if "?" in session["ws_url"] else "?"
                ws_url = f"{session['ws_url']}{separator}{urlencode({'token': session['token']})}"

                self._websocket = await self._websocket_connect(
                    ws_url, ping_interval=20, ping_timeout=30
                )
                logger.info("Connected to Rumik successfully (model={})", RUMIK_MODEL)
                await self._call_event_handler("on_connected")
                return
            except Exception as e:
                logger.error(
                    "Rumik WS connect failed (attempt {}/{}): {}",
                    attempt,
                    _MAX_CONNECT_ATTEMPTS,
                    e,
                )
                self._websocket = None
                if attempt < _MAX_CONNECT_ATTEMPTS:
                    await asyncio.sleep(_CONNECT_RETRY_DELAY_SECS)

        msg = f"Rumik WS connect exhausted {_MAX_CONNECT_ATTEMPTS} attempt(s)"
        await self._call_event_handler("on_connection_error", msg)
        await self.push_error(error_msg=msg)

    async def _disconnect_websocket(self):
        try:
            await self.stop_all_metrics()
            if self._websocket and self._websocket.state is State.OPEN:
                await self._websocket.close()
        except Exception as e:
            logger.error("Rumik WS disconnect error: {}", e)
        finally:
            self._websocket = None
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        if self._websocket:
            return self._websocket
        raise Exception("Rumik WebSocket not connected")

    async def _report_error(self, error: ErrorFrame):
        await self._finish_active_context()
        await super()._report_error(error)

    async def on_audio_context_interrupted(self, context_id: str):
        """Barge-in: cancel the in-flight generation on the warm socket
        instead of tearing down and reconnecting. Rumik audio chunks carry
        no per-message context id, so the active context is cleared
        immediately (any orphaned audio the receive loop sees next is
        dropped since there's no active context to attach it to), and the
        synthesis lock stays held until Rumik confirms with `"cancelled"`
        (or a racing `"done"`) so the next turn can't start until the old
        generation has actually stopped on the server.
        """
        if context_id == self._active_context_id or self._synthesis_lock.locked():
            self._active_context_id = None
            try:
                if self._websocket and self._websocket.state is State.OPEN:
                    await self._websocket.send(json.dumps({"type": "cancel"}))
                else:
                    self._clear_active_context()
            except Exception as e:
                logger.debug("Rumik: unable to send cancel: {}", e)
                self._clear_active_context()
        await super().on_audio_context_interrupted(context_id)

    async def _receive_messages(self):
        async for message in self._get_websocket():
            if isinstance(message, bytes):
                if not message:
                    continue
                await self.stop_ttfb_metrics()
                context_id = self._active_context_id
                if not context_id:
                    continue
                frame = TTSAudioRawFrame(
                    audio=message,
                    sample_rate=self.sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
                await self.append_to_audio_context(context_id, frame)
                continue

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.error("Rumik: invalid JSON message: {}", message)
                continue

            msg_type = data.get("type")
            if msg_type in ("done", "cancelled"):
                await self._finish_active_context()
            elif msg_type == "timeout":
                logger.debug("Rumik: idle timeout: {}", data.get("message"))
                self._disconnecting = True
                await self._disconnect_websocket()
                break
            elif msg_type == "error" or data.get("error"):
                await self._finish_active_context(error_msg=f"Rumik TTS error: {data}")
                await self._disconnect_websocket()
                break
            # "queued" and anything unrecognized: nothing to do.

    async def _finish_active_context(self, *, error_msg: str | None = None):
        context_id = self._active_context_id
        if not context_id:
            self._clear_active_context()
            return
        if error_msg:
            await self.append_to_audio_context(context_id, ErrorFrame(error=error_msg))
        await self.append_to_audio_context(context_id, TTSStoppedFrame(context_id=context_id))
        await self.remove_audio_context(context_id)
        await self.stop_all_metrics()
        self._clear_active_context()

    def _clear_active_context(self):
        self._active_context_id = None
        if self._synthesis_lock.locked():
            self._synthesis_lock.release()

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            yield TTSStoppedFrame(context_id=context_id)
            await self.remove_audio_context(context_id)
            return

        await self._synthesis_lock.acquire()
        self._active_context_id = context_id

        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()

            await self._get_websocket().send(
                json.dumps(_build_synthesis_payload(self._settings, text, include_model=False))
            )
            await self.start_tts_usage_metrics(text)
            yield None
        except Exception as e:
            logger.error("Rumik send failed: {}", e)
            yield ErrorFrame(error=f"Rumik send failed: {e}")
            yield TTSStoppedFrame(context_id=context_id)
            await self.remove_audio_context(context_id)
            self._clear_active_context()
            await self._disconnect()
