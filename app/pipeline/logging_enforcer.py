"""Backfills a missed logInteraction call in the background, and — only for
the rare case a reply already sounds like a goodbye without end_call having
been called — forces a synchronous foreground nudge to actually end the call.

Previously both of these were the same mechanism: any unlogged reply forced
a synchronous extra LLM completion (a "developer" nudge message + a fresh
LLMRunFrame through the same OpenAILLMService instance) before the pipeline
would continue. Confirmed live via /live testing: pipecat's LLM service
processes LLMContextFrames strictly in arrival order, one at a time, so that
forced completion sat in the *same queue* the caller's own next turn needed —
if the caller finished speaking while the nudge's completion was still in
flight, their real question waited behind pure bookkeeping. Nearly every
reply hit this path (the prompt-only instruction to call logInteraction
inline turned out to be unreliable), so nearly every turn paid for two full
LLM round-trips back to back instead of one.

logInteraction only feeds the /admin call log — nothing in the live call
depends on it being written before the next turn starts. So instead of
re-entering the shared pipeline, a missed logInteraction call is backfilled
by a *separate*, independent OpenAI completion (its own AsyncOpenAI client,
forced via tool_choice to call log_interaction, given the conversation so far
plus the reply that needs logging) fired as a background task and never
awaited — the caller's next turn is never queued behind it. The tool schema
is generated from log_interaction's own docstring via pipecat's own adapter
machinery, not hand-duplicated, so the two can't drift out of sync.

Ending the call is different: a call that should end but doesn't is worse
than a log row landing a couple seconds late, and this only fires when a
reply already sounds like a goodbye (rare) — so that one case keeps the
original synchronous nudge-and-swallow behavior. The `_awaiting_followup`/
`_chain_extends` pair below is the same *response transaction* tracking as
before, just scoped to this narrower case: one caller-facing reply plus
however many silent tool-call-and-continuation rounds pipecat generates on
its own to produce it. This tracking is best-effort, not authoritative --
pipecat's automatic continuation frame looks identical whether it's genuinely
still part of the current transaction or the model has moved on to unprompted
new content, so a nudge-originated transaction deliberately stops swallowing
after its own immediate followup (`_chain_extends = False`) so a real next
question isn't lost as dead air -- which is also the gap that
`turn_taking_guard.py`'s `OneUtterancePerTurnGuard` exists to backstop with a
transaction-agnostic invariant: never more than one caller-facing utterance
per caller turn, however many transactions produced it.
"""

from __future__ import annotations

import json

from loguru import logger
from openai import AsyncOpenAI

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.adapters.services.open_ai_adapter import OpenAILLMAdapter
from pipecat.frames.frames import (
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMRunFrame,
    LLMTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.config.settings import settings
from app.tools.log_interaction import log_interaction, write_interaction_row

# Generated once from log_interaction's own signature/docstring (the same
# machinery `LLMContext(tools=[log_interaction, ...])` uses in main.py) so
# the background backfill call always matches the real tool's fields and
# field descriptions -- no hand-copied schema to fall out of sync.
_LOG_INTERACTION_TOOLS = OpenAILLMAdapter().to_provider_tools_format(
    ToolsSchema(standard_tools=[log_interaction])
)

_BACKFILL_SYSTEM_PROMPT = (
    "You are backfilling a restaurant voice bot's call log, not continuing "
    "the call. The assistant's last reply (given below, plus the "
    "conversation before it for context) resolved or addressed something "
    "the caller asked, but the assistant didn't call logInteraction for it. "
    "Call logInteraction now with accurate fields for that reply, exactly as "
    "the tool's own field descriptions ask for. Only call the tool -- do not "
    "say anything else."
)

_ENDING_SIGNAL_PHRASES = (
    "goodbye",
    "have a great day",
    "talk soon",
    "hang up",
    # Bare "112" catches every phrasing ("dial 112", "by dialing 112", "112
    # immediately"); an exact-phrase match missed "dialing 112" live and
    # left an emergency reply un-nudged for the full 60s timeout. Only
    # appears in this app's emergency instruction, so it's an unambiguous signal.
    "112",
)


def _sounds_like_ending(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _ENDING_SIGNAL_PHRASES)


_END_CALL_FOLLOWUP_PROMPT = (
    "Your last reply already sounded like the call was ending (a goodbye, or "
    "telling the caller to hang up for an emergency), but you did not call "
    "end_call for it. The conversation is over -- call end_call now, in this "
    "same silent turn, and don't say anything further out loud."
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
    def __init__(
        self,
        context: LLMContext,
        worker_handle: WorkerHandle,
        *,
        call_session_id: str,
        caller_phone: str,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._context = context
        self._worker_handle = worker_handle
        self._call_session_id = call_session_id
        self._caller_phone = caller_phone
        self._openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
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
                    "LogInteractionEnforcer: no tool call this turn — backfilling "
                    "logInteraction in the background (reply={!r}, sounds_like_ending={})",
                    reply_text,
                    ending,
                )
                # Never awaited: the caller's next turn must not queue behind
                # this. See module docstring.
                self.create_task(self._backfill_log_interaction(reply_text))

                if ending:
                    logger.info(
                        "LogInteractionEnforcer: reply sounds like a goodbye but "
                        "end_call wasn't called — nudging synchronously"
                    )
                    self._context.add_message(
                        {"role": "developer", "content": _END_CALL_FOLLOWUP_PROMPT}
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

    async def _backfill_log_interaction(self, reply_text: str) -> None:
        """Classify and log a reply the model itself didn't log, via a
        standalone OpenAI call that never touches the shared conversational
        pipeline. Best-effort: any failure here only costs a missing /admin
        row, never the live call, so it's logged and dropped, not retried."""
        messages = [
            {"role": "system", "content": _BACKFILL_SYSTEM_PROMPT},
            *[dict(m) for m in self._context.messages],
            {"role": "assistant", "content": reply_text},
        ]
        try:
            response = await self._openai_client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                tools=_LOG_INTERACTION_TOOLS,
                tool_choice={"type": "function", "function": {"name": "log_interaction"}},
                # gpt-5.6-luna 400s on function tools without this (see
                # app/main.py's _build_llm and TROUBLESHOOTING.md) --
                # confirmed live 2026-09-19 when this call site broke on
                # every single turn because only _build_llm had the fix.
                reasoning_effort="none",
            )
            tool_call = response.choices[0].message.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
        except Exception:
            logger.exception(
                "LogInteractionEnforcer: background classification failed for reply={!r}",
                reply_text,
            )
            return

        ok = await write_interaction_row(
            call_session_id=self._call_session_id,
            caller_phone=args.get("caller_phone") or self._caller_phone,
            topic=args.get("topic", ""),
            resolved=bool(args.get("resolved", False)),
            caller_name=args.get("caller_name", ""),
            details=args.get("details", ""),
            guests_count=args.get("guests_count", ""),
            drift=bool(args.get("drift", False)),
            call_confidence=args.get("call_confidence", "medium"),
        )
        logger.info(
            "LogInteractionEnforcer: background-logged (ok={}) topic={!r}",
            ok,
            args.get("topic"),
        )
