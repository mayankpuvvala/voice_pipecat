"""Drops narrated logging/tool-call text from a reply without eating the rest.

The prompt-only instruction not to narrate logInteraction doesn't
reliably hold. Each "\n\n"-separated paragraph is evaluated on its own
merits and dropped only if it looks like tool-call syntax or a
plain-English logging mention — an earlier version dropped everything
after the first break, which silently ate legitimate multi-paragraph
answers too. Sits after LogInteractionEnforcer.
"""

from __future__ import annotations

import re

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_CODE_SHAPED = re.compile(r"\b(?:[a-z]+[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]*_[A-Za-z0-9_]+)\(")

# Second alternative catches pronoun-free narration like
# "Logging interaction: topic ..., resolved true, ...".
_NARRATES_LOGGING = re.compile(
    r"\b(i'?ll|let me|going to|i'm going to)\s+"
    r"(go ahead and\s+|just\s+)*"
    r"(log|note|jot|record|save)\b"
    r"|\blog(?:ging|ged)?\s+(the\s+|our\s+|this\s+)?(interaction|conversation)\b",
    re.IGNORECASE,
)

# Flags any "{" or "}" rather than matching specific tool-call/JSON shapes —
# normal spoken prose never contains a literal brace.
_BRACE_NARRATION = re.compile(r"[{}]")


def _is_forbidden(paragraph: str) -> bool:
    return bool(
        _CODE_SHAPED.search(paragraph)
        or _NARRATES_LOGGING.search(paragraph)
        or _BRACE_NARRATION.search(paragraph)
    )


class SecondParagraphFilter(FrameProcessor):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._buffer = ""
        self._holding = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = ""
            self._holding = False
        elif isinstance(frame, LLMTextFrame):
            if not self._holding and "\n\n" not in frame.text:
                await self.push_frame(frame, direction)
                return
            if not self._holding:
                before, _, after = frame.text.partition("\n\n")
                if before:
                    await self.push_frame(LLMTextFrame(text=before + "\n\n"), direction)
                self._buffer = after
                self._holding = True
            else:
                self._buffer += frame.text
            await self._flush_complete_paragraphs(direction)
            return
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._holding:
                await self._flush_paragraph(self._buffer, direction)
                self._buffer = ""
                self._holding = False

        await self.push_frame(frame, direction)

    async def _flush_complete_paragraphs(self, direction: FrameDirection) -> None:
        while "\n\n" in self._buffer:
            paragraph, _, rest = self._buffer.partition("\n\n")
            self._buffer = rest
            await self._flush_paragraph(paragraph, direction, trailing_break=True)

    async def _flush_paragraph(
        self, paragraph: str, direction: FrameDirection, *, trailing_break: bool = False
    ) -> None:
        if not paragraph.strip():
            return
        if _is_forbidden(paragraph):
            logger.info("SecondParagraphFilter: dropped a narrated-logging paragraph: {!r}", paragraph)
            return
        text = paragraph + ("\n\n" if trailing_break else "")
        await self.push_frame(LLMTextFrame(text=text), direction)
