"""Sarvam STT with connect retries and a call-health escalation hook.

pipecat's own SarvamSTTService never retries a failed connect, leaving the
caller in silence for the rest of the call. This wraps `_connect` with a
few retries, then calls `on_connect_exhausted` so the bot can apologize
and hang up instead (see CallHealthMonitor.degrade_with_apology).

Deliberately doesn't swap to OpenAI's STT mid-call — Sarvam's persistent
WebSocket stream vs. OpenAI's batch model risks a subtly broken swap.
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
        """on_connect_exhausted: awaited once every connect attempt fails.
        Wired in app/main.py to CallHealthMonitor.degrade_with_apology."""
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
