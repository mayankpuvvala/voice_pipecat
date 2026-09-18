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
    # gpt-5.6-luna, not gpt-4o-mini or gpt-5.4-nano: gpt-4o-mini got relative
    # dates ("this Saturday") wrong ~80% of the time in testing, which
    # check_availability/book_table trust as-is -- gpt-5.4-nano fixed that.
    # gpt-5.6-luna (tested 2026-09-19, see TROUBLESHOOTING.md's
    # "Conversational LLM model choice" section) matched gpt-5.4-nano 6/6 on
    # both that date-resolution test AND the log_interaction reliability
    # test, at statistically identical latency (~1.18s median) and slightly
    # cheaper output tokens. Needs `extra={"reasoning_effort": "none"}` on
    # OpenAILLMService.Settings (see app/main.py's _build_llm) -- it 400s on
    # /v1/chat/completions with function tools otherwise. gpt-5-nano was
    # also tested and rejected: 6/6 correct but 10-29s latency (reasoning
    # model, burns time on hidden reasoning tokens) -- disqualifying for a
    # live call despite being the cheapest option by price alone.
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
    openai_tts_voice: str = os.environ.get("OPENAI_TTS_VOICE", "echo")

    # Conversational LLM only — see app/main.py's _build_llm. Kept separate
    # from openai_api_key/openai_model above, which stay OpenAI-only: both
    # LogInteractionEnforcer's backfill call and rumik_tts.py's TTS fallback
    # go straight at OpenAI's API via their own AsyncOpenAI client and would
    # break if those fields were repointed at Groq instead.
    #
    # GROQ_ENABLED gates this independently of GROQ_API_KEY being set (see
    # TROUBLESHOOTING.md, "Groq LLM" section): this account's current tier
    # rate-limits at 8000 tokens/minute, and this bot's ~19K-char system
    # prompt alone is ~4-5K tokens/turn — measured real turns taking 47-58s
    # each once that budget was used up (the OpenAI SDK client retries 429s
    # with silent backoff, so it doesn't fail fast, it just hangs). Do not
    # flip this on without first upgrading the Groq billing tier AND
    # re-running the latency check — see the smoke-test approach in this
    # session's history / ask for it again.
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    groq_enabled: bool = os.environ.get("GROQ_ENABLED", "false").strip().lower() == "true"
    # pipecat's GroqLLMService defaults to "llama-3.3-70b-versatile", which
    # 404s as of 2026-09-18 — not in this key's /v1/models list any more.
    # qwen/qwen3.8-27b tested fastest and most reliable of the available
    # models (correct tool-calling/date-math, no wasted reasoning-token
    # overhead) once rate-limit throttling isn't in the way.
    groq_model: str = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")

    sarvam_api_key: str = os.environ.get("SARVAM_API_KEY", "")
    rumik_api_key: str = os.environ.get("RUMIK_API_KEY", "")
    twilio_account_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "")

    google_service_account_email: str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL", "")
    google_service_account_private_key: str = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", ""
    )
    google_sheet_id: str = os.environ.get("GOOGLE_SHEET_ID", "")

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

    # Call tracing (STT/LLM/TTS latency, turn/interruption spans) via
    # Langfuse's OTel endpoint — see app/pipeline/tracing.py. Unset either
    # key and tracing just stays off; region defaults to Langfuse's EU host.
    langfuse_public_key: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.environ.get("LANGFUSE_SECRET_KEY", "")
    langfuse_host: str = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")


settings = Settings()
