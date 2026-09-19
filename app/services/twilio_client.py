"""Twilio REST API access: caller-number lookup, plus outbound SMS used for
real-time alerting (see log_interaction.py and call_health.py).

lookup_caller_number is Twilio-specific because Twilio's WebSocket handshake
carries only the CallSid, not the caller's number (Exotel's includes it
directly). send_sms rides Twilio's API regardless of which provider
carried the call.
"""

from __future__ import annotations

import httpx
from loguru import logger

from app.config.settings import settings


async def send_sms(to: str, body: str) -> bool:
    """Best-effort SMS send. Returns False (logged, never raised) if creds/
    from-number/destination aren't configured or the send fails — a missing
    alert should never take down whatever triggered it, live call included."""
    if not to or not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.warning("send_sms: skipped (no destination number or Twilio creds configured)")
        return False
    if not settings.twilio_sms_from_number:
        logger.warning("send_sms: skipped (TWILIO_SMS_FROM_NUMBER not configured)")
        return False

    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                data={"To": to, "From": settings.twilio_sms_from_number, "Body": body},
            )
            response.raise_for_status()
            return True
    except Exception:
        logger.exception("send_sms: failed to send alert SMS to {}", to)
        return False


_FALLBACK_ICE_SERVERS: list[dict[str, str]] = [{"urls": "stun:stun.l.google.com:19302"}]


def _parse_twilio_ice_servers(data: dict) -> list[dict[str, str]]:
    servers: list[dict[str, str]] = []
    for entry in data.get("ice_servers", []):
        url = entry.get("url") or entry.get("urls")
        if not url:
            continue
        server: dict[str, str] = {"urls": url}
        if entry.get("username"):
            server["username"] = entry["username"]
        if entry.get("credential"):
            server["credential"] = entry["credential"]
        servers.append(server)
    return servers


async def fetch_ice_servers_async() -> list[dict[str, str]]:
    """Twilio's Network Traversal Service: temporary STUN + TURN credentials
    (including TURN-over-TLS on port 443, which gets through firewalls/NATs
    that block plain UDP) — see app/live_client.py, which calls this fresh on
    every /live page load for the browser's own RTCPeerConnection, and
    app/main.py, which calls the sync twin once at startup for the bot's
    server-side aiortc peer.

    Plain STUN alone isn't enough here: pipecat's dev WebRTC runner otherwise
    configures no ice_servers at all server-side, so the bot's aiortc peer
    only ever advertises its own container's private IP — unreachable from
    any browser, on any network, once this runs on Railway (not a client-side
    NAT problem, an "there is no public candidate at all" problem). TURN
    gives both peers a relay reachable over a plain outbound connection
    regardless of what inbound traffic the hosting platform allows.

    Falls back to Google's public STUN-only server on any failure (missing
    creds, Twilio API error) so a hiccup here never blocks /live's page load
    or the bot's startup — it just means relay won't work until this
    succeeds again.
    """
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        return _FALLBACK_ICE_SERVERS
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Tokens.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url, auth=(settings.twilio_account_sid, settings.twilio_auth_token)
            )
            response.raise_for_status()
            return _parse_twilio_ice_servers(response.json()) or _FALLBACK_ICE_SERVERS
    except Exception:
        logger.exception("fetch_ice_servers_async: failed to fetch Twilio TURN credentials")
        return _FALLBACK_ICE_SERVERS


def fetch_ice_servers_sync() -> list[dict[str, str]]:
    """Blocking twin of fetch_ice_servers_async, for app/main.py's one-time
    call at process startup/module-import time, before any event loop exists
    yet — see that function's docstring for what this returns and why."""
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        return _FALLBACK_ICE_SERVERS
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Tokens.json"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                url, auth=(settings.twilio_account_sid, settings.twilio_auth_token)
            )
            response.raise_for_status()
            return _parse_twilio_ice_servers(response.json()) or _FALLBACK_ICE_SERVERS
    except Exception:
        logger.exception("fetch_ice_servers_sync: failed to fetch Twilio TURN credentials")
        return _FALLBACK_ICE_SERVERS


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
