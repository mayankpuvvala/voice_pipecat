"""Forces a logInteraction check after any assistant turn that ends without
calling any tool, and swallows the spoken text of any round that pipecat's
own function-calling machinery auto-generates afterward — whether that
round was triggered by this enforcer's own nudge or by a normal, fully
compliant reply+tool-call turn.

Prompt-only instructions ("call logInteraction in the same turn") reliably
worked for reservation-style replies but not for short factual answers,
confirmed via isolated eval-transport testing: the model would narrate "I'll
log that" and then simply not call the tool for a plain hours/parking
question, even after the instruction was made explicit and absolute. This
closes the gap structurally instead of with more prompt wording.

Separately: pipecat's LLMAssistantAggregator automatically re-runs the LLM
after ANY function call completes (standard tool-calling continuation, so
the model can react to the tool's result) — this happens unconditionally,
regardless of whether this enforcer had anything to do with the tool call.
When a round already spoke its full reply in the same turn as the tool call
(the normal, compliant case), that automatic continuation has nothing
legitimate left to add, but nothing was stopping it from generating and
speaking a paraphrased restatement anyway. Confirmed live from a real call:
a reply+logInteraction turn and a reply+book_table+logInteraction turn were
both reliably followed by an unprompted, reworded repeat of what was just
said. This enforcer now swallows that continuation too, the same
mechanism used for its own nudge followups.

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

Tracks tool calls via FunctionCallsStartedFrame, not FunctionCallInProgressFrame
— confirmed via a real eval run that FunctionCallInProgressFrame is NOT
reliably ordered before the LLMFullResponseEndFrame of the same round.
BaseLLMService.run_function_calls broadcasts FunctionCallsStartedFrame
synchronously for the whole batch, then (for sequential execution) only
*enqueues* each call for a background task — FunctionCallInProgressFrame is
broadcast later, from that task, once it actually starts running. A round's
LLMFullResponseEndFrame can reach this processor before that task gets
scheduled, which read as "no tool call" here and cleared the swallow state
one round early — the observed symptom was an extra, unprompted turn typing
right past a question the caller hadn't even answered yet, not a repeat of
the same words. FunctionCallsStartedFrame (a SystemFrame, so it doesn't wait
in the normal per-round frame flow) doesn't have this gap.
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
        elif isinstance(frame, FunctionCallsStartedFrame):
            self._tool_call_seen = True
        elif isinstance(frame, LLMFullResponseEndFrame):
            reply_text = "".join(self._reply_text_parts)
            self._reply_text_parts = []

            if self._awaiting_followup:
                # Log at INFO (not DEBUG) deliberately: this is the one place
                # that would prove or disprove whether a repeated reply came
                # from this enforcer's own followup round vs. some other
                # cause (e.g. a duplicate STT/LLM trigger upstream of this
                # processor entirely) — cheap enough to always leave on.
                logger.info(
                    "LogInteractionEnforcer: swallowed round ended (tool_call={}, text={!r})",
                    self._tool_call_seen,
                    reply_text,
                )
                if not self._tool_call_seen:
                    # A swallowed round that itself called a tool (e.g.
                    # logInteraction) isn't actually done — pipecat's own
                    # LLMAssistantAggregator automatically runs another round
                    # right after any function result (see
                    # _maybe_push_context_after_function_result), independent
                    # of this enforcer. That next round is still part of the
                    # same silent exchange, not a new reply to the caller, so
                    # keep swallowing until a round finally finishes with
                    # nothing left to call. Confirmed live: clearing this
                    # unconditionally let that automatic continuation's text
                    # slip past the swallow and get spoken, sounding like the
                    # bot repeating itself.
                    self._awaiting_followup = False
            elif not self._tool_call_seen:
                logger.info(
                    "LogInteractionEnforcer: no tool call this turn, nudging (reply={!r})",
                    reply_text,
                )
                self._context.add_message(
                    {"role": "developer", "content": _followup_prompt(reply_text)}
                )
                if self._worker_handle.worker is not None:
                    self._awaiting_followup = True
                    await self._worker_handle.worker.queue_frames([LLMRunFrame()])
            elif reply_text.strip():
                # This round both spoke to the caller AND called a tool in
                # the same turn — exactly what the system prompt asks for,
                # no nudge needed. But pipecat's LLMAssistantAggregator still
                # automatically runs another round right after any function
                # result, whether or not this enforcer had anything to do
                # with the tool call. Since this round already said
                # everything it had to say, that automatic continuation has
                # nothing legitimate left to add. Confirmed live from a real
                # call: every one of these (a reply+logInteraction turn, a
                # reply+book_table+logInteraction turn) was followed by an
                # unprompted, paraphrased restatement of what was just said,
                # spoken with no caller input in between. Swallow it the
                # same way as a nudge followup. A tool-call-only round with
                # nothing spoken (e.g. a silent check_availability lookup)
                # deliberately isn't covered here — its continuation is that
                # turn's first real chance to speak, so let it through.
                logger.info(
                    "LogInteractionEnforcer: round replied and called a tool — "
                    "swallowing the automatic post-tool-call continuation"
                )
                self._awaiting_followup = True
            self._tool_call_seen = False

        await self.push_frame(frame, direction)
