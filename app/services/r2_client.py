"""Cloudflare R2 uploads for call recordings.

Replaces an earlier Google Drive-based approach that turned out to be a dead
end: Google removed storage quota for service accounts in 2021, so a service
account can never own a file in a regular (non-Shared-Drive) Google Drive no
matter what permissions a folder grants it — confirmed live, via
`storageQuotaExceeded` on an actual upload attempt, not just docs. R2 is
plain S3-compatible object storage with no such per-identity quota model —
an API token scoped to the bucket is all that's needed.

`boto3` is a blocking/synchronous client, not asyncio — `upload_recording`
must be awaited via `asyncio.to_thread(...)` from async call sites (see
app/pipeline/recording.py).
"""

from __future__ import annotations

import boto3
from botocore.client import Config

from app.config.settings import settings


def _client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_recording(filename: str, wav_bytes: bytes) -> str:
    """Upload a WAV recording to the configured bucket.

    Returns the public URL if R2_PUBLIC_URL_BASE is configured, otherwise
    just the bare object key (filename) as a reference — still valid for
    cross-checking against the bucket directly, just not clickable.
    """
    client = _client()
    client.put_object(
        Bucket=settings.r2_bucket_name,
        Key=filename,
        Body=wav_bytes,
        ContentType="audio/wav",
    )
    if settings.r2_public_url_base:
        return f"{settings.r2_public_url_base.rstrip('/')}/{filename}"
    return filename
