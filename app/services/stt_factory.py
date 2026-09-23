"""Builds the STT service for a call from `restaurant.stt_provider` (see
app/config/restaurants/__init__.py's Restaurant.stt_provider docstring) —
the one place that picks the STT vendor.

Every branch here targets Hindi/English code-switched speech, since that's
what real calls to these restaurants sound like:
  - Sarvam saaras:v3 in "codemix" mode, language hinted HI_IN — Sarvam's
    own code-mixed transcription mode (see resilient_stt.py).
  - Deepgram nova-3 with language="multi" — Deepgram's multilingual
    code-switching mode; Hindi is one of the ~10 languages nova-3 "multi"
    switches between mid-utterance (docs.deepgram.com/docs/
    multilingual-code-switching). "multi" isn't a pipecat Language enum
    member, so it's passed as the raw string the base Settings.language
    field already accepts.

Both wrap their pipecat base service with a resilient subclass that fires
`on_connect_exhausted` once the provider's own retries are exhausted, so
CallHealthMonitor can apologize and hang up instead of leaving the caller
in silence — see resilient_stt.py and resilient_deepgram_stt.py for why
each needs a different wrapping strategy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language

from app.config.restaurants import Restaurant
from app.config.settings import Settings
from app.services.resilient_deepgram_stt import ResilientDeepgramSTTService
from app.services.resilient_stt import ResilientSarvamSTTService

STT_PROVIDERS = ("sarvam", "deepgram")


def build_stt(
    restaurant: Restaurant,
    settings: Settings,
    *,
    on_connect_exhausted: Callable[[str], Awaitable[None]] | None = None,
) -> STTService:
    provider = restaurant.stt_provider

    if provider == "sarvam":
        return ResilientSarvamSTTService(
            api_key=settings.sarvam_api_key,
            mode="codemix",
            settings=SarvamSTTService.Settings(
                model="saaras:v3",
                language=Language.HI_IN,
                start_speech_volume_threshold=-40.0,
            ),
            on_connect_exhausted=on_connect_exhausted,
        )

    if provider == "deepgram":
        return ResilientDeepgramSTTService(
            api_key=settings.deepgram_api_key,
            settings=DeepgramSTTService.Settings(
                model="nova-3",
                language="multi",
                smart_format=True,
            ),
            on_connect_exhausted=on_connect_exhausted,
        )

    raise ValueError(
        f"Unknown stt_provider {provider!r} on restaurant {restaurant.name!r} — "
        f"expected one of {STT_PROVIDERS}"
    )
