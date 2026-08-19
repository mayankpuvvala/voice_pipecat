"""The `logInteraction` tool: logs each resolved/unresolved call topic by
POSTing to the same n8n webhook the existing Vapi bot already uses.

n8n's `1b. Parse Vapi Tool Call` node expects a Vapi-shaped envelope
(`message.toolCalls[0].function.arguments`). Rather than touching that
workflow, this builds the same envelope so `restaurant_reception_workflow.json`
needs zero edits — see n8n/restaurant_reception_workflow.json in this repo.
"""

from __future__ import annotations

import uuid

import httpx
from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

from app.config.settings import settings


async def log_interaction(
    params: FunctionCallParams,
    topic: str,
    resolved: bool,
    caller_name: str = "",
    caller_phone: str = "",
    details: str = "",
    guests_count: str = "",
    drift: bool = False,
    call_confidence: str = "medium",
) -> None:
    """Log what this caller asked about, whether it was answered directly or needs the owner's attention.

    Call this immediately after resolving each topic, not at the end of the call — callers
    often hang up abruptly with no goodbye.

    Args:
        topic: Short label for what they asked about, e.g. "delivery hours", "large party reservation".
        resolved: True if answered directly from the restaurant facts (including taking a
            reservation), false if the owner needs to follow up. This is about *which path*
            you took, not about whether it went well.
        caller_name: Caller's name if given, otherwise empty string.
        caller_phone: Caller's callback phone number if given, otherwise empty string.
        details: One short sentence with specifics the owner needs.
        guests_count: Number of guests, e.g. "3", only when this topic is a reservation.
            Empty string otherwise.
        drift: True if this topic's query did NOT actually get solved — you talked about it
            but the caller's underlying need wasn't met (they seemed confused, you couldn't
            pin down what they wanted, or you answered something adjacent rather than what
            they actually asked). False if you cleanly landed on what they needed, even if
            the answer was "the owner will call you back."
        call_confidence: Your own honest read on how well this topic went: "high" if you're
            confident you understood the caller and handled it correctly, "medium" if there
            was some ambiguity (unclear speech, a guess on intent) but you think you got it
            right, "low" if you're genuinely unsure you understood them correctly or the
            caller seemed unsatisfied/confused by your response.
    """
    envelope = {
        "message": {
            "toolCalls": [
                {
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "function": {
                        "name": "logInteraction",
                        "arguments": {
                            "callerName": caller_name,
                            "callerPhone": caller_phone,
                            "topic": topic,
                            "resolved": resolved,
                            "details": details,
                            "guestsCount": guests_count,
                            "drift": drift,
                            "callConfidence": call_confidence,
                        },
                    },
                }
            ]
        }
    }

    if not settings.n8n_webhook_url:
        logger.warning("N8N_WEBHOOK_URL not set — would have logged: {}", envelope)
        await params.result_callback({"logged": False, "reason": "no webhook configured"})
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.n8n_webhook_url, json=envelope)
            response.raise_for_status()
    except Exception:
        logger.exception("Failed to log interaction to n8n")
        await params.result_callback({"logged": False})
        return

    await params.result_callback({"logged": True})
