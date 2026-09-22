"""Turns a total, unrecoverable provider failure mid-call into a clean,
honest ending instead of dead air until the telephony provider times out.
Last resort after STT/LLM/TTS each exhaust their own retries/fallbacks.

`degrade_with_apology` speaks a short apology then hangs up (TTS presumed
alive); `degrade_silently` just ends the call (TTS itself is dead). Both
log at CRITICAL and fire an SMS via OPS_ALERT_PHONE if configured — see
TROUBLESHOOTING.md.
"""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame

from app.config.settings import settings
from app.services.twilio_client import send_sms


class CallHealthMonitor:
    """One instance per call — see app/main.py's `run_bot`."""

    # One failed turn is cheap to let the caller repeat; wait for a second
    # before deciding the LLM is actually down.
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
        self._restaurant_name = restaurant_name
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

        if settings.ops_alert_phone:
            sent = await send_sms(
                settings.ops_alert_phone,
                f"[{self._restaurant_name}] Live call degraded (stage={stage}) and is "
                f"being ended. call_session_id={self._call_session_id} detail={detail}",
            )
            logger.info("Ops alert SMS for degraded call {}: sent={}", self._call_session_id, sent)

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
