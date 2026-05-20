# ADR-002: PostgreSQL in Docker over SQLite

## Status

Accepted.

## Context

The project requires persistence for store records and conversation summaries. SQLite would technically satisfy the functional requirement: a single-file database, zero-config, easy to ship. But the goal of this project is to demonstrate production-shaped engineering for a financial-services platform stack. The likely production database for that target is PostgreSQL (RDS or Aurora), and the gap between SQLite and Postgres is exactly the kind of gap where demo code breaks on the first day in a real environment: connection pooling semantics, transaction isolation, JSONB indexing, concurrent writes, migration tooling against a real engine.

## Decision

Run PostgreSQL 16 in Docker for local development, accessed via SQLAlchemy 2.0 async with the `asyncpg` driver. Schema is owned by Alembic. Everything an engineer would write against the database in production — pool sizing, isolation levels, JSONB columns for summary metadata, index choices, migration ordering — is exercised in development against the same engine.

## Consequences

Positive: production parity. Code that runs locally against Postgres 16 behaves the same against RDS Postgres 16. JSONB is available for the summary payload without retrofitting later. Connection pool tuning is realistic from day one. Alembic migrations are tested against the target engine, so DDL surprises (e.g., extension installation, lock-taking ALTERs) surface early. Multi-process and multi-connection scenarios work correctly, which matters as soon as the Streamlit demo and a CLI run against the same DB.

Negative: heavier setup than SQLite. Engineers need Docker running. First-time bring-up cost is `make db-up && make db-migrate` rather than zero. CI needs a Postgres service for integration tests. Disk usage for the volume is non-trivial.

## Alternatives Considered

- **SQLite (file-based)**: rejected for the parity reasons above. Acceptable for a smaller scope but would obscure the database layer's behaviour in the target environment.
- **Postgres natively installed**: rejected — version drift across contributor machines, no clean teardown, harder to wire into CI.
- **DuckDB**: rejected — analytics-oriented, not a fit for transactional store record CRUD.

This is a deliberate, eyes-open over-engineer for the current scope. The trade-off is justified by demonstrating familiarity with the production stack rather than the smallest possible solution.
