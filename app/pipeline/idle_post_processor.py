"""Idle-triggered post-call analysis: a careful, whole-transcript Confidence
rating and Escalation determination for calls already saved to the
Recordings sheet, run only once the bot has genuinely gone quiet (see
app/pipeline/active_calls.py) — never competes with a live call for the
same OpenAI/Sheets budget.

Why this exists separately from the live call_confidence/drift fields
log_interaction already asks the model for (app/tools/log_interaction.py):
those are a rushed, per-topic self-report made mid-conversation, useful in
real time but noisy. This looks at the complete transcript after the fact,
with no latency pressure, and produces one whole-call verdict instead of a
per-topic guess.

Writes to the Recordings tab's PostConfidence/Escalated/PostProcessedAt
columns. Those don't exist on the sheet yet — add them to Recordings' header
row to turn this on. Until then, sheets_client.update_cells silently drops
the unknown columns (same contract as append_row), so a call is simply
never marked processed and this keeps retrying it every idle tick until the
columns exist. app/admin/sheets_reader.py and admin_service/sheets_reader.py
both prefer these over the live self-reported values once PostProcessedAt
is set.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from loguru import logger
from openai import AsyncOpenAI

from app.config.settings import settings
from app.pipeline import active_calls
from app.services import sheets_client

_RECORDINGS_SHEET = "Recordings"

# Only run once the bot has been call-free for this long, so a quick gap
# between two back-to-back calls never triggers a batch mid-lull.
_MIN_IDLE_SECS = 45.0
_POLL_INTERVAL_SECS = 60.0
# Calls processed per idle tick — bounded so a big backlog can't burn a lot
# of OpenAI budget in one go; the loop just picks the rest up next tick.
_BATCH_SIZE = 5

_ANALYSIS_SYSTEM_PROMPT = (
    "You are reviewing a completed restaurant phone call transcript after "
    "the fact, with no time pressure, to produce the call's final record for "
    "the owner's dashboard. Read the whole transcript and call "
    "record_call_analysis once with your honest assessment.\n\n"
    "confidence: \"high\" if the bot clearly understood the caller and "
    "handled everything correctly, \"medium\" if there was some ambiguity "
    "(unclear speech, a guess on intent) but it was probably handled right, "
    "\"low\" if the bot likely misunderstood the caller or left them "
    "unsatisfied or confused.\n\n"
    "escalated: true if a human (the owner or staff) actually needs to "
    "follow up with this caller — the bot took a message, promised a "
    "callback, hit something out of scope, or the caller seemed unresolved "
    "at the end. false if the call was fully self-contained and nothing "
    "more is needed from a person."
)

_ANALYSIS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "record_call_analysis",
            "description": "Record the final assessment of this call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "escalated": {"type": "boolean"},
                },
                "required": ["confidence", "escalated"],
            },
        },
    }
]


async def _analyze_transcript(transcript: str) -> dict[str, object] | None:
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            tools=_ANALYSIS_TOOLS,
            tool_choice={"type": "function", "function": {"name": "record_call_analysis"}},
            # gpt-5.6-luna 400s on function tools without this — see
            # app/pipeline/logging_enforcer.py and TROUBLESHOOTING.md.
            reasoning_effort="none",
        )
        tool_call = response.choices[0].message.tool_calls[0]
        return json.loads(tool_call.function.arguments)
    except Exception:
        logger.exception("idle_post_processor: transcript analysis failed")
        return None


def _pending_rows() -> list[tuple[int, dict[str, str]]]:
    """Recordings rows with a transcript but no PostProcessedAt yet, paired
    with their 1-indexed sheet row number (row 1 is the header)."""
    rows = sheets_client.read_rows(_RECORDINGS_SHEET)
    return [
        (offset + 2, row)
        for offset, row in enumerate(rows)
        if row.get("Transcript", "").strip() and not row.get("PostProcessedAt", "").strip()
    ]


async def run_idle_post_processing_once() -> int:
    """Processes up to _BATCH_SIZE pending calls. Returns how many actually
    got written. Never raises — a bad batch just gets retried next tick."""
    try:
        pending = _pending_rows()
    except Exception:
        logger.exception("idle_post_processor: failed to read Recordings sheet")
        return 0

    processed = 0
    for row_number, row in pending[:_BATCH_SIZE]:
        call_session_id = row.get("CallSessionId", "")
        result = await _analyze_transcript(row["Transcript"])
        if result is None:
            continue

        try:
            sheets_client.update_cells(
                _RECORDINGS_SHEET,
                row_number,
                {
                    "PostConfidence": result.get("confidence", "medium"),
                    "Escalated": bool(result.get("escalated", False)),
                    "PostProcessedAt": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            logger.exception(
                "idle_post_processor: failed writing analysis for call {}", call_session_id
            )
            continue

        processed += 1
        logger.info(
            "idle_post_processor: analyzed call {} -> confidence={} escalated={}",
            call_session_id,
            result.get("confidence"),
            result.get("escalated"),
        )

    return processed


async def idle_post_processing_loop() -> None:
    """Runs for the life of the process (started once at FastAPI startup —
    see app/main.py). Only does work while genuinely idle: zero active
    calls, and it's stayed that way for _MIN_IDLE_SECS."""
    logger.info(
        "idle_post_processor: background loop started (poll every {}s, idle "
        "gate {}s, batch size {})",
        _POLL_INTERVAL_SECS,
        _MIN_IDLE_SECS,
        _BATCH_SIZE,
    )
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECS)
        if not active_calls.is_idle(_MIN_IDLE_SECS):
            continue
        try:
            processed = await run_idle_post_processing_once()
            if processed:
                logger.info("idle_post_processor: processed {} call(s) this idle tick", processed)
        except Exception:
            logger.exception("idle_post_processor: idle tick failed")
