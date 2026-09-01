"""Custom TTS/STT factories for audio-modality eval scenarios.

The eval harness's built-in audio services (Kokoro for user speech, Whisper/
Moonshine for judge transcription) all run local ONNX/torch models that aren't
installed in this project's venv (see requirements.txt — nothing beyond
pipecat-ai[openai,silero,websocket,sarvam,runner] is pulled in for this repo,
and this file deliberately doesn't add new dependencies for a test harness,
mirroring the RNNoise lesson about not casually adding heavyweight ML deps).

Instead this reuses services already in requirements.txt, authenticated with
the same credentials the bot itself uses (loaded via app.config.settings):

- `sarvam_user_speech` synthesizes the caller's scripted turns with Sarvam's
  own HTTP TTS (bulbul:v2) — real Hindi/English speech, not a generic voice,
  so it's a meaningful test of the bot's actual SarvamSTTService (hi-IN,
  mode="codemix") rather than a proxy.
- `openai_bot_transcription` transcribes the bot's synthesized replies with
  OpenAI's hosted Whisper endpoint (HTTP, not local) so the judge can assert
  on what the bot's real TTS audio actually said.

Referenced from scenario YAML via the `factory:` escape hatch documented in
pipecat.evals.speech / pipecat.evals.transcribe, e.g.:

    user:
      modality: audio
      speech:
        factory: "eval_scenarios.services.sarvam_user_speech"
        language: hi-IN

    judge:
      modality: audio
      transcription:
        factory: "eval_scenarios.services.openai_bot_transcription"
"""

from __future__ import annotations

import sys
from pathlib import Path

# The eval CLI is invoked from the repo root, but make this importable
# regardless of cwd so `factory:` resolution never depends on it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import settings  # noqa: E402


def sarvam_user_speech(voice_cfg: dict, sample_rate: int):
    """Build a real Sarvam HTTP TTS service to synthesize scripted caller turns.

    `voice_cfg.language` picks the target language (e.g. "hi-IN", "en-IN");
    `voice_cfg.voice` picks a bulbul:v3 speaker (default "priya"). bulbul:v2
    is deprecated on Sarvam's live API as of this writing (confirmed via a
    real call: "Model 'bulbul:v2' has been deprecated. Please use
    'bulbul:v3' instead.") — v3's speaker roster is disjoint from v2's, so
    the voice picked here must be a v3 name (aditya/ritu/priya/neha/rahul/
    pooja/rohan/simran/kavya/... — see SarvamHttpTTSService's docstring),
    not a v2 one like "anushka".
    """
    import aiohttp

    from pipecat.services.sarvam.tts import SarvamHttpTTSService
    from pipecat.transcriptions.language import Language

    session = aiohttp.ClientSession()
    language = Language(voice_cfg.get("language", "hi-IN"))
    return SarvamHttpTTSService(
        api_key=settings.sarvam_api_key,
        aiohttp_session=session,
        sample_rate=sample_rate,
        settings=SarvamHttpTTSService.Settings(
            voice=voice_cfg.get("voice", "priya"),
            model="bulbul:v3",
            language=language,
        ),
    )


def openai_bot_transcription(config: dict, sample_rate: int):
    """Build a real OpenAI Whisper (HTTP) STT service to transcribe bot audio."""
    from pipecat.services.openai.stt import OpenAISTTService

    return OpenAISTTService(
        api_key=settings.openai_api_key,
        settings=OpenAISTTService.Settings(model="whisper-1"),
    )
