"""Builds the final system prompt handed to the LLM.

This is the restaurant's ported Vapi prompt plus the real additions this port
needs on top of it: a language instruction (Vapi pinned its transcriber to
English; this pipeline's Sarvam STT doesn't, so the model needs telling to
actually respond in kind) and guidance on the two logInteraction fields
(`drift`, `callConfidence`) that don't exist in the original Vapi tool schema.
"""

from __future__ import annotations

from app.config.restaurants.spice_route_kitchen import Restaurant

_LANGUAGE_INSTRUCTION = """

# Language
The caller may speak English, Hindi, Telugu, or a code-switched mix of these.
Reply in the same language (or mix) the caller just used — if they switch
languages mid-conversation, switch with them. Text-to-speech for this
prototype phase is an English voice only, so replies will be read aloud with
an English accent regardless of language — that's a known limitation of this
phase, not something to compensate for in what you actually say."""

_LOGGING_QUALITY_INSTRUCTION = """

# Self-assessing each logged topic
logInteraction takes two extra fields beyond the ones already described above:
- drift: true if this topic did NOT actually get solved — you talked about it,
  but the caller's real need wasn't met (you couldn't pin down what they
  wanted, misheard them, or answered something adjacent to the actual
  question). false if you cleanly landed on what they needed, even when the
  honest answer was "the owner will call you back."
- callConfidence: your own honest read on how well you handled this specific
  topic — "high" if you're confident you understood the caller correctly,
  "medium" if there was real ambiguity you had to guess through but you think
  you got it right, "low" if you're genuinely unsure you understood them or
  they seemed unsatisfied or confused by your answer.
Be honest here rather than defaulting to "false" / "high" — these are read by
the owner to spot calls worth listening back to, so they're only useful if
they reflect what actually happened."""


def build_system_prompt(restaurant: Restaurant) -> str:
    return restaurant.system_prompt + _LANGUAGE_INSTRUCTION + _LOGGING_QUALITY_INSTRUCTION
