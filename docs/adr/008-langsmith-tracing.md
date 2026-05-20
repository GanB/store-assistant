# ADR-008: LangSmith for tracing

## Status

Accepted.

## Context

A LangGraph agent has many moving parts per turn: a model call, possibly several tool calls, validators, and conditional edges. When something goes wrong — a tool argument that fails validation, an unexpected route, a hallucinated phone number — the only way to debug efficiently is to see the full trace: prompt, response, tool inputs, tool outputs, state at each node, latencies, and token counts. Reading those out of structured logs is possible but slow, especially during live demos and debugging sessions.

## Decision

Enable LangSmith tracing for development and demo runs. Configuration is environment-driven:

- `LANGSMITH_TRACING=true` enables the `langsmith` callback handler.
- `LANGSMITH_API_KEY` authenticates.
- `LANGSMITH_PROJECT` (default `store-assistant`) groups traces.

LangGraph emits run events automatically when these are set; no application code changes are required to opt in. Tracing is off by default in production unless explicitly enabled, to avoid shipping conversation contents to a third-party service without a deliberate decision.

## Consequences

Positive: anyone with access can open the LangSmith UI and walk through every conversation, every tool call, every validation failure, with full inputs and outputs. Latency and token usage per node are visible without writing custom metrics. The free tier is sufficient for a demo and for low-volume internal use. Integration is first-class with LangGraph.

Negative: data egress to LangSmith — fine for synthetic test data, not acceptable for production traffic that may contain PII or regulated content. Vendor dependency on LangChain Inc. Without an API key, `langsmith` is silently a no-op, but the dependency footprint is still present.

## Alternatives Considered

- **No tracing, structlog only**: rejected — structlog is in scope for production logs but is not a substitute for hierarchical span-style traces during development.
- **OpenTelemetry from day one**: viable but heavier to bootstrap; planned for production (see `docs/production-readiness.md`).
- **Langfuse self-hosted**: the production target. Functionally similar to LangSmith, but self-hostable inside the production VPC and therefore acceptable for traffic that must not leave the network. Migration is a callback-handler swap; the application emits the same span structure either way.

LangSmith is the development choice; Langfuse (or OpenTelemetry-based tracing) is the production choice.
