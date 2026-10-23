"""Live call transfer via the telephony provider's Transfer API — the
provider-side half of the `transfer_call` tool (app/tools/transfer_call.py).

Both providers this supports expose the same Plivo-shaped endpoint: POST
`.../Account/{auth_id}/Call/{call_uuid}/` with `legs=aleg` and an
`aleg_url` the provider fetches new XML from. That XML (built here, served
by app/transfer_routes.py) is a <Dial> to the restaurant's transfer number,
which replaces our <Stream> on the caller's leg. Docs relied on:
- Plivo: https://www.plivo.com/docs/voice/api/call/transfer-a-call
  (HTTP Basic auth with auth_id/auth_token, JSON body).
- Vobiz: https://www.vobiz.ai/docs/call/transfer-call
  (X-Auth-ID/X-Auth-Token headers, JSON body, 202 Accepted on success).
Plivo's page doesn't state its success status code, so any 2xx counts.

Never raises: every failure comes back as a TransferResult with a reason
the tool can hand straight to the LLM.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

import httpx
from loguru import logger

from app.config.settings import settings

# Path from the provider's own docs, appended to each base below.
_API_BASES = {
    "plivo": "https://api.plivo.com/v1",
    "vobiz": "https://api.vobiz.ai/api/v1",
}
# A stuck Transfer request is dead air on a live call — fail fast and let
# the bot apologise instead.
_REQUEST_TIMEOUT_SECS = 8.0
# How long the transfer target rings before the caller hears the
# "no one picked up" line (see build_dial_status_xml). Vobiz's own default
# is 120s, far longer than any caller will wait in silence.
DIAL_TIMEOUT_SECS = 30

# E.164-ish: optional "+", 8-15 digits. Plivo sends numbers without the "+",
# so both forms are accepted.
_PHONE_NUMBER = re.compile(r"^\+?\d{8,15}$")

NO_ANSWER_MESSAGE = (
    "Sorry, no one could take your call right now. The owner has your "
    "details and will call you back soon. Goodbye."
)


@dataclass
class TransferLine:
    """Everything transfer_call needs about this specific call's line.

    Built once per call in app/main.py's run_bot, only when live transfer
    is actually possible (see `transfer_available`), and handed to the tool
    via app_resources["transfer_line"].

    Attributes:
        provider: "plivo", "vobiz", or "simulated" (eval runs — never dials).
        target_number: Who the transfer dials (see transfer_target()).
        call_uuid: The provider's call UUID (the <Stream> start event's callId).
        aleg_url: Absolute URL of our /transfer-dial XML route.
        set_auto_hang_up: Toggles the transport's own hang-up-on-pipeline-end
            (real Plivo only — see app/main.py's _create_plivo_transport).
            None when the transport has none to toggle.
    """

    provider: str
    target_number: str
    call_uuid: str = ""
    aleg_url: str = ""
    set_auto_hang_up: Callable[[bool], None] | None = None


@dataclass(frozen=True)
class TransferResult:
    ok: bool
    reason: str = ""


def api_base(provider: str) -> str:
    return _API_BASES[provider]


def transfer_endpoint(provider: str, auth_id: str, call_uuid: str) -> str:
    # Trailing slash is part of both providers' documented path; Vobiz
    # notes a wrong-case path 401s, so this is kept verbatim.
    return f"{api_base(provider)}/Account/{auth_id}/Call/{call_uuid}/"


def _credentials(provider: str) -> tuple[str, str]:
    if provider == "plivo":
        return settings.plivo_auth_id, settings.plivo_auth_token
    if provider == "vobiz":
        return settings.vobiz_auth_id, settings.vobiz_auth_token
    return "", ""


def credentials_configured(provider: str) -> bool:
    auth_id, auth_token = _credentials(provider)
    return bool(auth_id and auth_token)


def is_phone_number(value: str) -> bool:
    return bool(_PHONE_NUMBER.match(value or ""))


async def request_transfer(
    provider: str,
    call_uuid: str,
    aleg_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> TransferResult:
    """Ask the provider to move the caller's leg onto `aleg_url`'s XML.

    `client` is only for tests (an httpx.MockTransport-backed client); a
    real call gets a short-lived client of its own.
    """
    if provider not in _API_BASES:
        return TransferResult(False, f"live transfer isn't supported on {provider!r} calls")
    auth_id, auth_token = _credentials(provider)
    if not (auth_id and auth_token):
        return TransferResult(False, f"{provider} REST credentials aren't configured")
    if not call_uuid:
        return TransferResult(False, "this call's provider call UUID is unknown")

    url = transfer_endpoint(provider, auth_id, call_uuid)
    body = {"legs": "aleg", "aleg_url": aleg_url, "aleg_method": "POST"}
    request_kwargs: dict = {"json": body}
    if provider == "plivo":
        request_kwargs["auth"] = (auth_id, auth_token)
    else:
        request_kwargs["headers"] = {"X-Auth-ID": auth_id, "X-Auth-Token": auth_token}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECS)
    try:
        response = await client.post(url, **request_kwargs)
    except Exception as e:
        logger.warning("Transfer request to {} failed for call {}: {!r}", provider, call_uuid, e)
        return TransferResult(False, f"couldn't reach {provider}'s transfer API ({type(e).__name__})")
    finally:
        if owns_client:
            await client.aclose()

    if 200 <= response.status_code < 300:
        logger.info(
            "Transfer accepted by {} for call {} (status={}, body={})",
            provider,
            call_uuid,
            response.status_code,
            response.text[:300],
        )
        return TransferResult(True)

    logger.warning(
        "Transfer rejected by {} for call {}: status={} body={}",
        provider,
        call_uuid,
        response.status_code,
        response.text[:300],
    )
    return TransferResult(False, f"{provider} rejected the transfer (HTTP {response.status_code})")


def set_plivo_auto_hang_up(serializer, enabled: bool) -> None:
    """Toggle PlivoFrameSerializer's hang-up-on-EndFrame/CancelFrame.

    transfer_call needs it OFF once the caller's leg has been transferred:
    the call UUID stays the same, so the serializer's REST DELETE on
    pipeline teardown would hang up the caller mid-transfer. pipecat has no
    public toggle; `_hangup_attempted` is the flag serialize() itself uses
    to only hang up once (pipecat 1.7.0 serializers/plivo.py) —
    tests/test_transfer_call.py pins this so an upgrade that renames it
    fails loudly instead of silently hanging up transferred callers.
    """
    serializer._hangup_attempted = not enabled


def build_dial_xml(number: str, *, caller_id: str = "", action_url: str = "") -> str:
    """The <Dial> both providers fetch from aleg_url once a transfer lands.

    `caller_id` should be a number this account owns (the DID the caller
    dialed) — Vobiz warns the B-leg may fail otherwise
    (https://www.vobiz.ai/docs/xml/dial). `action_url` receives DialStatus
    once the Dial ends; see build_dial_status_xml.
    """
    attrs = [f"timeout={quoteattr(str(DIAL_TIMEOUT_SECS))}"]
    if caller_id and is_phone_number(caller_id):
        attrs.append(f"callerId={quoteattr(caller_id)}")
    if action_url:
        attrs.append(f"action={quoteattr(action_url)}")
        attrs.append('method="POST"')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Dial {' '.join(attrs)}><Number>{escape(number)}</Number></Dial>"
        "</Response>"
    )


def build_dial_status_xml(dial_status: str) -> str:
    """What runs after the transfer's <Dial> ends.

    Completed means the caller actually talked to someone — just end. Any
    other status (busy, no-answer, timeout, failed...) gets one spoken line
    so the caller isn't left in silence. The escalation SMS/WhatsApp already
    went out before the transfer, so "the owner will call you back" is true.
    Ending the XML ends the call on both providers (no <Hangup> needed —
    Vobiz doesn't document one).
    """
    if (dial_status or "").lower() == "completed":
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Speak>{escape(NO_ANSWER_MESSAGE)}</Speak></Response>"
    )
