# ADR-015: Conversation-state persistence with LangGraph PostgresSaver

## Status

Accepted.

## Context

Until this ADR landed, conversation state lived in-process via `MemorySaver`. That works for a single-developer demo but is wrong for any deployment that touches real users:

- A restart — deploy, autoscaler scale-down, crash, OOM — drops the state of every in-flight conversation.
- For regulated FS (see [ADR-013](013-production-architecture.md), [docs/THREAT_MODEL.md](../THREAT_MODEL.md) §5), conversation transcripts are records subject to retention, discovery, and right-to-deletion. In-process state cannot satisfy any of those.
- Operational features (replay, audit, fork-for-investigation) require querying historical state. The in-memory map exposed by `MemorySaver` doesn't.

The system needs durable, queryable, recovery-friendly conversation state.

## Decision

**Use LangGraph's `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`, backed by a dedicated `agent_state` Postgres schema, schema-managed by Alembic, exposed to the rest of the app through `agent/checkpointer.py` and `threads.py`.**

The decisions in detail:

1. **`AsyncPostgresSaver` over alternatives.** Durable. Survives process and node restart. Backed by Postgres which we already operate. Supports the `aget`/`aput`/`alist`/`aget_state` shape the agent expects, plus the SQL surface needed for fork and right-to-deletion. See "Alternatives" below for the rejected paths.

2. **Dedicated `agent_state` schema in the same Postgres instance.** Clean separation between agent-internal state (LangGraph-defined) and domain data (`stores`, `conversation_summaries`). Lets us back up / restore the two independently when we need to. Makes right-to-deletion a single transaction across both worlds (a `DELETE … WHERE thread_id = $1` against three tables in `agent_state` plus two in `public`).

3. **Schema managed via Alembic, not `.setup()` at runtime.** [ADR-009](009-migration-only-schema-changes.md) forbids DDL outside `alembic/versions/`; that invariant must hold for the checkpointer too. The migration (`0003_add_agent_state_schema.py`) creates the four tables LangGraph's saver expects (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`) and seeds `checkpoint_migrations.v` with the high-water mark of LangGraph's internal migration list, so the saver treats the schema as already provisioned and never executes DDL at runtime. The DDL was discovered by running `setup()` against a throwaway schema and translating the `information_schema` view; the procedure is documented in the migration's docstring. T501 enforces the no-`.setup()` invariant in CI.

4. **Thread management exposed via `app/threads.py`.** `list_threads`, `get_thread_state`, `list_checkpoints`, `fork_thread`, `delete_thread`. Each is a focused, transactional operation. `fork_thread` copies `checkpoints`, `checkpoint_writes`, and `checkpoint_blobs` rows up to and including a named checkpoint into a new `thread_id` in a single transaction — atomic, partial-failure-rolling-back. `delete_thread` is the right-to-deletion mechanic: it scrubs all three checkpoint tables AND the domain rows that share `thread_id` (`stores`, `conversation_summaries`) in one transaction.

5. **Right-to-deletion via `delete_thread()` is the canonical mechanic.** Documented in [docs/THREAT_MODEL.md](../THREAT_MODEL.md) §5.7 ("right-to-deletion mechanics"). The deletion includes every place the user's data lands: agent_state checkpoints, the domain rows. Backup-arm and external-trace-arm (LangSmith) of the deletion are still deferred per ADR-013; the scope of `delete_thread` is "everything in this database".

6. **Fork is a first-class operation, not an emergent feature.** The audit and replay use cases (regulator asks "what would this conversation have looked like if the agent had said Y instead of X at turn 3?") need fork-from-checkpoint. Implementing it as a first-class operation means we don't have to retrofit it later when the regulator asks.

## Rationale

### Why PostgresSaver specifically

- **Durable.** Survives crashes, restarts, autoscaler events.
- **Already-have.** We operate Postgres for the domain tables. Adding Redis or a separate KV store would be an extra moving part for a property Postgres already provides.
- **Queryable.** Replay, audit, and fork all need to walk the checkpoint history. Postgres' SQL surface exposes that without a custom indexer.
- **Cluster-safe.** Multiple agent processes can share the same checkpointer because writes are transactional and per-thread keyed. SqliteSaver's single-writer model would prevent this.

### Why a separate schema

- Clean boundary. Domain code never touches `agent_state` directly; agent code never touches `public.stores` outside the repository.
- Right-to-deletion reasoning. "Delete everything for this thread" is two short query blocks rather than one long join.
- Backup independence. Schema-level `pg_dump` works for either side without entangling.

