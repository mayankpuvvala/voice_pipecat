"""Fetches Metered.ca TURN credentials at startup and patches Pipecat's dev
runner to actually use them for the "webrtc" transport.

Why a monkeypatch: pipecat.runner.run's internal WebRTC route setup
hardcodes `SmallWebRTCRequestHandler(esp32_mode=..., host=...)` with no
ice_servers — there's no CLI flag or env var to configure this. Patching
`SmallWebRTCRequestHandler.__init__` to inject a default ice_servers list is
the smallest change that actually reaches it, short of reimplementing the
runner's WebRTC route setup from scratch.

Without a TURN relay, raw WebRTC can't cross NAT between a browser and a
server that aren't on the same network — confirmed on Railway as a
peer-connection timeout. This is what actually fixes that, not just a
STUN-only workaround (STUN helps discover a path when direct connectivity
is possible; it doesn't relay media when it isn't, which is what Railway's
networking required here).
"""

from __future__ import annotations

import httpx
from aiortc import RTCIceServer
from loguru import logger

from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler


def _is_tcp_or_tls(urls: str) -> bool:
    """Keep only turn(s): entries that ride over TCP/TLS, not plain UDP.

    Confirmed via testing: the exact same code and credentials connect fine
    locally but fail on Railway, which strongly points at Railway blocking
    or not routing outbound UDP — a common container-platform restriction.
    STUN and plain `turn:` (UDP) candidates would just hang there rather
    than fail cleanly, so they're dropped rather than left in to fail slow.
    `turns:` is TLS (always TCP-based); `turn:...?transport=tcp` is TURN
    explicitly forced onto TCP.
    """
    return urls.startswith("turns:") or "transport=tcp" in urls


def fetch_turn_credentials(api_key: str, app_name: str) -> list[RTCIceServer]:
    """Metered's TURN REST API — credentials are short-lived, fetched fresh
    on every process start rather than hardcoded."""
    url = f"https://{app_name}.metered.live/api/v1/turn/credentials"
    response = httpx.get(url, params={"apiKey": api_key}, timeout=10.0)
    response.raise_for_status()
    servers = response.json()
    filtered = [s for s in servers if _is_tcp_or_tls(s["urls"])]
    logger.info(
        "Metered returned {} ice server(s), keeping {} TCP/TLS-only "
        "(dropping UDP/STUN entries that fail silently if outbound UDP is "
        "blocked, as it appears to be on Railway)",
        len(servers),
        len(filtered),
    )
    return [
        RTCIceServer(
            urls=s["urls"],
            username=s.get("username"),
            credential=s.get("credential"),
        )
        for s in filtered
    ]


def patch_webrtc_ice_servers(ice_servers: list[RTCIceServer]) -> None:
    original_init = SmallWebRTCRequestHandler.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("ice_servers", ice_servers)
        original_init(self, *args, **kwargs)

    SmallWebRTCRequestHandler.__init__ = patched_init
    logger.info(
        "Patched SmallWebRTCRequestHandler with {} TURN/STUN ice server(s)",
        len(ice_servers),
    )


def setup_turn(api_key: str, app_name: str) -> None:
    """No-ops (with a warning) if not configured, rather than failing
    startup — the webrtc transport still works fine on localhost without
    this, it just won't work once deployed off it."""
    if not api_key or not app_name:
        logger.warning(
            "METERED_API_KEY/METERED_APP_NAME not set — the webrtc transport "
            "has no TURN server configured, and will likely fail to connect "
            "for anyone not on the same network as the server."
        )
        return
    try:
        ice_servers = fetch_turn_credentials(api_key, app_name)
    except Exception:
        logger.exception(
            "Failed to fetch Metered TURN credentials — webrtc transport "
            "will have no TURN server configured"
        )
        return
    patch_webrtc_ice_servers(ice_servers)
