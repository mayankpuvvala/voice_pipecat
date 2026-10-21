"""XML routes a live transfer lands on (Plivo and Vobiz alike — both speak
the same Plivo-compatible XML).

transfer_call's Transfer API request (app/services/call_transfer.py) points
the caller's leg at POST /transfer-dial, which dials the restaurant's
transfer number. When that <Dial> ends, the provider posts DialStatus to
/transfer-dial-status, which either just ends the call (they spoke to
someone) or speaks a short "no one picked up" line first.

The number dialed always comes from the restaurant config, never from the
request — this route must not become a way to make this account dial an
arbitrary number. Only callerId is taken from the query string, and only
if it looks like a phone number.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from fastapi import FastAPI, Request
from fastapi.responses import Response
from loguru import logger

from app.config.restaurants import Restaurant, transfer_target
from app.services.call_transfer import (
    NO_ANSWER_MESSAGE,
    build_dial_status_xml,
    build_dial_xml,
    is_phone_number,
)


def register_transfer_routes(app: FastAPI, restaurant: Restaurant) -> None:
    @app.api_route("/transfer-dial", methods=["GET", "POST"], include_in_schema=False)
    async def transfer_dial(request: Request) -> Response:
        number = transfer_target(restaurant)
        if not number:
            # transfer_call never requests a transfer without a target, so
            # this only fires on a config change mid-call — still don't
            # leave the caller in silence.
            logger.error("/transfer-dial hit but {} has no transfer number", restaurant.name)
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<Response><Speak>{escape(NO_ANSWER_MESSAGE)}</Speak></Response>"
            )
            return Response(content=xml, media_type="application/xml")

        caller_id = request.query_params.get("caller_id", "")
        if caller_id and not is_phone_number(caller_id):
            caller_id = ""
        action_url = f"https://{request.headers.get('host')}/transfer-dial-status"
        logger.info("Serving transfer Dial XML (callerId={!r})", caller_id)
        return Response(
            content=build_dial_xml(number, caller_id=caller_id, action_url=action_url),
            media_type="application/xml",
        )

    @app.post("/transfer-dial-status", include_in_schema=False)
    async def transfer_dial_status(request: Request) -> Response:
        form = await request.form()
        dial_status = str(form.get("DialStatus", "") or "")
        logger.info(
            "Transfer Dial ended: call_uuid={} DialStatus={} DialHangupCause={}",
            form.get("CallUUID"),
            dial_status,
            form.get("DialHangupCause"),
        )
        return Response(content=build_dial_status_xml(dial_status), media_type="application/xml")
