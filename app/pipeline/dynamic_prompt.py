"""Keeps the system prompt limited to topics the caller has actually raised.

A plain reservation call ("table for 4 Saturday 8pm") never touches the
menu, membership perks, seating/ambience, or any of a restaurant's other
optional facts — see app/config/restaurants/__init__.py's `TopicFacts` and
each restaurant's `topic_facts`. Shipping all of that on every single turn
regardless of what the call is actually about is pure wasted prompt tokens:
fewer input tokens per turn (cost), a shorter prompt to prefill (latency),
and less unrelated text for the model to weigh when deciding how to answer
the caller's actual question.

This is deliberately NOT retrieval-augmented generation — no embeddings, no
vector search, no extra LLM/network round trip (which would cost more
latency than it saves). It's a plain keyword match against the caller's own
words, run in-process against text already in memory. A missed keyword just
means that topic's facts stay out of the prompt; the base prompt's own rule
("if you don't know, don't guess — take a message") already covers that
gracefully, same as it would if the fact genuinely didn't exist.

Cumulative for the life of the call, never removes a topic once matched —
a caller might ask a follow-up about "it" a few turns after first
mentioning "the menu" without repeating the keyword, and dropping context
they were already given would be worse than the extra tokens of keeping it.
"""

from __future__ import annotations

import re

from loguru import logger

from pipecat.frames.frames import Frame, LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.aggregators.llm_context import LLMContext, LLMContextMessage
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService

from app.config.restaurants import Restaurant, TopicFacts
from app.pipeline.prompts import build_system_prompt


def _message_text(message: LLMContextMessage) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


class DynamicPromptInjector(FrameProcessor):
    """One per call — see app/main.py's `run_bot`. Sits between the user
    context aggregator and the LLM service so `self._context.messages`
    already includes the caller's latest turn by the time an
    `LLMContextFrame` reaches it.
    """

    def __init__(
        self, *, context: LLMContext, restaurant: Restaurant, llm: OpenAILLMService, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._context = context
        self._restaurant = restaurant
        self._llm = llm
        self._active_topics: frozenset[TopicFacts] = frozenset()
        self._matchers: dict[TopicFacts, re.Pattern[str]] = {
            topic: re.compile(
                r"\b(" + "|".join(re.escape(keyword) for keyword in topic.keywords) + r")\b",
                re.IGNORECASE,
            )
            for topic in restaurant.topic_facts
        }

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            user_text = " ".join(
                _message_text(message)
                for message in self._context.messages
                if message.get("role") == "user"
            )
            newly_matched = {
                topic
                for topic, pattern in self._matchers.items()
                if topic not in self._active_topics and pattern.search(user_text)
            }
            if newly_matched:
                self._active_topics |= newly_matched
                logger.debug(
                    "DynamicPromptInjector: topic(s) newly active this call: {} (total active: {})",
                    [topic.keywords[0] for topic in newly_matched],
                    len(self._active_topics),
                )
                await self.push_frame(
                    LLMUpdateSettingsFrame(
                        service=self._llm,
                        delta=OpenAILLMService.Settings(
                            system_instruction=build_system_prompt(
                                self._restaurant, frozenset(self._active_topics)
                            )
                        ),
                    ),
                    direction,
                )

        await self.push_frame(frame, direction)
