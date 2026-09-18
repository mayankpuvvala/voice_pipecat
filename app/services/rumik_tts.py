"""Rumik Silk (`mulberry` model) text-to-speech, as a pipecat TTSService.

pipecat has no first-party Rumik integration, so this wraps Rumik's one-shot
HTTP endpoint (POST /v1/tts) the same way pipecat's own OpenAITTSService and
SarvamHttpTTSService wrap their providers' HTTP endpoints: one request per
utterance, response decoded to raw PCM for a single TTSAudioRawFrame.

`mulberry` is the only Rumik model with documented male voice presets (the
newer mulberry-1.6 only ships female presets) and is the model that natively
handles Hindi (Devanagari), English, and Hindi/English code-mixed text in one
request — see https://docs.rumik.ai/mulberry.md.

Resilience: Rumik is a single vendor with no SLA we control, so a request
gets one quick retry before this falls back to OpenAI's TTS API for that same
utterance (same 24kHz PCM output, so no resampling needed) — see
`_request_rumik`/`_request_openai_fallback` below. Only if *both* fail (four
attempts total across two vendors, for one utterance) does this give up and
call `on_all_providers_failed`, wired in app/main.py to
CallHealthMonitor.degrade_silently — there's no third TTS vendor, so at that
point the bot genuinely cannot say anything further and the call just ends.
"""

from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx
from loguru import logger
from openai import AsyncOpenAI

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.utils.tracing.service_decorators import traced_tts

# Documented default for Rumik's WAV response — verified against a real
# response: mono, 16-bit PCM, 24000 Hz, standard 44-byte header. OpenAI's TTS
# API also outputs 24kHz PCM, so the fallback path needs no resampling either.
RUMIK_SAMPLE_RATE = 24000

_MAX_ATTEMPTS_PER_PROVIDER = 2
_RETRY_DELAY_SECS = 0.3


