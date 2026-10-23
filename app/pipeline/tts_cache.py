"""Greeting audio cache: the one TTS utterance that is identical on every call.

Every call opens with `Restaurant.first_message`. Synthesizing it per call costs
TTS characters and, more noticeably, adds the TTS socket-connect + first-audio
delay right at pickup. This records the greeting's audio the first time it is
synthesized (on a cache miss) and replays the stored PCM on later calls.

Deliberately NOT cached: the LLM's replies. They are generated per turn and
rarely repeat verbatim, so a lookup would almost always miss (measure with
scripts/top_bot_phrases.py before building anything for them). The apology in
call_health.py only plays when something is already failing, so it can't be
recorded safely either.

Off unless TTS_CACHE=true. Keyed by TTS provider/voice + text + sample rate, so
switching voice or telephony codec never replays the wrong audio.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    Frame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# 200 ms of 16-bit mono per replayed frame -- same ballpark as a streamed TTS chunk.
_REPLAY_CHUNK_SECS = 0.2


class GreetingCache:
    def __init__(self, base_dir: Path, namespace: str, text: str) -> None:
        self._dir = Path(base_dir)
        self._namespace = namespace
        self._text = text

    def _path(self, sample_rate: int) -> Path:
        digest = hashlib.sha256(f"{self._namespace}|{sample_rate}|{self._text}".encode()).hexdigest()[:24]
        return self._dir / f"greeting-{digest}.pcm"

    def load(self, sample_rate: int) -> bytes | None:
        try:
            data = self._path(sample_rate).read_bytes()
        except OSError:
            return None
        return data if len(data) >= 2 else None

    def save(self, sample_rate: int, pcm: bytes) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._path(sample_rate)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(pcm)
            tmp.replace(path)  # atomic: a concurrent call never reads a half-written file
        except OSError:
            logger.exception("tts_cache: could not save greeting audio")


def replay_frames(pcm: bytes, sample_rate: int) -> list[Frame]:
    """TTSStarted, audio chunks, TTSStopped -- what the output transport expects."""
    context_id = uuid.uuid4().hex
    chunk_bytes = max(2, int(sample_rate * _REPLAY_CHUNK_SECS) * 2)
    chunk_bytes -= chunk_bytes % 2  # never split a 16-bit sample
    frames: list[Frame] = [TTSStartedFrame(context_id=context_id, append_to_context=False)]
    for i in range(0, len(pcm), chunk_bytes):
        frames.append(
            TTSAudioRawFrame(audio=pcm[i : i + chunk_bytes], sample_rate=sample_rate, num_channels=1, context_id=context_id)
        )
    frames.append(TTSStoppedFrame(context_id=context_id))
    return frames


class _Capture:
    """Pure state machine behind GreetingRecorder (kept separate so it's testable)."""

    def __init__(self) -> None:
        self.armed = False
        self._chunks: list[bytes] = []
        self._rate = 0
        self._recording = False

    def arm(self) -> None:
        self.armed, self._recording, self._chunks = True, False, []

    def feed(self, frame: Frame) -> tuple[int, bytes] | None:
        """Returns (sample_rate, pcm) once a complete, uninterrupted utterance was captured."""
        if not self.armed:
            return None
        if isinstance(frame, (InterruptionFrame, CancelFrame)):
            self.armed = self._recording = False  # truncated audio must never be cached
            self._chunks = []
        elif isinstance(frame, TTSStartedFrame):
            self._recording, self._chunks = True, []
        elif isinstance(frame, TTSAudioRawFrame) and self._recording:
            if frame.num_channels != 1:
                self.armed = self._recording = False
                return None
            self._rate = frame.sample_rate
            self._chunks.append(frame.audio)
        elif isinstance(frame, TTSStoppedFrame) and self._recording:
            self.armed = self._recording = False
            pcm = b"".join(self._chunks)
            self._chunks = []
            if pcm:
                return self._rate, pcm
        return None


class GreetingRecorder(FrameProcessor):
    """Sits right after the TTS service. Passes every frame through untouched;
    while armed, records the next TTS utterance into the cache."""

    def __init__(self, cache: GreetingCache, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cache = cache
        self._capture = _Capture()

    def arm(self) -> None:
        self._capture.arm()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        done = self._capture.feed(frame)
        if done:
            rate, pcm = done
            self._cache.save(rate, pcm)
            logger.info("tts_cache: stored greeting ({} bytes @ {} Hz)", len(pcm), rate)
        await self.push_frame(frame, direction)
