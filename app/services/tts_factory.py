"""Builds the TTS service for a call from `restaurant.tts_provider` (see
app/config/restaurants/__init__.py's Restaurant.tts_provider docstring) —
the one place that picks the TTS vendor.

Rumik/Sarvam/OpenAI/Deepgram all read Hindi/English code-mixed reply text
straight from the script, no per-reply language switch required for
correct pronunciation. TTSLanguageSwitcher (app/pipeline/
tts_language_switcher.py, wired in app/main.py regardless of provider)
only actually pushes a language Settings update to SarvamTTSService now —
Rumik/OpenAI safely ignore that field, but DeepgramFluxTTSService does
NOT: any settings change, including one it never reads for synthesis,
makes it tear down and rebuild its whole WebSocket connection. Confirmed
live on a real Plivo call — see that module's docstring and
TROUBLESHOOTING.md:
  - Rumik mulberry — natively code-mixed, see app/services/rumik_tts.py.
  - Sarvam bulbul:v3 — the language field IS read (target_language_code),
    which is exactly what TTSLanguageSwitcher exists to keep in sync.
  - OpenAI gpt-4o-mini-tts — reads whatever script is in the text and
    speaks it in the matching language/accent; no language param at all.
  - Deepgram Flux (flux-naveen-en) — an Indian-accented male voice; the
    only Indian English option Deepgram offers (their stable Aura-2 line
    has no en-IN voices, only Flux does). Flux TTS is early access per
    pipecat's DeepgramFluxTTSService docstring — voice catalog/protocol
    may still change. No persona/description knob like Rumik/OpenAI have;
    the fixed voice is the whole steer.

None of these has a retry/fallback wrapper here — same "a TTS failure
just ends the call" posture documented in rumik_tts.py's module docstring,
now covering whichever provider is actually active (see app/main.py's
on_pipeline_error, which checks `frame.processor is tts` rather than a
provider-specific isinstance so this stays true regardless of the switch).
"""

from __future__ import annotations

from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language

from app.config.restaurants import Restaurant
from app.config.settings import Settings
from app.services.rumik_tts import RumikTTSService

TTS_PROVIDERS = ("rumik", "sarvam", "openai", "deepgram")

# Shared persona description/instructions across providers that support
# steering delivery style (Rumik's `description`, OpenAI's `instructions`).
_VOICE_PERSONA = (
    "a male, 30s, indian accent voice, normal pitch, smooth timbre, "
    "conversational pacing, professional register, like a restaurant host"
)


def build_tts(restaurant: Restaurant, settings: Settings) -> TTSService:
    provider = restaurant.tts_provider

    if provider == "rumik":
        return RumikTTSService(
            api_key=settings.rumik_api_key,
            speaker="adam",
            description=_VOICE_PERSONA,
        )

    if provider == "sarvam":
        return SarvamTTSService(
            api_key=settings.sarvam_api_key,
            settings=SarvamTTSService.Settings(
                model="bulbul:v3",
                language=Language.EN_IN,
            ),
        )

    if provider == "openai":
        return OpenAITTSService(
            api_key=settings.openai_api_key,
            settings=OpenAITTSService.Settings(
                model="gpt-4o-mini-tts",
                voice=settings.openai_tts_voice,
                instructions=_VOICE_PERSONA,
            ),
        )

    if provider == "deepgram":
        return DeepgramFluxTTSService(
            api_key=settings.deepgram_api_key,
            settings=DeepgramFluxTTSService.Settings(voice="flux-naveen-en"),
        )

    raise ValueError(
        f"Unknown tts_provider {provider!r} on restaurant {restaurant.name!r} — "
        f"expected one of {TTS_PROVIDERS}"
    )


# Vendor + model labels for build_tts's own branches, kept in sync with them
# by hand (no service instance to introspect at this point) — used to record
# which TTS actually ran on a given call for the admin dashboard's
# LLM/STT/TTS debug view. See app/pipeline/recording.py.
_TTS_LABELS = {
    "rumik": "Rumik (mulberry)",
    "sarvam": "Sarvam (bulbul:v3)",
    "openai": "OpenAI (gpt-4o-mini-tts)",
    "deepgram": "Deepgram (flux-naveen-en)",
}


def describe_tts(restaurant: Restaurant) -> str:
    return _TTS_LABELS.get(restaurant.tts_provider, restaurant.tts_provider)