class RumikTTSService(TTSService):
    """Silk `mulberry` model TTS. Voice is a named speaker preset (male:
    lucas, noah, theo, adam) plus a required natural-language `description`
    steering delivery style — Rumik has no separate voice-id-only mode for
    this model.
    """

    def __init__(
        self,
        *,
        api_key: str,
        speaker: str,
        description: str,
        base_url: str = "https://silk-api.rumik.ai/v1/tts",
        openai_api_key: str = "",
        openai_fallback_voice: str = "echo",
        on_all_providers_failed: Callable[[str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        """Args (beyond the Rumik-specific ones above):
            openai_api_key: If set, used as the fallback TTS provider once
                Rumik exhausts its own retries. Left empty (the default),
                there's no fallback and a Rumik failure goes straight to
                on_all_providers_failed.
            openai_fallback_voice: One of OpenAI's TTS voices (e.g. "echo",
                "alloy") — pass settings.openai_tts_voice, which otherwise sits
                unused now that Rumik is the primary TTS voice.
            on_all_providers_failed: Awaited with a short reason string once
                Rumik *and* the OpenAI fallback have both failed for one
                utterance — the bot has nothing left to speak with. Wired to
                CallHealthMonitor.degrade_silently in app/main.py.
        """
        # TTSService requires model/voice/language to be explicitly set (even
        # to None) rather than left NOT_GIVEN, matching OpenAITTSService's/
        # SarvamHttpTTSService's own pattern.
        default_settings = TTSSettings(model="mulberry", voice=speaker, language=None)

        super().__init__(
            sample_rate=RUMIK_SAMPLE_RATE,
            push_start_frame=True,
            push_stop_frames=True,
            settings=default_settings,
            **kwargs,
        )
        self._api_key = api_key
        self._speaker = speaker
        self._description = description
        self._base_url = base_url
        self._openai_client = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None
        self._openai_fallback_voice = openai_fallback_voice
        self._on_all_providers_failed = on_all_providers_failed

    def can_generate_metrics(self) -> bool:
        return True

    async def _request_rumik(self, text: str) -> bytes | None:
        """Up to `_MAX_ATTEMPTS_PER_PROVIDER` tries against Rumik. Returns raw
        PCM on success, None once every attempt has failed."""
        for attempt in range(1, _MAX_ATTEMPTS_PER_PROVIDER + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        self._base_url,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json={
                            "model": "mulberry",
                            "text": text,
                            "speaker": self._speaker,
                            "description": self._description,
                        },
                    )

                if response.status_code == 200:
                    await self.start_tts_usage_metrics(text)
                    with wave.open(io.BytesIO(response.content), "rb") as wav:
                        if wav.getframerate() != self.sample_rate:
                            logger.warning(
                                "Rumik returned {}Hz audio, pipeline expects {}Hz",
                                wav.getframerate(),
                                self.sample_rate,
                            )
                        return wav.readframes(wav.getnframes())

                logger.error(
                    "Rumik TTS error (status {}, attempt {}/{}): {}",
                    response.status_code,
                    attempt,
                    _MAX_ATTEMPTS_PER_PROVIDER,
                    response.text[:300],
                )
            except Exception as e:
                logger.error(
                    "Rumik TTS request failed (attempt {}/{}): {}",
                    attempt,
                    _MAX_ATTEMPTS_PER_PROVIDER,
                    e,
                )
            if attempt < _MAX_ATTEMPTS_PER_PROVIDER:
                await asyncio.sleep(_RETRY_DELAY_SECS)
        return None

    async def _request_openai_fallback(self, text: str) -> bytes | None:
        """Same contract as `_request_rumik`, against OpenAI's TTS API. Returns
        None immediately (no attempt made) if no OpenAI key was configured."""
        if self._openai_client is None:
            return None

        for attempt in range(1, _MAX_ATTEMPTS_PER_PROVIDER + 1):
            try:
                async with self._openai_client.audio.speech.with_streaming_response.create(
                    model="tts-1",
                    voice=self._openai_fallback_voice,
                    input=text,
                    response_format="pcm",
                ) as response:
                    if response.status_code == 200:
                        await self.start_tts_usage_metrics(text)
                        return b"".join([chunk async for chunk in response.iter_bytes()])

                    body = await response.text()
                    logger.error(
                        "OpenAI TTS fallback error (status {}, attempt {}/{}): {}",
                        response.status_code,
                        attempt,
                        _MAX_ATTEMPTS_PER_PROVIDER,
                        body[:300],
                    )
            except Exception as e:
                logger.error(
                    "OpenAI TTS fallback request failed (attempt {}/{}): {}",
                    attempt,
                    _MAX_ATTEMPTS_PER_PROVIDER,
                    e,
                )
            if attempt < _MAX_ATTEMPTS_PER_PROVIDER:
                await asyncio.sleep(_RETRY_DELAY_SECS)
        return None

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        pcm = await self._request_rumik(text)

        if pcm is None:
            if self._openai_client is not None:
                logger.warning(
                    "TTS: Rumik exhausted {} attempt(s) — falling back to OpenAI TTS "
                    "for this utterance",
                    _MAX_ATTEMPTS_PER_PROVIDER,
                )
            pcm = await self._request_openai_fallback(text)
            if pcm is not None:
                logger.info("TTS: served via OpenAI fallback (Rumik unavailable)")

        if pcm is None:
            logger.critical(
                "CALL_HEALTH stage=tts — Rumik and the OpenAI TTS fallback both failed "
                "for this utterance; nothing left to speak with"
            )
            if self._on_all_providers_failed is not None:
                await self._on_all_providers_failed(
                    "tts: Rumik and the OpenAI fallback both failed"
                )
            yield ErrorFrame(error="All TTS providers failed (Rumik + OpenAI fallback)")
            return

        await self.stop_ttfb_metrics()
        yield TTSAudioRawFrame(pcm, self.sample_rate, 1, context_id=context_id)
