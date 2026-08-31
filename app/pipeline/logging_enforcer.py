"""Forces a logInteraction check after any assistant turn that ends without
calling any tool.

Prompt-only instructions ("call logInteraction in the same turn") reliably
worked for reservation-style replies but not for short factual answers,
confirmed via isolated eval-transport testing: the model would narrate "I'll
log that" and then simply not call the tool for a plain hours/parking
question, even after the instruction was made explicit and absolute. This
closes the gap structurally instead of with more prompt wording.

Sits right after `llm` in the pipeline (not after assistant_aggregator) —
confirmed via testing that FunctionCallInProgressFrame/LLMFullResponseEndFrame
don't reliably propagate past tts/assistant_aggregator (those consume the
frames for their own aggregation rather than forwarding them). Because of
that position, this can't rely on the assistant's reply already being
recorded in context by assistant_aggregator when it reacts — an earlier
version tried that and produced a developer message referencing a reply the
model hadn't technically "seen" in context order yet, which reliably failed.
Instead it captures the reply's own text as it streams through and quotes it
directly in the nudge, so the nudge is self-contained regardless of when (or
whether, relative to this) assistant_aggregator's own copy lands in context.
"""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import (
    FunctionCallInProgressFrame,
    LLMFullResponseEndFrame,
    LLMRunFrame,
    LLMTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def _followup_prompt(reply_text: str) -> str:
    quoted = reply_text.strip() or "(no spoken content — a tool-call-only turn)"
    return (
        f'Your last reply was: "{quoted}" — and you did not call logInteraction '
        "for it. If that reply resolved or addressed anything the caller asked "
        "— including a short factual answer, not just reservations — call "
        "logInteraction for it right now. If nothing was actually resolved yet "
        "(you're still gathering details, or you just asked a clarifying "
        "question), call nothing and don't say anything further; stay silent."
    )


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

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            self._reply_text_parts.append(frame.text)
            if self._awaiting_followup:
                # This text is from the nudge's own follow-up generation,
                # which is meant to be a silent tool call, never spoken —
                # the model doesn't reliably honor "stay silent" as plain
                # wording (that unreliability is the whole reason this
                # enforcer exists instead of a prompt-only instruction), so
                # swallow it structurally rather than trust compliance.
                # Confirmed live: a turn needing multiple tool calls (e.g. a
                # full reservation — check_availability, book_table,
                # logInteraction, each its own round-trip) was getting its
                # own confirmation spoken 2-3x, once per extra nudge cycle,
                # before this — the more tool calls a turn needed, the more
                # times it repeated.
                return
        elif isinstance(frame, FunctionCallInProgressFrame):
            self._tool_call_seen = True
        elif isinstance(frame, LLMFullResponseEndFrame):
            reply_text = "".join(self._reply_text_parts)
            self._reply_text_parts = []

            if self._awaiting_followup:
                self._awaiting_followup = False
            elif not self._tool_call_seen:
                logger.debug("LogInteractionEnforcer: no tool call this turn, nudging")
                self._context.add_message(
                    {"role": "developer", "content": _followup_prompt(reply_text)}
                )
                if self._worker_handle.worker is not None:
                    self._awaiting_followup = True
                    await self._worker_handle.worker.queue_frames([LLMRunFrame()])
            self._tool_call_seen = False

        await self.push_frame(frame, direction)
