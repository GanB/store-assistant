from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.tools import BaseTool

from store_assistant.config import Settings

DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.0


def get_llm(
    settings: Settings,
    tools: Sequence[BaseTool] | None = None,
) -> Any:
    llm = ChatAnthropic(
        model=settings.app_llm_model,
        anthropic_api_key=settings.anthropic_api_key,
        temperature=DEFAULT_TEMPERATURE,
        max_tokens=DEFAULT_MAX_TOKENS,
    )
    if tools:
        return llm.bind_tools(list(tools))
    return llm
