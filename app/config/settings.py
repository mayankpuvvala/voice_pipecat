"""Environment-driven settings, read from `.env` via python-dotenv."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    openai_tts_voice: str = os.environ.get("OPENAI_TTS_VOICE", "shimmer")

    sarvam_api_key: str = os.environ.get("SARVAM_API_KEY", "")
    twilio_account_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "")

    google_service_account_email: str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL", "")
    google_service_account_private_key: str = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", ""
    )
    google_sheet_id: str = os.environ.get("GOOGLE_SHEET_ID", "")

    google_oauth_client_id: str = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    google_oauth_client_secret: str = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    google_oauth_refresh_token: str = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "")

    r2_account_id: str = os.environ.get("R2_ACCOUNT_ID", "")
    r2_access_key_id: str = os.environ.get("R2_ACCESS_KEY_ID", "")
    r2_secret_access_key: str = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    r2_bucket_name: str = os.environ.get("R2_BUCKET_NAME", "")
    r2_public_url_base: str = os.environ.get("R2_PUBLIC_URL_BASE", "")

    admin_username: str = os.environ.get("ADMIN_USERNAME", "")
    admin_password: str = os.environ.get("ADMIN_PASSWORD", "")


settings = Settings()
