# ADR-007: Async LLM and async DB throughout

## Status

Accepted.

## Context

The store assistant's request path is dominated by two kinds of I/O: a network call to Anthropic for each LLM step (several seconds, give or take), and database calls to Postgres (sub-millisecond to a few milliseconds). Mixing async LLM calls with a synchronous database driver tends to push DB calls onto a thread pool, complicating session lifetime, propagating context badly, and making structured concurrency awkward. A consistent concurrency model across the stack avoids those failure modes.

## Decision

Use async end-to-end:

- LLM calls go through `langchain-anthropic`'s async client, awaited inside LangGraph nodes.
- The database is accessed via SQLAlchemy 2.0's async API with the `asyncpg` driver. The repository layer exposes only `async def` methods. Sessions are created per request scope (or per graph invocation) and closed via async context managers.
- Alembic uses the async template (configured in `alembic/env.py`) so migrations run against the same driver as the app.

The Streamlit UI bridges async ↔ sync at the edge: Streamlit itself is synchronous, so user-input handling calls `asyncio.run` (or an equivalent loop bridge) to drive the async graph for one turn. Internal code remains async.

## Consequences

Positive: a single concurrency model. Cancellation and timeouts compose properly via `asyncio.timeout`. DB and LLM I/O can be interleaved cleanly when needed (e.g., kicking off a "find by phone" query in parallel with a streamed model call). asyncpg is the fastest Postgres driver available to Python and pairs well with SQLAlchemy 2.0's async sessions. Pool pressure is bounded by event-loop concurrency rather than thread count.

Negative: async leaks into nearly every layer. Synchronous helpers must be rare and contained. Streamlit's bridge to async is awkward and adds a small per-turn overhead. Some libraries (e.g., older test fixtures) require pytest-asyncio adjustments. Stack traces are deeper; profiling async code requires care.

## Alternatives Considered

- **Sync DB + async LLM**: rejected — context propagation across thread pool boundaries is a recurring source of bugs (session bound to wrong loop, context vars lost).
- **Sync everywhere with thread pools for LLM**: rejected — wastes the LLM-call-bound nature of the workload, where async shines.
- **trio / anyio**: rejected — `asyncio` integrates more directly with the libraries we already depend on.
