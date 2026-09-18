"""Keeps Sarvam TTS's synthesis locale in sync with whatever language the
bot's own reply is actually written in, instead of a locale fixed for the
whole call.

`_build_tts()` in app/main.py used to hardcode `language=Language.HI_IN` for
every reply, for the entire call. Sarvam's `target_language_code` governs
pronunciation, not just which script it can read -- confirmed live: plain
English replies ("That's a table for 3...") came out with guest counts and
other numbers/words spoken with Hindi phonetics, even though the text itself
was English and the caller never used a word of Hindi. `_LANGUAGE_INSTRUCTION`
in prompts.py already tells the model to default to English and only reply in
Hindi once the caller genuinely speaks a full Hindi sentence -- this processor
just makes the TTS locale follow that same already-correct decision, per
reply, instead of staying pinned to one language for the whole call.

Sits right after SecondParagraphFilter (so it sees exactly the final text
that will actually reach the caller, with any narrated tool-call text already
dropped) and before `tts`. Decided once per response from the first
non-empty text chunk -- these replies are short (one sentence, per the
brevity instruction), so a reply's opening words reliably indicate its
language; deciding per-chunk instead would risk flapping the locale
mid-utterance on an English word/number inside an otherwise-Hindi reply.
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

# Hindi (and other Indic scripts this call's STT can produce, Devanagari
# specifically since that's all Hindi ever transcribes as) -- plain Latin-
# script English/Hinglish text never matches this.
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
