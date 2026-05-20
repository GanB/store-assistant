# ADR-001: LangGraph for agent orchestration

## Status

Accepted.

## Context

The store assistant is a stateful, multi-turn conversation with branching control flow. It must collect a store name, an owner name, and a phone number; validate the phone; gate retrieval on a passphrase; detect a termination intent; and emit a structured summary on exit. Across turns the agent needs to know what slots are filled, what was just validated, whether the caller has authenticated for retrieval, and whether termination has been signalled. This is a state machine, not a single prompt-and-tool loop.

LangChain's classic `AgentExecutor` is a fixed loop: model → tool → model → tool. Conditional routing, explicit per-step state, replay from a midpoint, and graph-level introspection are bolted on rather than first-class. For a project that needs deterministic flow, traceable transitions, and clean test seams at each transition, that loop is the wrong shape.

## Decision

Use LangGraph as the orchestration layer. Model the agent as a `StateGraph` with a typed `AgentState`, explicit nodes for the LLM call, tool execution, validation, and termination, and conditional edges that route based on state (e.g., "passphrase verified?" → retrieval node vs. re-prompt node). Bound the graph with `recursion_limit` from `APP_MAX_GRAPH_ITERATIONS` to prevent runaway loops.

LangChain primitives (`ChatAnthropic`, tool decorators, message types, output parsers) are still used inside nodes — orchestration is LangGraph, model and tool plumbing remain LangChain.

## Consequences

Positive: state transitions are explicit and testable in isolation. Conditional edges replace fragile prompt-engineered routing. Built-in checkpointing enables replay from any node, which is invaluable for debugging and for LangSmith trace inspection. The graph is the documentation: a reader can read `build_graph()` and see the full control flow.

Negative: more upfront wiring than `AgentExecutor`. Slightly steeper learning curve for contributors who only know LangChain. The graph builder becomes a chokepoint that needs careful review on changes.

## Alternatives Considered

- **LangChain `AgentExecutor`**: rejected — opaque control flow, no clean place to enforce passphrase gating, harder to test transitions in isolation.
- **Hand-rolled state machine in plain Python**: rejected — would re-implement checkpointing, message reduction, and LangSmith integration that LangGraph already provides.
- **Semantic Kernel / Haystack agents**: rejected — non-native fit with the LangChain tool/prompt ecosystem this project uses.
