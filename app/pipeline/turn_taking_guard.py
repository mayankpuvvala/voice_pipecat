"""Stops the bot from speaking a second time before the caller replies.

Confirmed live from a real call: the caller asked for a reservation, the
bot asked "What name should I put the reservation under?", and — with no
caller input in between — immediately also asked "What date and time
would you like for the reservation?". The caller never got a chance to
answer the first question; both landed back to back, so they answered
both at once. Traced via the server log: round 1 spoke the name question
(no tool call, so LogInteractionEnforcer nudged it); the nudge's own
silent followup correctly called logInteraction and said nothing; but
pipecat's automatic continuation *after that tool call* is a real, new,
un-swallowed round by design (see logging_enforcer.py's docstring — a
nudge-originated chain deliberately only swallows its own immediate
followup, never further, specifically so a legitimate next question isn't
lost as dead air). In this case what came out of that continuation wasn't
a legitimate next question, though — it was the bot just moving on to its
next planned question without waiting for an answer to the first.

This app's own prompt already asks for exactly one utterance per caller
turn (_BREVITY_INSTRUCTION: "Ask ONE question at a time... ask, hear the
answer, ask the next thing"), so there's no case where a second spoken
utterance before new caller input is actually correct for this app —
enforcing it structurally costs nothing legitimate. Tracked via the
LLMContext directly (not frames flowing through this processor, since
user input travels through a different branch of the pipeline that never
reaches this position): count the "user" messages in context each time a
round is about to speak. If that count hasn't grown since the last time
the bot actually spoke, this round is a second utterance for the same
caller turn — drop its text. A tool-call-only round that stays silent
(e.g. a lookup with nothing to say yet) never counts as "spoke," so the
turn's real first utterance — whenever it arrives — still goes through
normally; this only blocks a *second* one.

Sits after LogInteractionEnforcer: needs to see whatever that processor
already decided to let through (including the never-swallowed
automatic-continuation case this exists to catch), not the raw LLM stream.
"""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class OneUtterancePerTurnGuard(FrameProcessor):
    def __init__(self, context: LLMContext, **kwargs) -> None:
        super().__init__(**kwargs)
        self._context = context
        self._last_spoken_at_user_count = -1
        self._suppress_this_round = False
        self._credited_this_round = False
        self._dropped_text_parts: list[str] = []

    def _user_message_count(self) -> int:
        return sum(1 for m in self._context.messages if m.get("role") == "user")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._dropped_text_parts = []
            self._credited_this_round = False
            self._suppress_this_round = self._user_message_count() == self._last_spoken_at_user_count
        elif isinstance(frame, LLMTextFrame):
            if self._suppress_this_round:
                self._dropped_text_parts.append(frame.text)
                return
            # Credit the moment real text is actually forwarded, not at
            # round-end: an interruption can cancel a round before its
            # LLMFullResponseEndFrame ever reaches this processor, and a
            # credit-on-end approach would then treat a retry of the same
            # question as a still-unspoken first utterance. Crediting here
            # means even a partially-spoken, later-interrupted utterance
            # still counts -- correctly, since the caller did hear part of
            # an answer for this turn.
            if not self._credited_this_round and frame.text.strip():
                self._last_spoken_at_user_count = self._user_message_count()
                self._credited_this_round = True
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._suppress_this_round:
                held = "".join(self._dropped_text_parts)
                self._dropped_text_parts = []
                if held.strip():
                    logger.info(
                        "OneUtterancePerTurnGuard: dropped a second utterance "
                        "for the same caller turn: {!r}",
                        held,
                    )
            self._suppress_this_round = False

        await self.push_frame(frame, direction)
