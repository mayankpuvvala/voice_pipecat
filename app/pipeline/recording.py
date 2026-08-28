"""Saves each call's full audio to Drive and logs it to the Recordings sheet.

Uses app.services.drive_oauth_client (uploads as an actual human Google
account via OAuth) as the near-term backend — app.services.r2_client
(Cloudflare R2) is the intended longer-term one, parked until R2 is actually
activated on the Cloudflare account; swap the import below once it is.

Wired to AudioBufferProcessor's on_audio_data event (see app/main.py), which
fires once per call — with the complete merged user+bot audio — right as the
call ends. Awaited directly rather than fired-and-forgotten: the small delay
this adds to pipeline teardown happens after all audio has already reached
the caller, so it's invisible to them, and awaiting it guarantees the
recording is actually saved before the worker is considered stopped rather
than risking it getting cut off mid-upload.
"""

from __future__ import annotations

import asyncio
import io
import wave
from datetime import datetime, timezone

from loguru import logger

from app.services import drive_oauth_client as recording_backend
from app.services import sheets_client

_RECORDINGS_SHEET = "Recordings"
_SAMPLE_WIDTH_BYTES = 2  # pipecat's raw audio frames are 16-bit PCM throughout


def _to_wav_bytes(pcm_audio: bytes, sample_rate: int, num_channels: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(_SAMPLE_WIDTH_BYTES)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_audio)
    return buffer.getvalue()


async def save_call_recording(
    call_session_id: str,
    caller_phone: str,
    pcm_audio: bytes,
    sample_rate: int,
    num_channels: int,
) -> None:
    """Encode, upload, and log one call's recording. Never raises — a failed
    recording save shouldn't take down call teardown."""
    if not pcm_audio:
        logger.debug("No audio captured for call {} — skipping recording save", call_session_id)
        return

    wav_bytes = _to_wav_bytes(pcm_audio, sample_rate, num_channels)
    duration_secs = len(pcm_audio) / (sample_rate * num_channels * _SAMPLE_WIDTH_BYTES)
    now = datetime.now(timezone.utc)
    filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{call_session_id}.wav"

    try:
        recording_url = await asyncio.to_thread(
            recording_backend.upload_recording, filename, wav_bytes
        )
    except Exception:
        logger.exception("Failed to upload call recording for {}", call_session_id)
        return

    row = {
        "Timestamp": now.isoformat(),
        "CallSessionId": call_session_id,
        "CallerPhone": caller_phone,
        "DurationSecs": f"{duration_secs:.1f}",
        "RecordingURL": recording_url,
    }
    try:
        await asyncio.to_thread(sheets_client.append_row, _RECORDINGS_SHEET, row)
    except Exception:
        logger.exception("Failed to log recording row for {}", call_session_id)
