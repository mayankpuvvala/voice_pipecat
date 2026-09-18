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
    caller's own words suggest it's actually relevant this call.

    See app/pipeline/dynamic_prompt.py for how `keywords` gets matched
    against the conversation and app/pipeline/prompts.py's
    build_system_prompt for how `text` gets spliced in. Kept as data here
    (not logic) so each restaurant's config owns its own topic split
    without touching the matching code.
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
    # Facts that most calls never touch (full menu, membership perks,
    # seating/ambience, etc.) — left out of system_prompt itself and only
    # spliced in once the caller actually asks about that topic. See this
    # file's TopicFacts docstring and app/pipeline/dynamic_prompt.py.
    topic_facts: tuple[TopicFacts, ...] = ()


# Imported after `Restaurant` is defined above, not before: each client
# module imports `Restaurant` from this package, so this package's own
# class definition has to exist first.
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
