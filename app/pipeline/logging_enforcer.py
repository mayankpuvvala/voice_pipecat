"""Forces a logInteraction check after any assistant turn that ends without
calling any tool, and swallows the spoken text of any round that pipecat's
own function-calling machinery auto-generates afterward — whether that
round was triggered by this enforcer's own nudge or by a normal, fully
compliant reply+tool-call turn.

The `_awaiting_followup`/`_chain_extends` pair below is this file's
tracking of a single *response transaction*: one caller-facing reply plus
however many silent tool-call-and-continuation rounds pipecat generates
on its own to produce it (e.g. reply+tool-call, then the automatic
continuation that reacts to the tool result). Everything inside one
transaction is swallowed except the round(s) that actually carry the
caller-facing reply; `_chain_extends` is what keeps swallowing through a
chain of several such rounds instead of releasing after just one.

This tracking is best-effort, not authoritative: pipecat's automatic
continuation frame looks identical whether it's genuinely still part of
the current transaction or the model has moved on to unprompted new
content, so a nudge-originated transaction deliberately stops swallowing
after its own immediate followup (see `_chain_extends = False` below) so
a real next question isn't lost as dead air -- which is also the gap that
`turn_taking_guard.py`'s `OneUtterancePerTurnGuard` exists to backstop
with a transaction-agnostic invariant: never more than one caller-facing
utterance per caller turn, however many transactions produced it.
"""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import (
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMRunFrame,
    LLMTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


_ENDING_SIGNAL_PHRASES = (
    "goodbye",
    "have a great day",
    "talk soon",
    "hang up",
    "call 112",  # the emergency rule's own wording (see restaurant configs) --
    "dial 112",  # keep in sync if that instruction's phrasing ever changes
)


def _sounds_like_ending(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _ENDING_SIGNAL_PHRASES)


def _followup_prompt(reply_text: str, *, also_end_call: bool) -> str:
    quoted = reply_text.strip() or "(no spoken content — a tool-call-only turn)"
    prompt = (
        f'Your last reply was: "{quoted}" — and you did not call logInteraction '
        "for it. If that reply resolved or addressed anything the caller asked "
        "— including a short factual answer, not just reservations — call "
        "logInteraction for it right now. If nothing was actually resolved yet "
        "(you're still gathering details, or you just asked a clarifying "
        "question), call nothing and don't say anything further; stay silent."
    )
    if also_end_call:
        prompt += (
            " That reply also already said goodbye or told the caller to hang "
            "up (e.g. for an emergency) — the conversation is over, so call "
            "end_call now too, in this same silent turn, alongside logInteraction."
        )
    return prompt


class WorkerHandle:
    """Holds the PipelineWorker once it exists.

    The processor has to be built before `Pipeline(...)`, which is itself
    built before `PipelineWorker(...)` — so it can't take the worker directly
    in its constructor. Set `worker` on this once, right after the worker is
    actually created.
    """

    def __init__(self) -> None:
        self.worker = None


class LogInteractionEnforcer(FrameProcessor):
    def __init__(self, context: LLMContext, worker_handle: WorkerHandle, **kwargs) -> None:
        super().__init__(**kwargs)
        self._context = context
        self._worker_handle = worker_handle
        self._tool_call_seen = False
        self._reply_text_parts: list[str] = []
        self._awaiting_followup = False
        self._chain_extends = False

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            self._reply_text_parts.append(frame.text)
            if self._awaiting_followup:
                return
        elif isinstance(frame, FunctionCallsStartedFrame):
            self._tool_call_seen = True
        elif isinstance(frame, LLMFullResponseEndFrame):
            reply_text = "".join(self._reply_text_parts)
            self._reply_text_parts = []

            if self._awaiting_followup:
                logger.info(
                    "LogInteractionEnforcer: swallowed round ended (tool_call={}, text={!r})",
                    self._tool_call_seen,
                    reply_text,
                )
                if not self._tool_call_seen or not self._chain_extends:
                    self._awaiting_followup = False
            elif not self._tool_call_seen and reply_text.strip():
                ending = _sounds_like_ending(reply_text)
                logger.info(
                    "LogInteractionEnforcer: no tool call this turn, nudging "
                    "(reply={!r}, also_end_call={})",
                    reply_text,
                    ending,
                )
                self._context.add_message(
                    {
                        "role": "developer",
                        "content": _followup_prompt(reply_text, also_end_call=ending),
                    }
                )
                if self._worker_handle.worker is not None:
                    self._awaiting_followup = True
                    self._chain_extends = False
                    await self._worker_handle.worker.queue_frames([LLMRunFrame()])
            elif reply_text.strip():
                logger.info(
                    "LogInteractionEnforcer: round replied and called a tool — "
                    "swallowing the automatic post-tool-call continuation"
                )
                self._awaiting_followup = True
                self._chain_extends = True
            self._tool_call_seen = False

        await self.push_frame(frame, direction)
