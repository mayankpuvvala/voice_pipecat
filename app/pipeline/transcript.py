"""Builds a human-readable transcript from the LLM context, and a short
post-call summary via a one-off LLM completion.

Separate from logInteraction's per-topic rows, which stay real-time since
callers hang up abruptly. This is a supplementary record for admin/owner review.
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


def build_transcript(context: LLMContext, bot_name: str) -> str:
    """Render user/assistant turns as "Caller:"/"{bot_name}:" lines. Skips
    developer nudges and tool-result messages. bot_name is a required
    parameter rather than read from ACTIVE_RESTAURANT, so this stays
    reusable across restaurants.
    """
    lines: list[str] = []
    for msg in context.messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _message_text(msg.get("content")).strip()
        if not text:
            continue
        speaker = "Caller" if role == "user" else bot_name
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
            # max_tokens (not max_completion_tokens) 400s on gpt-5.6-luna --
            # confirmed live 2026-09-19, see TROUBLESHOOTING.md.
            max_completion_tokens=120,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Failed to generate call summary")
        return ""
