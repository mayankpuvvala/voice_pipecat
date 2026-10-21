"""Tracks whether the bot's audio is still playing to the caller.

Exists for transfer_call: once the provider's Transfer API moves the
caller's leg, our <Stream> is gone, so anything still playing is cut off
mid-word. turn_taking_guard only knows text was *sent* to TTS, not that
the audio finished — this knows the latter, from the output transport's
own BotStarted/StoppedSpeakingFrames. Fed by app/main.py's
on_frame_reached_downstream handler rather than being a pipeline stage,
so it adds nothing to the live audio path.
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger
from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame


class BotSpeechTracker:
    FRAME_TYPES = (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)

    def __init__(self) -> None:
        self._speaking = False
        self._started = asyncio.Event()
        self._stopped = asyncio.Event()
        self._stopped.set()

    @property
    def speaking(self) -> bool:
        return self._speaking

    def observe(self, frame: Frame) -> None:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._speaking = True
            self._stopped.clear()
            self._started.set()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._speaking = False
            self._started.clear()
            self._stopped.set()

    async def wait_until_quiet(
        self,
        *,
        start_timeout: float = 2.0,
        max_wait: float = 15.0,
        settle: float = 0.6,
    ) -> bool:
        """Wait for the line just sent to TTS to finish playing.

        If audio hasn't started yet (TTS still synthesizing), waits up to
        `start_timeout` for it to start — past that, assumes it already
        played. Then waits for it to stop, and requires `settle` seconds of
        continued silence so a pause between two sentences of the same line
        isn't mistaken for the end. Bounded by `max_wait` overall: returns
        False if that runs out (the caller proceeds anyway — a clipped line
        beats a call stuck waiting forever).
        """
        deadline = time.monotonic() + max_wait

        def remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        if not self._speaking:
            try:
                await asyncio.wait_for(self._started.wait(), min(start_timeout, remaining()))
            except TimeoutError:
                return True

        while True:
            try:
                await asyncio.wait_for(self._stopped.wait(), remaining())
            except TimeoutError:
                logger.warning("BotSpeechTracker: still speaking after {}s — giving up", max_wait)
                return False
            try:
                await asyncio.wait_for(self._started.wait(), min(settle, remaining()))
            except TimeoutError:
                return True
            if remaining() <= 0:
                return False
