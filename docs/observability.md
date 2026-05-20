# Observability

The store assistant emits structured JSON logs and (optionally) LangSmith traces. This document describes what is logged, what is redacted, and how to view traces.

## What gets logged

| Layer | Event names | Fields (post-redaction) |
|---|---|---|
| **CLI** | `cli.startup`, `cli.conversation_ended`, `cli.graph_invocation_failed` | `thread_id`, `level`, `timestamp`, exception info on failures |
| **Tracing setup** | `tracing.langsmith.enabled` | `project`, `thread_id` |
| **Agent — intent** | `agent.intent.scope_check`, `agent.intent.termination_detected` | `in_scope`, `off_scope_count`, `reason` (`explicit_utterance` \| `max_off_scope` \| `max_iterations`) |
| **Agent — LLM** | `agent.llm_node.invoked`, `agent.llm_node.response_received` | `iteration`, `has_tool_calls`, `input_tokens`, `output_tokens` |
| **Agent — tools** | `agent.tool_called`, `agent.tool_succeeded`, `agent.tool_failed` | `tool_name`, `arg_keys` (not values), `success`, `error_class`, `reason` |
| **Agent — summary** | `agent.summary.generated`, `agent.summary.persisted` | `length`, `summary_id` |
| **Data — store** | `data.store.save_attempt`, `data.store.saved`, `data.store.duplicate_rejected`, `data.store.lookup_attempt`, `data.store.found`, `data.store.not_found` | `name`, `store_id` |
| **Data — summary** | `data.summary.saved` | `summary_id` |

Every log line carries `timestamp` (ISO-8601, UTC), `level` (`info` / `warning` / `error`), and `thread_id` (bound to the structlog contextvars at REPL startup).

## Redaction policy

A `RedactingProcessor` runs in the structlog pipeline before the JSON renderer. It walks the event dict (recursively into nested dicts and lists) and replaces the value of any key whose name contains one of the following substrings (case-insensitive) with the literal string `[REDACTED]`:

- `passphrase`
- `password`
- `secret`
- `token`
- `api_key`
- `apikey`
- `phone` (matches `phone`, `phone_e164`, etc.)

This is a defence-in-depth measure: log call sites are already written to emit metadata only (tool argument *keys*, not values; store names but not phone numbers). The redactor catches accidents where a sensitive value reaches a log call.

What it does NOT redact:

- Free text in the `event` field (e.g., a custom error message). Call sites must not embed sensitive data in `event`.
- Conversation message bodies. The agent never logs message content; it logs metadata (token counts, tool call presence, iteration number) only.

## How to view LangSmith traces

Tracing is opt-in. Enable by setting both env vars in `.env`:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your-key>
LANGSMITH_PROJECT=store-assistant   # default
```

When enabled, `main.py` exports `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, and `LANGCHAIN_PROJECT` to the process environment at startup. LangChain's auto-tracer picks these up and ships every LLM call, tool call, and chain node to LangSmith.

View traces at: `https://smith.langchain.com/projects` — filter by the project name above. Each conversation appears as a run; expand to see system prompt, model output, tool inputs/outputs, and per-node latency.

Tracing data leaves the local machine when enabled. Do not enable in production traffic that may contain customer PII without a data-processing agreement and review.

## Production additions

For a real production deployment these would be added:

- **Distributed tracing**: OpenTelemetry SDK with W3C trace context propagation across the agent → tool → repository → DB chain. Spans exported to an OTLP collector (Datadog, Honeycomb, or Tempo). LangSmith stays for development; OTel is the primary trace store in production.
- **Metrics**: Prometheus exporter for request rate, p50/p95/p99 latency per node and per tool, tool-call success rate, LLM tokens (input / output / cached) per minute, DB query latency, error rate by class.
- **Log aggregation**: ship JSON logs to CloudWatch Logs (or ELK / Loki). Index on `thread_id`, `event`, and `tool_name` for incident triage.
- **Alerting**: SLO-based alerts (latency p95 over budget, error rate > 1%, LLM provider error rate > 5%). Pages on-call via the standard pager.
- **Self-hosted tracing**: Langfuse running inside the VPC for traces that contain regulated content; LangSmith remains for synthetic / lower-environment traffic only.
