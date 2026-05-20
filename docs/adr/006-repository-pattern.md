# ADR-006: Repository pattern over direct ORM access

## Status

Accepted.

## Context

The agent's tool nodes need to persist and retrieve store records and conversation summaries. The naive approach is to import SQLAlchemy session helpers directly into the tool functions and execute queries inline. That works, but it couples agent logic to the ORM, makes unit tests slow (they need a real or fully mocked SQLAlchemy session), and scatters query logic across the codebase. It also tangles two concerns that should be separable: "what does the agent want to know" and "how is that fetched from storage."

## Decision

Introduce a thin repository layer in `src/store_assistant/data/repositories.py`. Each repository (e.g., `StoreRepository`, `SummaryRepository`) exposes async methods at the level of business operations — `save_store(dto)`, `get_store_by_phone(phone)`, `record_summary(summary)` — and returns Pydantic DTOs, not ORM rows. SQLAlchemy is an implementation detail of the repository. Tools depend on a repository protocol, instantiated and injected at graph build time.

## Consequences

Positive: unit tests for tool nodes substitute an in-memory fake repository with no SQLAlchemy involvement at all, which keeps the unit test tier fast and independent of Postgres. Integration tests exercise the real repository against a live database. Query logic is centralised — one place to add an index hint, one place to fix an N+1, one place to add audit-log emission. The agent code reads as business intent: `await stores.save(record)` rather than `await session.execute(insert(Store).values(...))`.

Negative: an extra layer of indirection. For a CRUD-heavy service with a small surface, the repository can feel like ceremony. Mitigated by keeping repositories thin: no business logic, just persistence operations and DTO mapping.

## Alternatives Considered

- **Direct SQLAlchemy in tools**: rejected — testability and coupling concerns above.
- **Active Record pattern (methods on ORM models)**: rejected — couples business logic to ORM lifecycle, makes async session management harder to reason about.
- **Generic repository with a query DSL**: rejected — premature abstraction. We have a small, finite set of queries; concrete methods are clearer than a generic `find(filters, ordering, paging)` API and better-typed.
- **CQRS with separate read and write models**: rejected — overkill for current scope. The repository pattern leaves the door open to split later if traffic patterns demand it.
