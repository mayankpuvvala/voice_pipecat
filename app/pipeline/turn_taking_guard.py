"""Stops the bot from speaking a second time before the caller replies.

Confirmed live: pipecat's automatic continuation after a tool call can
stack a second, unanswered question onto the same turn (see
logging_enforcer.py's docstring). This enforces one plain invariant: the
bot may speak at most once per caller turn, where a "turn" is the count
of committed user messages in the shared LLMContext.
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
        """The id of the caller turn currently in progress — the count of
        user messages already in the context (see module docstring)."""
        return sum(1 for m in self._context.messages if m.get("role") == "user")

    def has_spoken_this_turn(self) -> bool:
        """Whether real text has actually reached the caller for the turn in progress.

        Exposed for end_call.py's structural gate — `_answered_turn_id` only
        updates when non-suppressed text is forwarded, so a silent
        tool-calls-only round correctly returns false here.
        """
        return self._answered_turn_id == self._current_turn_id()

    def spoken_text_this_turn(self) -> str:
        """Everything actually forwarded to the caller so far this turn.

        Exposed for end_call.py's reservation-confirmation guard: a model
        can narrate a fabricated "you're all set" without calling
        book_table, so the real spoken text must be checked against the
        real tool result.
        """
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
            # Credit on forward, not at round-end — an interruption can cancel
            # a round before the end frame arrives, but the caller still heard part of it.
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
