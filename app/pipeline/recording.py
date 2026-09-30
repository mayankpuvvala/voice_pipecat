"""Saves each call's full audio, transcript, and summary to the Recordings sheet.

Uses drive_oauth_client as the near-term backend — r2_client (Cloudflare
R2) is the intended longer-term one, parked until R2 is activated.

Wired to AudioBufferProcessor's on_audio_data/on_recording_stopped, with
buffer_size set (see app.main) so the processor flushes periodically
instead of holding the whole call's raw PCM in memory until it ends.
CallRecorder streams each flushed chunk straight to a temp WAV file on
disk, so the process never holds more than one chunk's worth of audio in
RAM regardless of call length or how many calls are concurrently active.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
import wave
from datetime import datetime, timezone

from loguru import logger

from app.pipeline.transcript import generate_call_summary
from app.services import drive_oauth_client as recording_backend
from app.services import sheets_client

_RECORDINGS_SHEET = "Recordings"
_SAMPLE_WIDTH_BYTES = 2  # pipecat's raw audio frames are 16-bit PCM throughout
_MAX_TRANSCRIPT_CHARS = 45_000  # Sheets cells cap at 50,000 chars; leave headroom


class CallRecorder:
    """Accumulates one call's audio on disk instead of in memory.

    Create one per call, feed it chunks as AudioBufferProcessor flushes
    them (on_audio_data), then call finalize() once (on_recording_stopped)
    to upload the file and log the Recordings row. The temp file is always
    removed afterward, success or failure.
    """

    def __init__(
        self,
        call_session_id: str,
        caller_phone: str,
        llm_provider: str = "",
        stt_provider: str = "",
        tts_provider: str = "",
    ):
        self._call_session_id = call_session_id
        self._caller_phone = caller_phone
        self._llm_provider = llm_provider
        self._stt_provider = stt_provider
        self._tts_provider = tts_provider

        self._path = os.path.join(
            tempfile.gettempdir(), f"call-recording-{call_session_id}-{uuid.uuid4().hex}.wav"
        )
        self._wave_writer: wave.Wave_write | None = None
        self._frame_bytes = 0
        self._sample_rate = 0
        self._num_channels = 1
        self._closed = False
        # pipecat's event handlers run as unawaited background tasks (see
        # base_object.py's _call_event_handler — each handler call is wrapped
        # in asyncio.create_task and never awaited by the caller), so
        # on_recording_stopped's finalize() can otherwise start, and close
        # the wave file, while a still-in-flight on_audio_data chunk write
        # is mid-write on a different thread — crashing with "'NoneType'
        # object has no attribute 'write'" inside wave.py. This lock
        # serializes add_chunk against itself and against finalize so the
        # file is never closed out from under a pending write.
        self._lock = asyncio.Lock()

    async def add_chunk(self, pcm_chunk: bytes, sample_rate: int, num_channels: int) -> None:
        """Append one flushed chunk straight to the temp WAV file on disk."""
        if not pcm_chunk:
            return
        async with self._lock:
            if self._closed:
                logger.warning(
                    "Dropping audio chunk for call {} — already finalized",
                    self._call_session_id,
                )
                return
            await asyncio.to_thread(
                self._write_chunk_sync, pcm_chunk, sample_rate, num_channels
            )

    def _write_chunk_sync(self, pcm_chunk: bytes, sample_rate: int, num_channels: int) -> None:
        if self._wave_writer is None:
            self._sample_rate = sample_rate
            self._num_channels = num_channels
            self._wave_writer = wave.open(self._path, "wb")
            self._wave_writer.setnchannels(num_channels)
            self._wave_writer.setsampwidth(_SAMPLE_WIDTH_BYTES)
            self._wave_writer.setframerate(sample_rate)
        self._wave_writer.writeframes(pcm_chunk)
        self._frame_bytes += len(pcm_chunk)

    async def finalize(self, transcript: str) -> None:
        """Close the file, upload it, log the Recordings row, then remove it.

        Never raises — a failure here shouldn't take down call teardown.
        """
        async with self._lock:
            if self._wave_writer is not None:
                self._wave_writer.close()
                self._wave_writer = None
            self._closed = True

        try:
            await self._save(transcript)
        finally:
            try:
                if os.path.exists(self._path):
                    os.remove(self._path)
            except OSError:
                logger.exception("Failed to remove temp recording file {}", self._path)

    async def _save(self, transcript: str) -> None:
        has_audio = self._frame_bytes > 0
        if not has_audio and not transcript:
            logger.debug(
                "Nothing captured for call {} — skipping recording save", self._call_session_id
            )
            return

        now = datetime.now(timezone.utc)
        recording_url = ""
        duration_secs = 0.0

        if has_audio:
            duration_secs = self._frame_bytes / (
                self._sample_rate * self._num_channels * _SAMPLE_WIDTH_BYTES
            )
            filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{self._call_session_id}.wav"
            try:
                recording_url = await asyncio.to_thread(
                    recording_backend.upload_recording, filename, self._path
                )
            except Exception:
                logger.exception(
                    "Failed to upload call recording for {}", self._call_session_id
                )
                # Keep going — still want the transcript/summary saved even if
                # the audio upload failed.

        transcript = transcript[:_MAX_TRANSCRIPT_CHARS]
        summary = await generate_call_summary(transcript)

        row = {
            "Timestamp": now.isoformat(),
            "CallSessionId": self._call_session_id,
            "CallerPhone": self._caller_phone,
            "DurationSecs": f"{duration_secs:.1f}",
            "RecordingURL": recording_url,
            "Transcript": transcript,
            "Summary": summary,
            "LLMProvider": self._llm_provider,
            "STTProvider": self._stt_provider,
            "TTSProvider": self._tts_provider,
        }
        try:
            await asyncio.to_thread(sheets_client.append_row, _RECORDINGS_SHEET, row)
        except Exception:
            logger.exception("Failed to log recording row for {}", self._call_session_id)
