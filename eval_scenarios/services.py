"""TTS/STT factories for audio-modality eval scenarios.

The harness's built-in audio services need local ONNX/torch models not in
this venv, so these reuse hosted services already in requirements.txt
(Sarvam TTS, OpenAI Whisper). Referenced from scenario YAML via `factory:`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The eval CLI is invoked from the repo root, but make this importable
# regardless of cwd so `factory:` resolution never depends on it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import settings  # noqa: E402


def sarvam_user_speech(voice_cfg: dict, sample_rate: int):
    """Build a real Sarvam HTTP TTS service for scripted caller turns.

    Voice must be a bulbul:v3 speaker name (e.g. priya, aditya, ritu) —
    bulbul:v2 is deprecated on Sarvam's API and its speaker names aren't
    valid on v3.
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


def judge_llm(config: dict):
    """Build the eval judge's LLM with temperature=0 for determinism.

    Repeated identical runs flipped verdicts under the API's default
    temperature=1 (pure sampling noise, not real bot behavior change).
    gpt-5-family models reject temperature=0 (400 error) and need
    reasoning_effort="minimal" instead, or their 200-token judge budget
    gets consumed by hidden reasoning before any output.
    """
    from pipecat.services.openai.llm import OpenAILLMService

    model = config.get("model", "gpt-4o")
    settings_kwargs: dict = {"model": model}
    if not model.startswith("gpt-5"):
        settings_kwargs["temperature"] = 0
    else:
        settings_kwargs["extra"] = {"reasoning_effort": "minimal"}

    return OpenAILLMService(settings=OpenAILLMService.Settings(**settings_kwargs))


class _EnsembleJudgeService:
    """Wraps two judge LLM services behind one run_inference(), used as a
    single unit by EvalJudge.

    Trusts the primary's "yes" outright, but only trusts a "no" if the
    secondary doesn't contradict it — a false "no" wrongly fails a correct
    bot reply, the costliest judge mistake. gpt-4o-mini is the primary
    (more consistent in testing); gpt-5-mini is only a safety net.
    """

    def __init__(self, primary, secondary):
        self._primary = primary
        self._secondary = secondary

    async def run_inference(self, *, context, max_tokens, system_instruction):
        from loguru import logger

        from pipecat.evals.judge import _parse_verdict

        primary_response = await self._primary.run_inference(
            context=context, max_tokens=max_tokens, system_instruction=system_instruction
        )
        primary_verdict = _parse_verdict(primary_response or "")
        if primary_verdict.verdict != "no":
            return primary_response

        try:
            secondary_response = await self._secondary.run_inference(
                context=context, max_tokens=max_tokens, system_instruction=system_instruction
            )
            secondary_verdict = _parse_verdict(secondary_response or "")
        except Exception as e:
            logger.debug("Ensemble judge: secondary check failed ({}), keeping primary's no", e)
            return primary_response

        if secondary_verdict.verdict == "yes":
            logger.info(
                "Ensemble judge: primary said no, secondary disagreed (yes) -- overriding. "
                "primary_reason={!r} secondary_reason={!r}",
                primary_verdict.reason,
                secondary_verdict.reason,
            )
            return secondary_response

        return primary_response


def ensemble_judge_llm(config: dict):
    """Default judge factory: gpt-4o-mini as primary, gpt-5-mini consulted
    only to double-check a "no" — see _EnsembleJudgeService's docstring.

    Config keys: `model` (default gpt-4o-mini), `secondary_model`
    (default gpt-5-mini).
    """
    primary = judge_llm({"model": config.get("model", "gpt-4o-mini")})
    secondary = judge_llm({"model": config.get("secondary_model", "gpt-5-mini")})
    return _EnsembleJudgeService(primary, secondary)


def openai_bot_transcription(config: dict, sample_rate: int):
    """Batch-transcribe bot audio via OpenAI's REST Whisper endpoint.

    Not pipecat's OpenAISTTService — that's realtime-WebSocket only and
    rejects captured PCM as an unsupported format when called directly.
    """
    import io
    import wave

    from openai import AsyncOpenAI
    from pipecat.frames.frames import TranscriptionFrame

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    class _BatchWhisperTranscriber:
        async def run_stt(self, audio: bytes):
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # pipecat's captured audio is 16-bit PCM throughout
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            buffer.seek(0)
            buffer.name = "audio.wav"  # gives the upload a filename/content-type

            response = await client.audio.transcriptions.create(
                model="whisper-1",
                file=buffer,
            )
            yield TranscriptionFrame(text=response.text, user_id="", timestamp="")

    return _BatchWhisperTranscriber()
