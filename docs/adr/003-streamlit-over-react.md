# ADR-003: Streamlit for the demo UI

## Status

Accepted.

## Context

The assistant needs a UI surface for the demo. Two main options: build a small React frontend with a Python API behind it, or use Streamlit. A React stack would be the right choice for a real product — it gives full control over UX, supports streaming token output naturally, integrates with design systems, and scales to multi-user, authenticated, audited interactions. None of those properties are load-bearing for a demo whose purpose is to show that the agent and persistence layer work end-to-end.

Time is finite, and every hour spent on JSX, bundlers, and CSS is an hour not spent on agent quality, validation correctness, migration discipline, and tests.

## Decision

Use Streamlit for the demo UI. The agent is the value; the UI is a window onto it. Keep the Streamlit app thin: it instantiates a graph session per Streamlit session, displays a chat history, and routes user input through the agent. Business logic stays in `src/store_assistant/agent` and `src/store_assistant/data` so the same code is reachable from the CLI entrypoint and from any future UI.

## Consequences

Positive: single-language stack (no Node, no bundler, no separate API contract to maintain). Time-to-demo is minutes, not hours. The Streamlit app is replaceable: because business logic lives elsewhere, swapping in React later is a UI rewrite, not a rewrite of the assistant.

Negative: Streamlit's component model is opinionated and constrains UX choices. Multi-user behaviour is limited (Streamlit runs one script per session). Streaming display of partial LLM output requires Streamlit's specific patterns, which differ from typical React/SSE flows. Production hardening (auth, audit, rate limiting) would be awkward inside Streamlit.

## Alternatives Considered

- **React + FastAPI**: the right answer for production. Rejected for current scope on time grounds.
- **Gradio**: similar trade-off to Streamlit; rejected because Streamlit has better integration with structured chat patterns and a cleaner state model for this kind of multi-turn flow.
- **CLI only**: rejected — a chat UI is more demonstrative for a conversational assistant. A CLI entrypoint exists alongside the Streamlit UI for tests and headless use.

The trade-off is explicit: scope discipline over UI polish. Production would replace this with a React frontend.
