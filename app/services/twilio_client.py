"""Twilio REST API lookups — currently just resolving the caller's number.

Twilio's Media Streams WebSocket handshake carries only the CallSid, not the
caller's number. Exotel's handshake includes the real From/To directly, so
this is Twilio-specific — a REST call to the Call resource using the CallSid
we already get for free, authenticated with the same account SID/token
pipecat's own runner already requires for auto-hang-up.
"""

from __future__ import annotations

import httpx
from loguru import logger

from app.config.settings import settings


async def lookup_caller_number(call_sid: str) -> str:
    """Return the caller's number for a Twilio call, or "" if unavailable."""
    if not call_sid or not settings.twilio_account_sid or not settings.twilio_auth_token:
        return ""

    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Calls/{call_sid}.json"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                url, auth=(settings.twilio_account_sid, settings.twilio_auth_token)
            )
            response.raise_for_status()
            return response.json().get("from") or ""
    except Exception:
        logger.exception("Failed to look up Twilio caller number for call {}", call_sid)
        return ""
