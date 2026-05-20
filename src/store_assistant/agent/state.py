from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    off_scope_count: int
    terminated: bool
    summary: str | None
    iteration_count: int


def initial_state() -> AgentState:
    return AgentState(
        messages=[],
        off_scope_count=0,
        terminated=False,
        summary=None,
        iteration_count=0,
    )