### Why no `.setup()` at runtime

ADR-009 is the constraint. Runtime DDL is forbidden because:
- It sidesteps migration review.
- It fails ungracefully when the app role doesn't have `CREATE` privileges (which it shouldn't, in production).
- It makes "what's the schema?" a question with no single answer.

The cost is a small ergonomic hit: `make db-migrate` must run before the first app start. The startup health check (T509) catches this with a clear error message — better than the saver silently re-running its own setup.

## Consequences

### Positive

- Conversations survive restarts, deploys, and crashes.
- Right-to-deletion is one transaction across all places the user's data exists in our database.
- Fork enables audit / replay workflows without a schema change later.
- The migration-only invariant (ADR-009) extends naturally to checkpoint state.
- Per-turn cost: single-digit milliseconds added to each tool/LLM transition for the checkpoint write. Measured locally at ~5–15ms; well under the LLM call latency that dominates the turn budget.

### Negative

- **Per-turn DB write.** Every node transition writes a checkpoint. For very high-throughput deployments this is a write-amplification concern. Mitigated by the bounded shape of our state (small messages list + a few scalar fields); production tuning would adjust pool sizing and consider write batching at higher load.
- **Schema is LangGraph-defined.** If LangGraph's `checkpoint_migrations.v` count advances in a future package release, we must add an Alembic migration that mirrors the new DDL and bumps our seeded high-water mark. The pinned LangGraph version in `pyproject.toml` (`langgraph-checkpoint-postgres>=2.0.0`) keeps this from happening silently. **Upgrade procedure**: bump the package, regenerate the discovery (run `setup()` against a throwaway schema, capture the new tables), write the corresponding Alembic migration, bump the seeded `LANGGRAPH_INTERNAL_MIGRATION_COUNT`, run round-trip tests (T508).
- **Schema discovery was a bootstrap, not a maintained API.** Using LangGraph's internal `MIGRATIONS` count to decide whether `setup()` is a no-op depends on internal-but-stable behaviour. A LangGraph release that changes the migration mechanism would force us to revisit. The cost of fighting this would be writing a custom checkpointer that's compatible with the saver's reads but stores data in our own schema — bigger lift than the version-pin discipline.

### Surface decisions

Fork is implemented as a public API but intentionally not surfaced in the demo UI. Forking is a debugging/audit primitive, not an end-user feature; exposing it in a demo UI without context creates confusion. Production deployments would expose fork only to operators via an admin tool. See [ADR-016](016-ui-observability-surface.md) for the broader UI surface decisions.

### Operational notes

- **Backup policy.** The `agent_state` schema must be backed up with the same RPO/RTO as `public`. For regulated FS, both are subject to the same retention. Documented as a deferred item in ADR-013 (Data section).
- **Right-to-deletion.** `delete_thread()` is the canonical mechanic for in-database scrubbing. The full deletion picture also includes the LangSmith trace history (if tracing is enabled — opt-in env var) and any backups; those arms are deferred per ADR-013 and surfaced in [docs/THREAT_MODEL.md](../THREAT_MODEL.md) §5.7.
- **No-FK-by-design.** The `thread_id` column on `stores` and `conversation_summaries` is unconstrained — no FK to `agent_state.checkpoints`. The checkpoint primary key is composite (`thread_id, checkpoint_ns, checkpoint_id`) which doesn't FK cleanly to a single column. The pragmatic choice is the column-only approach with deletion logic enforced in `app/threads.py`.

## Alternatives considered

- **`MemorySaver` (status quo before this ADR).** Rejected — no durability. Restart drops every in-flight conversation. Useful for unit tests where state lifecycle is bounded; wrong for production.
- **`SqliteSaver`.** Rejected — single-writer file lock. Doesn't survive node restart in clustered deployments. Loses the right-to-deletion property if the file rotates. Useful as a development checkpointer but not as the production target.
- **Redis-backed checkpointer.** Rejected — adds an infrastructure dependency we don't otherwise have. Postgres is sufficient. Redis would be the right choice if we needed sub-millisecond checkpoint reads at high throughput; we don't.
- **Custom checkpointer over the existing app DB tables.** Rejected — reinvents LangGraph's primitive and ties us to internal LangGraph state shapes, which would break on every framework upgrade. The framework's saver is the source of truth for state structure; we own the deployment, not the format.
