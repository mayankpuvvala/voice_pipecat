"""Turns a total, unrecoverable provider failure mid-call into a clean,
honest ending instead of dead air the caller sits through until the
telephony provider's own timeout finally hangs up on them.

STT, LLM, and TTS each retry/fall back on their own first — see
resilient_stt.py's connect retries, rumik_tts.py's Rumik→OpenAI fallback,
and main.py's `retry_on_timeout=True` on OpenAILLMService. By the time any
of them calls into a CallHealthMonitor, its own retries are exhausted —
this is the last resort, not the first line of defense.

Two endings, depending on whether TTS itself is the thing that died:

- `degrade_with_apology`: STT or LLM failed but TTS is presumed to still
  work, so speak a short apology and hang up — the caller gets *something*
  instead of silence.
- `degrade_silently`: TTS itself exhausted every provider it has, so there
  is nothing left to speak with; just end the call.

Either way this logs at CRITICAL with a fixed, greppable prefix
(CALL_HEALTH_DEVELOPER_ALERT) meant to get someone's attention, not blend
into normal logs. See TROUBLESHOOTING.md.
"""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame


class CallHealthMonitor:
    """One instance per call — see app/main.py's `run_bot`."""

    # A single LLM failure already means its own retries were exhausted for
    # that turn — but one failed turn is still cheap to let the caller
    # repeat, so this waits for a second before deciding the LLM is down.
    _LLM_FAILURE_THRESHOLD = 2

    def __init__(
        self,
        *,
        worker_handle,
        capture_transcript,
        call_session_id: str,
        caller_phone: str,
        restaurant_name: str,
    ) -> None:
        self._worker_handle = worker_handle
        self._capture_transcript = capture_transcript
        self._call_session_id = call_session_id
        self._caller_phone = caller_phone
        self._apology_text = (
            f"I'm really sorry, we're having a technical issue on our end right now. "
            f"Please try calling {restaurant_name} back in a few minutes. "
            "Thank you for your patience, and sorry again for the trouble."
        )
        self._triggered = False
        self._llm_failures = 0

    async def note_llm_failure(self, detail: str) -> None:
        """Call on every OpenAILLMService ErrorFrame. Only escalates to
        degrade_with_apology once `_LLM_FAILURE_THRESHOLD` turns in this same
        call have failed."""
        self._llm_failures += 1
        logger.error(
            "CALL_HEALTH stage=llm failure {}/{} call_session_id={} — {}",
            self._llm_failures,
            self._LLM_FAILURE_THRESHOLD,
            self._call_session_id,
            detail,
        )
        if self._llm_failures >= self._LLM_FAILURE_THRESHOLD:
            await self.degrade_with_apology("llm", detail)

    async def degrade_with_apology(self, stage: str, detail: str) -> None:
        """A provider is dead but TTS is presumed to still work — speak an
        apology, then hang up. Used for STT connect failures and repeated LLM
        failures."""
        await self._degrade(stage, detail, speak_apology=True)

    async def degrade_silently(self, stage: str, detail: str) -> None:
        """TTS itself is the thing that's dead — there is no way to speak to
        the caller, so just end the call."""
        await self._degrade(stage, detail, speak_apology=False)

    async def _degrade(self, stage: str, detail: str, *, speak_apology: bool) -> None:
        if self._triggered:
            logger.warning(
                "CALL_HEALTH: stage={} also failed ({}) but the call is already "
                "ending — ignoring",
                stage,
                detail,
            )
            return
        self._triggered = True

        logger.critical(
            "CALL_HEALTH_DEVELOPER_ALERT call_session_id={} caller={} stage={} "
            "speak_apology={} detail={!r} — ending call",
            self._call_session_id,
            self._caller_phone,
            stage,
            speak_apology,
            detail,
        )

        # on_client_disconnected only fires for a caller-initiated close (same
        # reason end_call.py captures it explicitly instead) — this is a
        # bot-initiated one.
        self._capture_transcript()

        worker = self._worker_handle.worker
        if worker is None:
            logger.error("CALL_HEALTH: no worker available to end the call cleanly")
            return

        frames = [TTSSpeakFrame(self._apology_text)] if speak_apology else []
        frames.append(EndWorkerFrame())
        await worker.queue_frames(frames)
