"""Owner WhatsApp alerts via CallMeBot — free, text-only, own-number only.

Chosen over Meta's WhatsApp Cloud API (2026-10-20 research) because these
alerts only ever go to the restaurant owner's own phone, which is exactly
what CallMeBot's free tier allows, and it needs no Meta business
verification or approved message templates. Cloud API is the upgrade path
if alerts ever need to reach customers (≈₹0.115 per utility message).

One-time setup per owner phone: from that phone, send the WhatsApp message
"I allow callmebot to send me messages" to CallMeBot's number (see
https://www.callmebot.com/blog/free-api-whatsapp-messages/); the reply
contains an API key → WHATSAPP_CALLMEBOT_APIKEY (or
WHATSAPP_CALLMEBOT_APIKEY_<restaurant_id>). The key is tied to that phone.

Text only — call audio goes out as the recording's shareable link (see
app/pipeline/recording.py), not as an attachment.
"""

from __future__ import annotations

import httpx
from loguru import logger

from app.config.settings import settings

_CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
# CallMeBot rejects long messages; keep alerts well under its limit.
_MAX_CHARS = 900


async def send_whatsapp(to: str, body: str) -> bool:
    """Best-effort WhatsApp alert. Returns False (logged, never raised) when
    not configured or the send fails — same contract as twilio_client.send_sms,
    a missing alert must never take down a live call."""
    if not to or not settings.whatsapp_callmebot_apikey:
        logger.debug("send_whatsapp: skipped (no destination number or API key configured)")
        return False

    params = {
        "phone": to,
        "text": body[:_MAX_CHARS],
        "apikey": settings.whatsapp_callmebot_apikey,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(_CALLMEBOT_URL, params=params)
            response.raise_for_status()
            # CallMeBot answers 200 with an HTML page even for some failures
            # (e.g. a wrong key) — treat an explicit error message as a failure.
            if "APIKey is invalid" in response.text or "ERROR" in response.text.upper()[:200]:
                logger.warning("send_whatsapp: CallMeBot rejected the message: {}", response.text[:200])
                return False
            return True
    except Exception as e:
        # No traceback/message: httpx errors embed the request URL, which
        # carries the CallMeBot apikey as a query parameter.
        logger.error("send_whatsapp: failed to send alert ({})", type(e).__name__)
        return False
