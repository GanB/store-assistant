from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from store_assistant.agent.llm_factory import get_llm
from store_assistant.agent.nodes import (
    make_check_termination_node,
    make_generate_summary_node,
    make_llm_node,
)
from store_assistant.agent.state import AgentState
from store_assistant.agent.tools import make_get_store_phone_tool, make_save_store_tool
from store_assistant.config import Settings
from store_assistant.data.repository import (
    MetricsRepository,
    StoreRepository,
    SummaryRepository,
)

NODE_CHECK_TERMINATION = "check_termination"
NODE_LLM = "llm_node"
NODE_TOOLS = "tool_node"
NODE_SUMMARY = "generate_summary"


def _route_after_check_termination(state: AgentState) -> str:
    if state.get("terminated"):
        if state.get("summary"):
            return END
        return NODE_SUMMARY

    msgs = state.get("messages", [])
    if not msgs:
        return NODE_LLM

    last = msgs[-1]
    if isinstance(last, AIMessage):
        if last.tool_calls:
            return NODE_TOOLS
        return END

    return NODE_LLM


def build_graph(
    settings: Settings,
    store_repo: StoreRepository,
    summary_repo: SummaryRepository,
    llm: Any | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    metrics_repo: MetricsRepository | None = None,
) -> Any:
    save_tool = make_save_store_tool(settings, store_repo)
    get_tool = make_get_store_phone_tool(settings, store_repo)
    tools = [save_tool, get_tool]

    if llm is None:
        bound_llm = get_llm(settings, tools=tools)
        summary_llm: Any = get_llm(settings)
    else:
        bound_llm = llm.bind_tools(tools)
        summary_llm = llm

    check_termination = make_check_termination_node(
        settings.app_max_graph_iterations,
        off_scope_threshold=settings.app_off_scope_threshold,
    )
    llm_node = make_llm_node(bound_llm, metrics_repo=metrics_repo)
    tool_node = ToolNode(tools)
    summary_node = make_generate_summary_node(summary_llm, summary_repo)

    builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)
    builder.add_node(NODE_CHECK_TERMINATION, cast(Any, check_termination))
    builder.add_node(NODE_LLM, cast(Any, llm_node))
    builder.add_node(NODE_TOOLS, tool_node)
    builder.add_node(NODE_SUMMARY, cast(Any, summary_node))

    builder.add_edge(START, NODE_CHECK_TERMINATION)
    builder.add_conditional_edges(
        NODE_CHECK_TERMINATION,
        _route_after_check_termination,
        {
            NODE_LLM: NODE_LLM,
            NODE_TOOLS: NODE_TOOLS,
            NODE_SUMMARY: NODE_SUMMARY,
            END: END,
        },
    )
    builder.add_edge(NODE_LLM, NODE_CHECK_TERMINATION)
    builder.add_edge(NODE_TOOLS, NODE_CHECK_TERMINATION)
    builder.add_edge(NODE_SUMMARY, END)

    if checkpointer is None:
        checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


async def stream_graph(
    graph: Any,
    user_input: str,
    thread_id: str,
) -> AsyncIterator[dict[str, Any]]:
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    state_input: dict[str, list[BaseMessage]] = {
        "messages": [HumanMessage(content=user_input)],
    }
    async for event in graph.astream(state_input, config=config, stream_mode="values"):
        yield event
