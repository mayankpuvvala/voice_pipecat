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

    # Same webhook the existing Vapi/n8n workflow already uses.
    n8n_webhook_url: str = os.environ.get("N8N_WEBHOOK_URL", "")


settings = Settings()
