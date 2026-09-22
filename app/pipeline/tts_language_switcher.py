"""Keeps Sarvam TTS's synthesis locale in sync with the bot reply's actual
language, instead of a locale fixed for the whole call.

Sarvam's `target_language_code` governs pronunciation, not just script —
a hardcoded Hindi locale made plain English replies come out with Hindi
phonetics. Sits after SecondParagraphFilter, before `tts`; decided once
per response from the first non-empty text chunk.
"""

from __future__ import annotations

import re

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transcriptions.language import Language

# Devanagari script — plain Latin-script English/Hinglish never matches this.
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class TTSLanguageSwitcher(FrameProcessor):
    def __init__(
        self,
        tts,
        *,
        default_language: Language = Language.EN_IN,
        hindi_language: Language = Language.HI_IN,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._tts = tts
        self._default_language = default_language
        self._hindi_language = hindi_language
        self._current_language = default_language
        self._decided_this_response = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._decided_this_response = False
        elif (
            isinstance(frame, LLMTextFrame)
            and not self._decided_this_response
            and frame.text.strip()
        ):
            self._decided_this_response = True
            target = self._hindi_language if _DEVANAGARI.search(frame.text) else self._default_language
            if target != self._current_language:
                logger.debug(
                    "TTSLanguageSwitcher: switching TTS language {} -> {}",
                    self._current_language,
                    target,
                )
                self._current_language = target
                await self.push_frame(
                    TTSUpdateSettingsFrame(
                        service=self._tts,
                        delta=type(self._tts).Settings(language=target),
                    ),
                    direction,
                )

        await self.push_frame(frame, direction)
