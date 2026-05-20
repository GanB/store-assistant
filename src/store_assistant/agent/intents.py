from __future__ import annotations

import json
import re
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, SystemMessage

from store_assistant.agent.prompts import INTENT_CLASSIFIER_PROMPT

TERMINATION_PATTERNS: tuple[str, ...] = (
    r"\bi'?m\s+done\b",
    r"\bi\s+am\s+done\b",
    r"\bwe'?re\s+done\b",
    r"\ball\s+done\b",
    r"\bi'?m\s+good\b",
    r"\bi\s+am\s+good\b",
    r"\bthat'?s\s+(it|all)\b",
    r"\bthat\s+is\s+(it|all)\b",
    r"\bnothing\s+else\b",
    r"\bno\s+more\s+(stores|store|records|record)\b",
    r"\bgoodbye\b",
    r"\b(thanks|thank\s+you)\s*[,!.]?\s*bye\b",
    r"\bend\s+(this\s+)?(conversation|chat|session)\b",
    r"^\s*(quit|exit|bye)\s*[!.]?\s*$",
)

OFF_SCOPE_KEYWORDS: tuple[str, ...] = (
    "weather",
    "joke",
    "song",
    "movie",
    "recipe",
    "translate",
    "poem",
    "story",
    "philosophy",
    "politics",
    "news",
    "stock",
    "math problem",
    "calculate",
)


def detect_termination_utterance(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.strip().lower()
    return any(re.search(pattern, lowered) for pattern in TERMINATION_PATTERNS)


def detect_off_scope_utterance(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in OFF_SCOPE_KEYWORDS)


class _AsyncRunnable(Protocol):
    async def ainvoke(
        self,
        input: list[BaseMessage],  # noqa: A002
        config: Any | None = None,
    ) -> BaseMessage: ...


async def classify_scope(messages: list[BaseMessage], llm: _AsyncRunnable) -> bool:
    if not messages:
        return True
    prompt = [SystemMessage(content=INTENT_CLASSIFIER_PROMPT), *messages]
    response = await llm.ainvoke(prompt)
    raw = str(response.content).strip()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return True
    value = parsed.get("is_in_scope")
    if isinstance(value, bool):
        return value
    return True
