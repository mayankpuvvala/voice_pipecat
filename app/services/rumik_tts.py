"""Rumik Silk (`mulberry` model) text-to-speech, as a pipecat TTSService.

pipecat has no first-party Rumik integration, so this wraps Rumik's one-shot
HTTP endpoint (POST /v1/tts) the same way pipecat's own OpenAITTSService and
SarvamHttpTTSService wrap their providers' HTTP endpoints: one request per
utterance, response decoded to raw PCM for a single TTSAudioRawFrame.

`mulberry` is the only Rumik model with documented male voice presets (the
newer mulberry-1.6 only ships female presets) and is the model that natively
handles Hindi (Devanagari), English, and Hindi/English code-mixed text in one
request — see https://docs.rumik.ai/mulberry.md.
"""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncGenerator

import httpx
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.utils.tracing.service_decorators import traced_tts

# Documented default for Rumik's WAV response — verified against a real
# response: mono, 16-bit PCM, 24000 Hz, standard 44-byte header.
RUMIK_SAMPLE_RATE = 24000


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
        **kwargs,
    ):
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

    def can_generate_metrics(self) -> bool:
        return True

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
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

            if response.status_code != 200:
                logger.error(
                    "Rumik TTS error (status {}): {}",
                    response.status_code,
                    response.text[:300],
                )
                yield ErrorFrame(error=f"Rumik TTS error (status {response.status_code})")
                return

            await self.start_tts_usage_metrics(text)

            with wave.open(io.BytesIO(response.content), "rb") as wav:
                if wav.getframerate() != self.sample_rate:
                    logger.warning(
                        "Rumik returned {}Hz audio, pipeline expects {}Hz",
                        wav.getframerate(),
                        self.sample_rate,
                    )
                pcm = wav.readframes(wav.getnframes())

            await self.stop_ttfb_metrics()
            yield TTSAudioRawFrame(pcm, self.sample_rate, 1, context_id=context_id)
        except Exception as e:
            logger.exception("Rumik TTS request failed")
            yield ErrorFrame(error=f"Rumik TTS error: {e}")
