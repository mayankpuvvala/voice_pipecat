"""Drops narrated logging/tool-call text from a reply without eating the rest.

The prompt-only instruction not to narrate logInteraction
(_LOGGING_TIMING_INSTRUCTION in prompts.py) doesn't reliably hold -- confirmed
live in both code-shaped ("logInteraction({...});") and plain-English ("I'll
log that for you.") forms reaching real TTS audio.

An earlier version dropped everything after a round's first "\n\n", on the
theory that no legitimate reply needs a real paragraph break. That was
wrong, confirmed live: a caller asked about the beer list, and the
legitimate multi-paragraph, bulleted answer that followed the first break
got silently dropped along with the trailing narration -- almost nothing
reached TTS. A menu question is exactly the kind of thing that legitimately
produces multiple paragraphs.

So each "\n\n"-separated paragraph is now evaluated on its own merits: a
paragraph is dropped only if it looks like narrated tool-call syntax or a
plain-English logging mention; every other paragraph -- bulleted lists
included -- is forwarded. The common case (no "\n\n" at all) streams
through with no added delay; only once a break appears does the rest of the
round get buffered and forwarded paragraph-by-paragraph as each completes.

Sits after LogInteractionEnforcer -- that processor needs the raw LLM frame
stream directly (see its own docstring); this filter only needs whatever
text it already decided to let through.
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

# Two shapes confirmed live, neither caught by a plain first-person-verb
# match: a filler word splitting the verb phrase ("I'll just log our
# conversation"), and a gerund/label narration with no leading pronoun at
# all ("Logging interaction: topic ..., resolved true, ...") -- the second
# alternative below exists for that leading-pronoun-free case.
_NARRATES_LOGGING = re.compile(
    r"\b(i'?ll|let me|going to|i'm going to)\s+"
    r"(go ahead and\s+|just\s+)*"
    r"(log|note|jot|record|save)\b"
    r"|\blog(?:ging|ged)?\s+(the\s+|our\s+|this\s+)?(interaction|conversation)\b",
    re.IGNORECASE,
)

# Two more narration shapes confirmed live (verified against actual TTS
# audio, not just raw text): template-brace syntax ("{{ \n\nlog_interaction}}",
# with the stray "\n\n" splitting it across paragraphs) and raw single-brace
# JSON tool args ('{"topic":"...",...}'). Rather than matching either
# specific shape, just flag any "{" or "}" -- normal spoken prose never
# contains a literal brace, so this is broader and catches both at once.
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
