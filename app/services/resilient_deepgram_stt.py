"""Deepgram STT with a call-health escalation hook, matching
ResilientSarvamSTTService's contract (see that module's docstring) so
app/services/stt_factory.py can swap providers without app/main.py caring.

Unlike Sarvam, pipecat's own DeepgramSTTService already retries a dropped/
failed connection forever with exponential backoff (`_connection_handler`'s
while-loop) and only gives up — permanently, no more retries — in two
places: a 4xx `ApiError` at the handshake (bad API key), or too many quick
failures in a row (`QuickFailureTracker`). Both paths already call
`push_error` then `return`, ending the loop for good. So this wrapper
doesn't reimplement retry logic like the Sarvam one does; it just watches
the connection task and fires `on_connect_exhausted` if that task ever
finishes on its own (give-up) rather than via `_disconnect()`'s deliberate
cancellation, using the error message from the last `push_error` call as
the reason.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger

from pipecat.services.deepgram.stt import DeepgramSTTService


class ResilientDeepgramSTTService(DeepgramSTTService):
    def __init__(
        self,
        *,
        on_connect_exhausted: Callable[[str], Awaitable[None]] | None = None,
        **kwargs,
    ) -> None:
        """on_connect_exhausted: awaited once Deepgram's own reconnect loop
        gives up for good. Wired in app/main.py to
        CallHealthMonitor.degrade_with_apology."""
        super().__init__(**kwargs)
        self._on_connect_exhausted = on_connect_exhausted
        self._last_error_msg = "Deepgram STT: connection permanently failed"

    async def push_error(self, error_msg: str, exception: Exception | None = None, fatal: bool = False):
        self._last_error_msg = error_msg
        await super().push_error(error_msg, exception=exception, fatal=fatal)

    async def _connect(self) -> None:
        await super()._connect()
        if self._connection_task is not None:
            self.create_task(self._watch_for_exhausted_connection(self._connection_task))

    async def _watch_for_exhausted_connection(self, task: asyncio.Task) -> None:
        try:
            await task
        except asyncio.CancelledError:
            # A deliberate _disconnect() (call ending, or _do_reconnect's own
            # disconnect-then-reconnect for a settings update) — not a give-up.
            return
        except Exception:
            # _connection_handler itself doesn't raise past its own try/except;
            # anything here is unexpected, not a normal give-up. Leave it to
            # whatever already logged/pushed the error.
            return

        reason = f"stt: Deepgram gave up reconnecting — {self._last_error_msg}"
        logger.critical(
            "CALL_HEALTH stage=stt — {} — no STT fallback provider is wired up for "
            "live audio, see TROUBLESHOOTING.md",
            reason,
        )
        if self._on_connect_exhausted is not None:
            await self._on_connect_exhausted(reason)
