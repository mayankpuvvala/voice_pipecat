"""Builds a human-readable transcript from the LLM context, and a short
post-call summary via a one-off LLM completion.

This is deliberately separate from logInteraction's per-topic rows, not a
replacement for them — those stay real-time specifically because callers
hang up abruptly and an "at the end" log would lose data when that happens.
This transcript/summary IS built at call-end (see run_bot's
on_client_disconnected), but it's a supplementary record for the admin page
and owner review, not the thing anything else depends on for correctness.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from pipecat.processors.aggregators.llm_context import LLMContext

from app.config.settings import settings

_SUMMARY_PROMPT = (
    "Summarize this restaurant phone call for the owner in 1-2 short "
    "sentences: what the caller wanted, and the outcome. Be factual and "
    "concise — no preamble, no restating that it's a summary."
)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def build_transcript(context: LLMContext) -> str:
    """Render user/assistant turns as "Caller:"/"Meera:" lines. Skips
    developer (our own logging-enforcer nudges) and tool-result messages —
    those aren't part of what was actually said on the call."""
    lines: list[str] = []
    for msg in context.messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _message_text(msg.get("content")).strip()
        if not text:
            continue
        speaker = "Caller" if role == "user" else "Meera"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


async def generate_call_summary(transcript: str) -> str:
    """Never raises — a failed summary shouldn't block saving the rest of
    the call record."""
    if not transcript.strip():
        return ""
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": transcript},
            ],
            max_tokens=120,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Failed to generate call summary")
        return ""
