"""Backfills a missed logInteraction call in the background, and — only
when a reply already sounds like a goodbye without end_call having been
called — forces a synchronous foreground nudge to actually end the call.

Backfilling used to force a synchronous extra LLM completion inline,
which queued behind the caller's own next turn and doubled round-trip
latency on nearly every turn. Since logInteraction only feeds the /admin
log, a missed call is now backfilled by a separate, un-awaited background
OpenAI completion instead. Ending the call stays synchronous, since a
call that should end but doesn't is worse than a late log row;
`turn_taking_guard.py` backstops this with a transaction-agnostic invariant.
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

# Generated from log_interaction's own signature/docstring so the backfill
# call always matches the real tool's fields — no hand-copied schema.
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
    # Bare "112" catches every phrasing ("dial 112", "by dialing 112"); an
    # exact-phrase match missed some live and left emergencies un-nudged.
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
    """Holds the PipelineWorker once it exists — the processor is built
    before the worker, so it can't take it directly in its constructor."""

    def __init__(self) -> None:
        self.worker = None


# Process-lifetime counters, not per-call -- answers "is the backfill path
# actually rare or is it firing on nearly every turn" without grepping logs.
# Reset on every deploy/restart; that's fine, this is a live health signal
# (see GET / in app/main.py), not a stored metric. Each turn's outcome
# increments exactly one of these -- see the two conclusion points in
# process_frame below.
LOG_INTERACTION_STATS = {"inline": 0, "backfilled": 0}


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
        # Persists across the silent tool-call-only rounds of a multi-round
        # transaction (check_availability -> log_interaction -> final spoken
        # reply each land as separate LLMFullResponseEndFrames) -- only
        # _tool_call_seen is per-round. Without this, a transaction that
        # calls log_interaction in an earlier round still looked "unlogged"
        # by the time the final, text-only round arrived, firing a
        # redundant backfill call every single time. Reset only at an
        # actual transaction boundary, not every round -- see process_frame.
        self._log_interaction_seen = False
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
            if any(fc.function_name == "log_interaction" for fc in frame.function_calls):
                self._log_interaction_seen = True
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
                    if self._log_interaction_seen:
                        LOG_INTERACTION_STATS["inline"] += 1
                    self._log_interaction_seen = False
            elif not self._tool_call_seen and reply_text.strip() and not self._log_interaction_seen:
                ending = _sounds_like_ending(reply_text)
                logger.info(
                    "LogInteractionEnforcer: no log_interaction call this "
                    "transaction — backfilling in the background (reply={!r}, "
                    "sounds_like_ending={})",
                    reply_text,
                    ending,
                )
                # Never awaited: the caller's next turn must not queue behind
                # this. See module docstring.
                self.create_task(self._backfill_log_interaction(reply_text))
                self._log_interaction_seen = False
                LOG_INTERACTION_STATS["backfilled"] += 1

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
            elif not self._tool_call_seen and reply_text.strip():
                # log_interaction was already called in an earlier round of
                # this same transaction -- final round, nothing to backfill,
                # and no tool call here so no continuation to swallow either.
                logger.info(
                    "LogInteractionEnforcer: already logged earlier this "
                    "transaction — no backfill needed (reply={!r})",
                    reply_text,
                )
                self._log_interaction_seen = False
                LOG_INTERACTION_STATS["inline"] += 1
            elif reply_text.strip():
                # Tool call(s) this round alongside text -- pipecat will
                # auto-continue after the tool call, so swallow that
                # continuation (same as always; unrelated to log_interaction
                # specifically). Don't reset _log_interaction_seen: it
                # carries forward into the swallowed continuation round(s).
                logger.info(
                    "LogInteractionEnforcer: round replied and called a tool — "
                    "swallowing the automatic post-tool-call continuation"
                )
                self._awaiting_followup = True
                self._chain_extends = True
            self._tool_call_seen = False

        await self.push_frame(frame, direction)

    async def _backfill_log_interaction(self, reply_text: str) -> None:
        """Classify and log a reply the model didn't log, via a standalone
        OpenAI call. Best-effort: a failure costs a missing /admin row,
        never the live call, so it's logged and dropped, not retried."""
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
                # app/main.py's _build_llm and TROUBLESHOOTING.md).
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
