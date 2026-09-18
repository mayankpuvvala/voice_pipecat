"""Sarvam STT with connect retries and a call-health escalation hook.

pipecat's own SarvamSTTService never retries a failed WebSocket connect and
never reconnects if the receive loop dies — `_connect`/`_receive_task_handler`
both catch their own exceptions and call `push_error` (non-fatal), leaving
`_socket_client` permanently None with nothing downstream reacting. A bad
key, an outage, or a dropped connection means the caller hears silence for
the rest of the call — pipecat's own STT resilience stops at "log it and
give up."

This wraps `_connect` with a few retries for transient connect failures, and
— if every attempt still fails — calls `on_connect_exhausted` so the bot can
apologize and hang up instead of sitting in dead air (see
app/pipeline/call_health.py's CallHealthMonitor.degrade_with_apology).

Deliberately does NOT swap to OpenAI's STT mid-call: Sarvam is a persistent
WebSocket stream (audio pushed continuously, transcripts async over the same
socket) vs. OpenAI's request-per-utterance batch model. Splicing one into an
already-linked pipecat Pipeline stage without live testing risks a subtly
broken swap (dropped audio, duplicated VAD frames, wrong task lifecycle) —
worse than the honest apologize-and-end fallback below. Needs a real test
call to revisit, not just code that looks right.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger

from pipecat.services.sarvam.stt import SarvamSTTService

_MAX_CONNECT_ATTEMPTS = 3
_RETRY_DELAY_SECS = 1.0


class ResilientSarvamSTTService(SarvamSTTService):
    def __init__(
        self,
        *,
        on_connect_exhausted: Callable[[str], Awaitable[None]] | None = None,
        **kwargs,
    ) -> None:
        """Args (beyond SarvamSTTService's own):
            on_connect_exhausted: Awaited with a short reason string once
                every connect attempt has failed. Wired in app/main.py to
                CallHealthMonitor.degrade_with_apology (stage="stt").
        """
        super().__init__(**kwargs)
        self._on_connect_exhausted = on_connect_exhausted

    async def _connect(self) -> None:
        for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
            await super()._connect()
            if self._socket_client is not None:
                if attempt > 1:
                    logger.info(
                        "STT (Sarvam): connected on attempt {}/{}",
                        attempt,
                        _MAX_CONNECT_ATTEMPTS,
                    )
                return

            if attempt < _MAX_CONNECT_ATTEMPTS:
                logger.warning(
                    "STT (Sarvam): connect attempt {}/{} failed, retrying in {}s",
                    attempt,
                    _MAX_CONNECT_ATTEMPTS,
                    _RETRY_DELAY_SECS,
                )
                await asyncio.sleep(_RETRY_DELAY_SECS)

        reason = f"stt: Sarvam failed to connect after {_MAX_CONNECT_ATTEMPTS} attempts"
        logger.critical(
            "CALL_HEALTH stage=stt — {} — no STT fallback provider is wired up for "
            "live audio, see TROUBLESHOOTING.md",
            reason,
        )
        if self._on_connect_exhausted is not None:
            await self._on_connect_exhausted(reason)
