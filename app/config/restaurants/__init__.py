"""The Restaurant config shape, plus which client's config is active.

`Restaurant` lives here (not inside any one client's module) because
multiple client configs need to import it. Which one is actually used by
this deployment/demo is picked via the RESTAURANT_ID env var (see
app.config.settings) — add a new client's Restaurant instance to
_RESTAURANTS below and set RESTAURANT_ID to its key to run as them.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from app.config.settings import settings


@dataclass(frozen=True)
class TopicFacts:
    """One optional block of system-prompt facts, included only when the
    caller's own words suggest it's relevant. See dynamic_prompt.py for
    keyword matching and prompts.py's build_system_prompt for splicing.
    """

    keywords: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Restaurant:
    name: str
    bot_name: str
    first_message: str
    end_call_message: str
    system_prompt: str
    timezone: str
    hours: dict[int, list[tuple[str, str]]]
    # Facts most calls never touch — spliced in only once the caller asks
    # about that topic. See TopicFacts docstring and dynamic_prompt.py.
    topic_facts: tuple[TopicFacts, ...] = ()
    # Real cell number (E.164) for real-time human-escalation SMS — see
    # log_interaction.write_interaction_row. Never guess this; leave blank
    # until the client confirms their real number.
    owner_phone: str = ""

    # Which STT/TTS vendor this restaurant's calls use — the one place to
    # switch either, per client. See app/services/stt_factory.py and
    # tts_factory.py for what each value builds and STT_PROVIDERS/
    # TTS_PROVIDERS there for the valid values; both raise at startup on an
    # unrecognized one, so a typo here fails loudly instead of on the first
    # call.
    stt_provider: str = "sarvam"
    tts_provider: str = "rumik"


# Imported after `Restaurant` is defined — each client module imports it from here.
from app.config.restaurants.spice_route_kitchen import SPICE_ROUTE_KITCHEN  # noqa: E402
from app.config.restaurants.zero40 import ZERO40_BREWING  # noqa: E402

_RESTAURANTS: dict[str, Restaurant] = {
    "spice_route_kitchen": SPICE_ROUTE_KITCHEN,
    "zero40": ZERO40_BREWING,
}

if settings.restaurant_id not in _RESTAURANTS:
    logger.warning(
        "RESTAURANT_ID '{}' doesn't match any configured restaurant ({}) — "
        "falling back to spice_route_kitchen. If this deployment is meant to "
        "run as a different client, calls will use the wrong facts/hours "
        "until this is fixed.",
        settings.restaurant_id,
        ", ".join(_RESTAURANTS),
    )

ACTIVE_RESTAURANT = _RESTAURANTS.get(settings.restaurant_id, SPICE_ROUTE_KITCHEN)
