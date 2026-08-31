"""Saves each call's full audio, transcript, and summary to the Recordings sheet.

Uses app.services.drive_oauth_client (uploads as an actual human Google
account via OAuth) as the near-term recording backend — app.services.r2_client
(Cloudflare R2) is the intended longer-term one, parked until R2 is actually
activated on the Cloudflare account; swap the import below once it is.

Wired to AudioBufferProcessor's on_audio_data event (see app/main.py), which
fires once per call — with the complete merged user+bot audio — right as the
call ends. Awaited directly rather than fired-and-forgotten: the small delay
this adds to pipeline teardown happens after all audio has already reached
the caller, so it's invisible to them, and awaiting it guarantees the
recording is actually saved before the worker is considered stopped rather
than risking it getting cut off mid-upload.

Audio upload, summary generation, and the sheet write are independent best
efforts — a failure in one (e.g. the recording upload) doesn't prevent the
others (transcript/summary) from still being saved.
"""

from __future__ import annotations

import asyncio
import io
import wave
from datetime import datetime, timezone

from loguru import logger

from app.pipeline.transcript import generate_call_summary
from app.services import drive_oauth_client as recording_backend
from app.services import sheets_client

_RECORDINGS_SHEET = "Recordings"
_SAMPLE_WIDTH_BYTES = 2  # pipecat's raw audio frames are 16-bit PCM throughout
_MAX_TRANSCRIPT_CHARS = 45_000  # Sheets cells cap at 50,000 chars; leave headroom


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
    transcript: str = "",
) -> None:
    """Encode/upload the audio, generate a summary, and log one row for this
    call. Never raises — a failure here shouldn't take down call teardown."""
    if not pcm_audio and not transcript:
        logger.debug("Nothing captured for call {} — skipping recording save", call_session_id)
        return

    now = datetime.now(timezone.utc)
    recording_url = ""
    duration_secs = 0.0

    if pcm_audio:
        wav_bytes = _to_wav_bytes(pcm_audio, sample_rate, num_channels)
        duration_secs = len(pcm_audio) / (sample_rate * num_channels * _SAMPLE_WIDTH_BYTES)
        filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{call_session_id}.wav"
        try:
            recording_url = await asyncio.to_thread(
                recording_backend.upload_recording, filename, wav_bytes
            )
        except Exception:
            logger.exception("Failed to upload call recording for {}", call_session_id)
            # Keep going — still want the transcript/summary saved even if
            # the audio upload failed.

    transcript = transcript[:_MAX_TRANSCRIPT_CHARS]
    summary = await generate_call_summary(transcript)

    row = {
        "Timestamp": now.isoformat(),
        "CallSessionId": call_session_id,
        "CallerPhone": caller_phone,
        "DurationSecs": f"{duration_secs:.1f}",
        "RecordingURL": recording_url,
        "Transcript": transcript,
        "Summary": summary,
    }
    try:
        await asyncio.to_thread(sheets_client.append_row, _RECORDINGS_SHEET, row)
    except Exception:
        logger.exception("Failed to log recording row for {}", call_session_id)
