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
    # Matches the voice already picked for the Vapi assistant (voice.voiceId).
    openai_tts_voice: str = os.environ.get("OPENAI_TTS_VOICE", "shimmer")

    sarvam_api_key: str = os.environ.get("SARVAM_API_KEY", "")

    # Live-call tools (logInteraction, check_availability, book_table) and
    # /admin all read/write the sheet directly — same service account n8n's
    # own Sheets credential already uses (it's already been granted Editor on
    # the sheet, so no separate sharing step needed).
    google_service_account_email: str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL", "")
    google_service_account_private_key: str = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", ""
    )
    google_sheet_id: str = os.environ.get("GOOGLE_SHEET_ID", "")

    # Required once this is reachable off localhost — see auth.py.
    admin_username: str = os.environ.get("ADMIN_USERNAME", "")
    admin_password: str = os.environ.get("ADMIN_PASSWORD", "")


settings = Settings()
