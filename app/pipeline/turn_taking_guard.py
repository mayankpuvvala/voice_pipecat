"""Stops the bot from speaking a second time before the caller replies.

Confirmed live: the bot asked "What name should I put the reservation
under?", then -- with no caller input in between -- immediately also asked
"What date and time?", so the caller answered both at once. Root cause:
LogInteractionEnforcer's nudge correctly swallowed its own immediate
followup, but pipecat's automatic continuation *after that tool call* is a
new, un-swallowed round by design (see logging_enforcer.py's docstring --
deliberately so a legitimate next question isn't lost as dead air). Here
the continuation wasn't a legitimate next question, just the bot moving on
without waiting for an answer.

This guard doesn't track *why* a round exists (nudge-originated vs. a
normal reply+tool-call chain) the way LogInteractionEnforcer does -- that's
inherently best-effort, since pipecat's automatic continuation looks
identical either way. Instead it enforces one plain, transaction-agnostic
invariant:

    the bot may speak at most once per caller turn.

A "caller turn" is `user_turn_id`, a count of committed user messages in
the shared LLMContext -- it only advances when the user aggregator
finalizes a real caller utterance, not for VAD noise, tool calls/results,
or pipecat's internal continuations.
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
        self._spoken_text_turn_id = -1
        self._spoken_text_parts: list[str] = []

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

    def spoken_text_this_turn(self) -> str:
        """Everything actually forwarded to the caller so far, across every
        round of the current turn's response transaction.

        Exposed for end_call.py's reservation-confirmation guard: confirmed
        live that a model refused by `has_spoken_this_turn()` above can
        respond by narrating a fabricated booking confirmation ("You're all
        set, Vikram!... table for two...") instead of actually calling
        book_table -- a caller could be told a table is booked when it never
        was. Checking the real spoken text against the real tool result is
        the only way to catch that; a boolean "something was said" isn't
        enough."""
        if self._spoken_text_turn_id != self._current_turn_id():
            return ""
        return "".join(self._spoken_text_parts)

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
            current_turn_id = self._current_turn_id()
            if self._spoken_text_turn_id != current_turn_id:
                self._spoken_text_turn_id = current_turn_id
                self._spoken_text_parts = []
            self._spoken_text_parts.append(frame.text)
            # Credit on forward, not at round-end: an interruption can cancel
            # a round before LLMFullResponseEndFrame arrives, so crediting at
            # end would wrongly treat an interrupted utterance as unspoken --
            # but the caller did hear part of it, so it should still count.
            if not self._credited_this_round and frame.text.strip():
                self._answered_turn_id = current_turn_id
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
