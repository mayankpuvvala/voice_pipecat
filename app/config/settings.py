"""Environment-driven settings, read from `.env` via python-dotenv."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

_RESTAURANT_ID = os.environ.get("RESTAURANT_ID", "spice_route_kitchen")


def _restaurant_env(base: str, default: str = "") -> str:
    """`<base>_<restaurant_id>` if set (lets one .env hold Drive OAuth
    credentials for several restaurants side by side, e.g. for local
    testing), else the plain `<base>` a single-tenant deployment uses."""
    return os.environ.get(f"{base}_{_RESTAURANT_ID}", os.environ.get(base, default))


@dataclass(frozen=True)
class Settings:
    # Which client's Restaurant config (app/config/restaurants/) this
    # deployment/demo runs as — see that package's __init__.py.
    restaurant_id: str = _RESTAURANT_ID

    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    # gpt-5.6-luna over gpt-5-mini (unreliable relative-date resolution) or
    # gpt-5-nano (correct but too slow, 10-29s). See TROUBLESHOOTING.md.
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
    openai_tts_voice: str = os.environ.get("OPENAI_TTS_VOICE", "echo")

    # Conversational LLM only — other call sites use their own OpenAI client.
    # GROQ_ENABLED gates this separately: this account's rate limit can
    # hang turns for 47-58s. See TROUBLESHOOTING.md before enabling.
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    groq_enabled: bool = os.environ.get("GROQ_ENABLED", "false").strip().lower() == "true"
    # pipecat's GroqLLMService default model 404s on this key; qwen/qwen3.8-27b
    # tested fastest and most reliable of the available alternatives.
    groq_model: str = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")

    sarvam_api_key: str = os.environ.get("SARVAM_API_KEY", "")
    rumik_api_key: str = os.environ.get("RUMIK_API_KEY", "")
    twilio_account_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
    # SMS-capable Twilio number for real-time human-escalation texts (see
    # twilio_client.send_sms). Leave unset and escalation SMS just stays off.
    twilio_sms_from_number: str = os.environ.get("TWILIO_SMS_FROM_NUMBER", "")

    google_service_account_email: str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL", "")
    google_service_account_private_key: str = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", ""
    )
    google_sheet_id: str = os.environ.get("GOOGLE_SHEET_ID", "")

    # Off-critical-path transcript analysis (app/pipeline/idle_post_processor.py)
    # that only runs once the bot has gone idle — backfills a whole-call
    # Confidence/Escalated verdict onto the Recordings sheet. On by default;
    # set false to disable without touching the sheet.
    idle_post_processing_enabled: bool = (
        os.environ.get("IDLE_POST_PROCESSING_ENABLED", "true").strip().lower() == "true"
    )

    google_oauth_client_id: str = _restaurant_env("GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str = _restaurant_env("GOOGLE_OAUTH_CLIENT_SECRET")
    google_oauth_refresh_token: str = _restaurant_env("GOOGLE_OAUTH_REFRESH_TOKEN")

    r2_account_id: str = os.environ.get("R2_ACCOUNT_ID", "")
    r2_access_key_id: str = os.environ.get("R2_ACCESS_KEY_ID", "")
    r2_secret_access_key: str = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    r2_bucket_name: str = os.environ.get("R2_BUCKET_NAME", "")
    r2_public_url_base: str = os.environ.get("R2_PUBLIC_URL_BASE", "")

    admin_username: str = os.environ.get("ADMIN_USERNAME", "")
    admin_password: str = os.environ.get("ADMIN_PASSWORD", "")

    # Real cell (E.164) for a real-time dev/ops alert when CallHealthMonitor
    # gives up on a live call — separate from Restaurant.owner_phone's
    # per-caller callback texts. Unset leaves it log-only.
    ops_alert_phone: str = os.environ.get("OPS_ALERT_PHONE", "")

    # Call tracing via Langfuse's OTel endpoint — see app/pipeline/tracing.py.
    # Unset either key and tracing just stays off.
    langfuse_public_key: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.environ.get("LANGFUSE_SECRET_KEY", "")
    langfuse_host: str = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")


settings = Settings()
