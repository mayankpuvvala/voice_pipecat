"""Drops a spoken reply's second paragraph instead of letting it reach TTS.

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
sentence the model was never supposed to say. One live call got

    "Our address is 142 Residency Road... \n\n I'll log that for you."

with "I'll log that for you." reaching TTS (a plain-English narration of
the same forbidden logging-mention, just not code-shaped -- an earlier,
narrower version of this filter that only dropped code-*looking* text let
this straight through). Another got a reservation question asked, a blank
line, then the exact same question again -- the model repeating itself
within a single generation, nothing to do with logging at all.

None of these have a legitimate counterpart: this app's prompt already
asks for one short, self-contained utterance per turn (see
_BREVITY_INSTRUCTION -- "ask ONE question at a time," "don't pad with
extra pleasantries or detail") and no other observed reply, correct or
broken, has ever needed a real paragraph break. So rather than pattern-
matching each new shape this failure mode turns up in, this filter just
drops everything after the first "\n\n" in a round, unconditionally. The
common case (no "\n\n" at all, the overwhelming majority of replies)
streams through with no added delay -- the hold only kicks in once a break
actually appears, and even then only the second paragraph is held (and
dropped); the first paragraph -- the real answer -- still streams in real
time.

Sits after LogInteractionEnforcer, not before: that processor's own
positioning (see its docstring) is tied to seeing the raw LLM frame stream
directly, and this filter only needs whatever text LogInteractionEnforcer
already decided to let through.
"""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class SecondParagraphFilter(FrameProcessor):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._held_parts: list[str] = []
        self._holding = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._held_parts = []
            self._holding = False
        elif isinstance(frame, LLMTextFrame):
            if self._holding:
                self._held_parts.append(frame.text)
                return
            if "\n\n" in frame.text:
                before, _, after = frame.text.partition("\n\n")
                if before:
                    await self.push_frame(LLMTextFrame(text=before), direction)
                self._held_parts = [after] if after else []
                self._holding = True
                return
            await self.push_frame(frame, direction)
            return
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._holding:
                held = "".join(self._held_parts)
                self._held_parts = []
                self._holding = False
                if held.strip():
                    logger.info(
                        "SecondParagraphFilter: dropped a reply's second paragraph: {!r}",
                        held,
                    )

        await self.push_frame(frame, direction)
