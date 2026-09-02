"""Strips narrated tool-call syntax out of the model's spoken reply.

_LOGGING_TIMING_INSTRUCTION (prompts.py) already tells the model never to
write a function name, code, or argument syntax into its spoken reply —
but that's a prompt-only instruction, and (the same lesson
logging_enforcer.py already learned for logInteraction itself) prompt-only
instructions don't reliably hold under this model. Confirmed live via the
eval suite: a reply came back as

    "Yes, we have a small lot behind the building... \n\nlogInteraction({
      topic: "parking availability", ...
    });"

and the `logInteraction({...})` fragment reached TTS intact — real code
syntax that would have been read aloud to a caller on a live call.

Both observed failures put the fake call after a blank-line break ("\n\n")
following the real spoken content. That lines up with the rest of this
app's prompt design, which already asks for one short, self-contained
utterance per turn (see _BREVITY_INSTRUCTION in prompts.py) — so a second
paragraph is already off-spec on its own, before even checking what's in
it. This processor holds back anything after the first "\n\n" in a round
instead of streaming it straight to TTS, and only releases it once the
round ends and it's been checked against a narrated-tool-call pattern:
dropped (never spoken, never added to context) if it matches, forwarded
otherwise so genuine content isn't silently eaten. The common case (no
"\n\n" at all, the overwhelming majority of replies) streams through with
no added delay — the hold only kicks in once a break actually appears.

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

# A bare identifier immediately followed by "(" -- e.g. "logInteraction(",
# "check_availability(", "bookTable(" -- the shape of every narrated tool
# call seen so far, regardless of the exact casing/spelling the model used
# for the tool's name.
_TOOL_CALL_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*\(")


class NarratedToolCallFilter(FrameProcessor):
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
                if _TOOL_CALL_PATTERN.search(held):
                    logger.info(
                        "NarratedToolCallFilter: dropped narrated tool-call text: {!r}",
                        held,
                    )
                elif held:
                    await self.push_frame(LLMTextFrame(text=held), direction)

        await self.push_frame(frame, direction)
