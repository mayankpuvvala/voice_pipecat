"""Drops narrated logging/tool-call text from a reply without eating the rest.

Originally built to catch narrated tool-call syntax reaching the caller
(_LOGGING_TIMING_INSTRUCTION in prompts.py tells the model never to write a
function name or argument syntax into its reply, but that's a prompt-only
instruction that doesn't reliably hold -- the same lesson
logging_enforcer.py already learned for logInteraction itself). Confirmed
live via the eval suite: a reply came back as real spoken content, a blank
line, then a literal narrated call --

    "Yes, we have a small lot behind the building... \n\nlogInteraction({
      topic: "parking availability", ...
    });"

-- and the `logInteraction({...})` fragment reached TTS intact.

A second, real call then showed the same "\n\n"-separated shape carrying a
different kind of unwanted content -- not code, just a stray extra
sentence the model was never supposed to say:

    "Our address is 142 Residency Road... \n\n I'll log that for you."

with "I'll log that for you." reaching TTS -- a plain-English narration of
the same forbidden logging-mention, just not code-shaped.

An earlier version of this filter reacted by dropping *everything* after
the first "\n\n" in a round, unconditionally, on the theory that no
legitimate reply needs a real paragraph break. That was wrong, confirmed
live: a caller asked "what beers do you have," and the model's answer was
"...along with pizzas and fast food.\n\nFor beers, we brew several
in-house varieties, including:\n\n- Old Timer (Witbier)\n- ...". The whole
beer list -- the actual answer to the question -- came after that first
blank line and got silently dropped along with the trailing "I'll log
this" narration, so almost nothing reached TTS. A caller asking about a
menu is exactly the kind of question that legitimately produces a
multi-paragraph, bulleted answer.

So this now evaluates each "\n\n"-separated paragraph in a round on its
own merits instead of accept/reject-ing the whole remainder as one unit:
a paragraph is dropped only if it looks like narrated tool-call syntax or
a plain-English mention of the act of logging/noting/saving/recording;
every other paragraph -- including a bulleted list with blank lines
between items -- is forwarded. The common case (no "\n\n" at all) still
streams through immediately with no added delay; only once a break
appears does the rest of the round get buffered (to evaluate complete
paragraphs, not partial ones) and forwarded paragraph-by-paragraph as
each one completes.

Sits after LogInteractionEnforcer, not before: that processor's own
positioning (see its docstring) is tied to seeing the raw LLM frame stream
directly, and this filter only needs whatever text LogInteractionEnforcer
already decided to let through.
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

# camelCase or snake_case immediately (no space) followed by "(" -- e.g.
# "logInteraction(", "check_availability(", "bookTable(" -- the shape of
# every narrated code call seen so far, regardless of the exact casing the
# model used for the tool's name. Deliberately NOT just "word(" with no
# case/underscore requirement: confirmed live that a plain English
# parenthetical like "Old Timer (Witbier)" matches that naively and gets
# a real menu item dropped as if it were code -- camelCase/snake_case
# essentially never occurs in ordinary spoken English, so requiring one
# keeps this narrow to what it's actually meant to catch.
_CODE_SHAPED = re.compile(r"\b(?:[a-z]+[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]*_[A-Za-z0-9_]+)\(")

# Plain-English mentions of the act of logging/noting/saving/recording --
# the prose equivalent of the same forbidden narration
# (_LOGGING_TIMING_INSTRUCTION explicitly bans both forms).
_NARRATES_LOGGING = re.compile(
    r"\b(i'?ll|let me|going to|i'm going to)\s+"
    r"(go ahead and\s+)?"
    r"(log|note|jot|record|save)\b",
    re.IGNORECASE,
)


def _is_forbidden(paragraph: str) -> bool:
    return bool(_CODE_SHAPED.search(paragraph) or _NARRATES_LOGGING.search(paragraph))


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
