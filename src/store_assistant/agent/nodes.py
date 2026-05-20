from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from store_assistant.agent.intents import (
    detect_off_scope_utterance,
    detect_termination_utterance,
)
from store_assistant.agent.prompts import SUMMARY_PROMPT, SYSTEM_PROMPT
from store_assistant.agent.state import AgentState
from store_assistant.data.repository import MetricsRepository, SummaryRepository
from store_assistant.logging_config import get_logger
from store_assistant.metrics import ToolCallSummary, TurnMetrics
from store_assistant.pricing import compute_cost

REASON_EXPLICIT_UTTERANCE = "explicit_utterance"
REASON_MAX_OFF_SCOPE = "max_off_scope"
REASON_MAX_ITERATIONS = "max_iterations"

NodeFn = Callable[[AgentState], Awaitable[dict[str, Any]]]
NodeFnWithConfig = Callable[[AgentState, RunnableConfig], Awaitable[dict[str, Any]]]

_log = get_logger("store_assistant.agent")


def _last_message(state: AgentState) -> BaseMessage | None:
    msgs = state.get("messages", [])
    return msgs[-1] if msgs else None


def make_check_termination_node(
    max_iterations: int, off_scope_threshold: int = 3
) -> NodeFn:
    async def check_termination(state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = {}

        if state.get("iteration_count", 0) >= max_iterations:
            updates["terminated"] = True
            _log.info(
                "agent.intent.termination_detected",
                reason=REASON_MAX_ITERATIONS,
                iteration_count=state.get("iteration_count", 0),
            )
            return updates

        last = _last_message(state)
        if isinstance(last, HumanMessage):
            text = last.content if isinstance(last.content, str) else str(last.content)
            if detect_termination_utterance(text):
                updates["terminated"] = True
                _log.info(
                    "agent.intent.termination_detected",
                    reason=REASON_EXPLICIT_UTTERANCE,
                )
                return updates
            if detect_off_scope_utterance(text):
                new_count = state.get("off_scope_count", 0) + 1
                updates["off_scope_count"] = new_count
                _log.info(
                    "agent.intent.scope_check",
                    in_scope=False,
                    off_scope_count=new_count,
                )
                if new_count >= off_scope_threshold:
                    updates["terminated"] = True
                    _log.info(
                        "agent.intent.termination_detected",
                        reason=REASON_MAX_OFF_SCOPE,
                        off_scope_count=new_count,
                    )

        return updates

    return check_termination


def make_llm_node(
    llm_with_tools: Any,
    metrics_repo: MetricsRepository | None = None,
) -> NodeFnWithConfig:
    """LLM node with optional per-turn metrics capture.

    Tests that don't care about persistence pass metrics_repo=None and the
    node behaves exactly as before. Production wiring (graph built from the
    streamlit / cli path) supplies a repo bound to the same session as the
    other writers for the turn, so the metrics row commits in the same DB
    interaction window as the store/summary writes.
    """

    async def llm_node(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, Any]:
        new_iter = state.get("iteration_count", 0) + 1
        _log.info("agent.llm_node.invoked", iteration=new_iter)

        messages: list[BaseMessage] = [
            SystemMessage(content=SYSTEM_PROMPT),
            *state.get("messages", []),
        ]
        t0 = time.monotonic()
        response = await llm_with_tools.ainvoke(messages)
        latency_ms = int((time.monotonic() - t0) * 1000)

        usage = getattr(response, "usage_metadata", None) or {}
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        cache_details = usage.get("input_token_details") or {}
        cache_read = int(cache_details.get("cache_read") or 0)
        cache_create = int(cache_details.get("cache_creation") or 0)

        tool_call_summaries = _extract_tool_call_summaries(response)
        _log.info(
            "agent.llm_node.response_received",
            iteration=new_iter,
            has_tool_calls=bool(tool_call_summaries),
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

        if metrics_repo is not None:
            await _persist_turn_metrics(
                metrics_repo,
                config=config,
                turn_index=new_iter,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_create,
                latency_ms=latency_ms,
                tool_calls=tool_call_summaries,
            )

        return {
            "messages": [response],
            "iteration_count": new_iter,
        }

    return llm_node


def _extract_tool_call_summaries(response: Any) -> list[ToolCallSummary]:
    """Build ToolCallSummary instances from an LLM response.

    Records the tool's name and the *keys* of its argument payload — never
    the values. Argument values can carry the user passphrase (the
    get_store_phone tool has a `passphrase` argument); see metrics.py for
    the wider invariant. Duration here is recorded as 0 because the LLM
    response only describes which tool calls were *requested*; the actual
    tool execution happens in a separate node and isn't measured by this
    record.
    """
    raw_calls = getattr(response, "tool_calls", None) or []
    out: list[ToolCallSummary] = []
    for call in raw_calls:
        if isinstance(call, dict):
            name = str(call.get("name", "unknown"))
            args = call.get("args") or {}
        else:
            name = str(getattr(call, "name", "unknown"))
            args = getattr(call, "args", {}) or {}
        arg_keys = sorted(args.keys()) if isinstance(args, dict) else []
        out.append(
            ToolCallSummary(
                tool_name=name,
                arg_keys=arg_keys,
                duration_ms=0,
                success=True,
            )
        )
    return out


async def _persist_turn_metrics(
    metrics_repo: MetricsRepository,
    *,
    config: RunnableConfig,
    turn_index: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    latency_ms: int,
    tool_calls: list[ToolCallSummary],
) -> None:
    configurable = config.get("configurable") or {}
    thread_id = str(configurable.get("thread_id") or "")
    if not thread_id:
        # Without a thread_id we can't key the row; skip rather than write
        # an orphan record.
        _log.info("agent.metrics.skipped_no_thread_id", turn_index=turn_index)
        return
    # checkpoint_id is the parent checkpoint LangGraph routed in on; if not
    # present, we leave None and the schema accepts it.
    checkpoint_id = str(configurable.get("checkpoint_id") or "") or ""
    run_id_raw = config.get("run_id")
    langsmith_run_id = str(run_id_raw) if run_id_raw else None

    metrics = TurnMetrics(
        turn_index=turn_index,
        thread_id=thread_id,
        checkpoint_id=checkpoint_id,
        node_name="llm_node",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cost_usd=compute_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        ),
        latency_ms=latency_ms,
        tool_calls=tool_calls,
        langsmith_run_id=langsmith_run_id,
    )
    await metrics_repo.record_turn(metrics)


def make_generate_summary_node(
    summary_llm: Any,
    summary_repo: SummaryRepository,
) -> NodeFnWithConfig:
    async def generate_summary(
        state: AgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        thread_id = config.get("configurable", {}).get("thread_id")

        prior = state.get("messages", [])
        prompt: list[BaseMessage] = [SystemMessage(content=SUMMARY_PROMPT), *prior]

        response = await summary_llm.ainvoke(prompt)
        text = response.content if isinstance(response.content, str) else str(response.content)
        _log.info("agent.summary.generated", length=len(text))

        saved = await summary_repo.save_summary(summary_text=text, thread_id=thread_id)
        _log.info(
            "agent.summary.persisted",
            summary_id=str(saved.id),
            thread_id=thread_id,
        )

        farewell = AIMessage(content="Conversation summary saved. Goodbye.")
        return {"summary": text, "messages": [farewell]}

    return generate_summary
