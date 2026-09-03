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

This guard doesn't try to track *why* a round exists (nudge-originated vs.
a normal reply+tool-call chain) the way LogInteractionEnforcer's
`_awaiting_followup`/`_chain_extends` do -- that tracking is inherently
best-effort, since pipecat's own automatic post-tool-call continuation
looks identical whether it's a legitimate next sentence or the model just
continuing unprompted. This guard is the backstop that doesn't need to
know which case it is: it enforces one plain invariant regardless --

    the bot may speak at most once per caller turn.

A "caller turn" here is identified by `user_turn_id`, a count of committed
user messages in the shared LLMContext. That count only advances when the
user aggregator has actually finalized a caller utterance into context --
it does not advance for VAD noise/false triggers, tool calls, tool
results, or any of pipecat's internal LLM continuations, which is what
makes it a valid turn id rather than just an incidental proxy.
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
        self._answered_turn_id = -1
        self._suppress_this_round = False
        self._credited_this_round = False
        self._dropped_text_parts: list[str] = []

    def _current_turn_id(self) -> int:
        """The id of the caller turn currently in progress.

        Derived (not separately stored) from how many user messages the
        context already holds -- see the module docstring for why that
        count is a valid turn id rather than just an incidental proxy.
        """
        return sum(1 for m in self._context.messages if m.get("role") == "user")

    def has_spoken_this_turn(self) -> bool:
        """Whether real text has actually reached the caller for the turn in progress.

        Exposed for end_call.py's own structural gate: `_answered_turn_id` is
        only set in the LLMTextFrame branch above, the moment non-suppressed
        text is actually forwarded -- so this is true only once the caller
        has genuinely heard something this turn, not merely because a round
        with a tool call happened to run. A silent tool-calls-only round
        (confirmed live: book_table -> logInteraction -> end_call with zero
        spoken text in between) leaves `_answered_turn_id` behind, so this
        correctly returns false for exactly that case.
        """
        return self._answered_turn_id == self._current_turn_id()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._dropped_text_parts = []
            self._credited_this_round = False
            self._suppress_this_round = self._current_turn_id() == self._answered_turn_id
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
                self._answered_turn_id = self._current_turn_id()
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
