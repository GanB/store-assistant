# Project Report — Store Assistant

`tests: 174/174 passing` `evals: 23/23 passing (replay)`

## What this is

A LangGraph-orchestrated conversational agent that captures store records (name,
owner, phone) and lets owners retrieve them later behind a passphrase gate. Built
around a save / retrieve / terminate flow with a multi-turn LLM agent — and
shaped to look like the substrate underneath a production system rather than a
demo. Time-boxed to roughly two weeks of focused work.

The agent runs as an explicit `StateGraph` with named nodes for termination
detection, the LLM call, tool execution, and summary generation. Tools are
typed Pydantic schemas. Phone numbers normalise to E.164 via Google's
`libphonenumber`. Retrieval is gated by a passphrase compared with
`hmac.compare_digest`. State is durable: every node transition writes a
checkpoint to a dedicated `agent_state` schema in Postgres via LangGraph's
`AsyncPostgresSaver`, alembic-managed (no runtime DDL). Termination triggers
a 2–3 sentence summary persisted to `conversation_summaries`.

## Highlights

What separates this from a working scaffold:

- **Eval harness as a first-class artifact.** 23 hand-curated traces across
  seven categories (happy_path, validation, passphrase, termination,
  adversarial, summary, resilience), five scorers, replay + live modes, gated
  in CI. Replay is deterministic, free, runs in ~1s. Design rationale in
  [ADR-012](adr/012-eval-harness.md). Every push to `ganesh-dev` runs the
  replay suite and uploads the report as a CI artifact.
- **Threat model wired into CI.** STRIDE per component plus all twelve OWASP
  LLM Top 10 threats with attack scenarios, mitigations, and an adversarial
  coverage matrix. T406 fails the build if a new threat is added without a
  corresponding eval trace or an explicit "no automated coverage" marker. See
  [docs/THREAT_MODEL.md](THREAT_MODEL.md) and [ADR-014](adr/014-threat-model-methodology.md).
- **Sixteen ADRs** covering every non-trivial decision: orchestration choice,
  storage substrate, validation discipline, tracing strategy, schema-change
  policy, eval methodology, production architecture, threat-model maintenance,
  checkpointing strategy, UI observability surface. Audit-grade rather than
  decorative.
- **Observability surface in the UI.** Every agent message renders a metadata
  strip (input/output tokens with cache breakdown, USD cost, latency, node
  name, LangSmith deep-link). Sidebar surfaces session totals, status panel
  (DB / migrations / checkpoints / tracing), guardrails badge, and a
  redacted state inspector. Agent unit-economics, auditability, and security
  posture are visible in the first five seconds. See [ADR-016](adr/016-ui-observability-surface.md).
- **Production-grade checkpointing.** `AsyncPostgresSaver` against a dedicated
  `agent_state` schema. Conversations survive process restart. `delete_thread()`
  removes every trace of a conversation in one transaction (right-to-deletion
  primitive). Fork + simulate-crash exposed via the operator CLI. Design and
  scope in [ADR-015](adr/015-checkpointing-strategy.md).
- **Redaction projection** on the inspector and structured logs. Passphrase
  is masked at top-level and as a substring (T613 enforces); NANP-shaped phones
  are masked; long strings are truncated. The redactor runs *before* the
  inspector or log renderer sees the value, so a user typing the passphrase
  verbatim into chat does not surface it.
- **CI-enforced posture.** Six jobs: lint + strict mypy, unit tests, Postgres-
  backed integration tests, alembic round-trip + drift check, DDL guard
  (greps `src/` for raw DDL keywords), security scan (bandit + pip-audit +
  ruff S + secret scan + alembic check + DDL guard), and the eval replay job.

## Architecture

A LangGraph `StateGraph` with four nodes (`check_termination`, `llm_node`,
`tool_node`, `generate_summary`) routes each turn based on the latest message
and accumulated state. `check_termination` is idempotent on user-intent
detection — it only fires when the latest message is a `HumanMessage`, so
multi-step tool loops do not double-count off-scope or termination signals.
Tools are typed Pydantic schemas; validation runs before any persistence.
Repositories wrap SQLAlchemy 2.0 async, return DTOs (never raw ORM rows), and
map integrity errors to domain exceptions.

Diagrams (component, save / retrieve / terminate sequences, state machine,
responsibilities table) live in [docs/architecture.md](architecture.md).

## Tech stack

| Choice | Why |
|---|---|
| Python 3.11 | Modern syntax, broad library support, target for production. |
| **uv** | Fast, hermetic, lockfile-first; `uv sync --frozen` reproduces the dependency graph deterministically in CI and Docker. |
| **LangGraph** | Explicit state machine over LangChain's opaque `AgentExecutor`; conditional edges, checkpointing, replay. (ADR-001) |
| **Anthropic Claude Sonnet 4.5** | Schema-faithful tool calling; configurable via `APP_LLM_MODEL`; clean Bedrock migration path. (ADR-004) |
| **PostgreSQL 16** | Production parity for connection pooling, JSONB, real migrations against the target engine. (ADR-002) |
| **SQLAlchemy 2.0 async + asyncpg** | One concurrency model end-to-end. (ADR-007) |
| **Alembic** | Single source of truth for schema; reversible, reviewable, drift-checked in CI. (ADR-009) |
| **Pydantic v2 + pydantic-settings** | Single schema definition reused across env loading, tool args, and DTOs. (ADR-005) |
| **Streamlit** | Time-to-demo UI; React would be the right call for a real product. (ADR-003) |
| **structlog** | JSON logs with `RedactingProcessor` and contextvars merge for `thread_id`. |
| **LangSmith** | Free, opt-in, native LangGraph integration for development; Langfuse self-hosted is the production target. (ADR-008) |

## Tests and evals

| Layer | Tests | Speed | External deps | Purpose |
|---|---:|---|---|---|
| Unit (`tests/unit`) | 143 | <2s | none | Phone validation, repository CRUD against in-memory SQLite, tool functions with mocked repo, redaction, metrics, pricing, intents, health checks, UI invariants, documentation gates. |
| Integration (`tests/integration`) | 31 | <3s | none (SQLite in-mem) | Full LangGraph + repo with mocked LLM. Save / retrieve / termination / mixed flows; checkpointing; metrics persistence; eval-runner harness. |
| E2E live (`tests/e2e`) | 1 | ~10s | Anthropic API | Real Claude call against the full graph. Opt-in via `LIVE_TESTS=1`. |

**Eval harness** lives in `evals/`. 23 hand-curated traces in
`evals/dataset/traces.jsonl` (never LLM-generated — that would defeat the
ground-truth contract). Five scorers: `task_completion`, `refusal`,
`passphrase_leak`, `summary_fidelity`, `phone_validation`. Replay-mode reports
land at `reports/evals/<UTC-timestamp>.md` with a per-trace breakdown and a
category × scorer summary table. The most recent report shows
`evals: 23/23 passing (replay)` across all categories.

## Trade-offs and deferred items

What was scoped out, with reasoning:

| Deferred | Why |
|---|---|
| React product UI | Streamlit is intentionally a demo surface (ADR-003). React is the right call for a real product; building it would burn time on JSX/bundlers without changing the agent story. |
| Per-store passphrases | Single global `APP_PASSPHRASE` exercises the constant-time-comparison pattern. Production would store a salted hash per store row; the gate shape stays the same. |
| Owner field as a separate column | The current scope covers name + phone; owner can be added in a later migration without breaking compatibility. |
| LLM-based intent classifier | The keyword-based off-scope detector is fast, deterministic, testable. The wiring (`classify_scope`) for a Haiku-backed classifier is implemented but unwired. |
| Output compliance classifier (TSR / CFPB) | A regulated-FS production must add a classifier in front of every assistant message; this is a project of its own and is documented as the most important production add. |
| Multi-tenant authentication, audit log to immutable store, WAF, rate limiting, mTLS, AWS Secrets Manager, OpenTelemetry / Prometheus / SIEM forwarding | Production posture; documented in [ADR-013](adr/013-production-architecture.md) with effort tiers. |
| Postgres-backed integration tests | Integration tests use in-memory SQLite for speed. A `INTEGRATION_DB=postgres` gate would catch JSONB / extension issues that SQLite hides. |
| Streaming UI output | Streamlit `st.write_stream` over `astream_events` would feel snappier on long replies. |

## What I'd do for production

[ADR-013](adr/013-production-architecture.md) is the full checklist; the short
version, organised by layer:

**Identity and access.** Per-user OIDC/SAML against the corporate IdP, replacing
the single-passphrase model with per-user salted-hashed credentials on `stores`
rows; multi-tenant isolation via Postgres RLS keyed on `tenant_id`; service-to-
service mTLS for tool calls; per-user rate limiting (passphrase attempts, total
requests, token spend) backed by Redis.

**Data.** Column-level encryption for `phone_e164` via `pgcrypto` + KMS-managed
DEKs per tenant; PII redaction on summaries before persisting (a classifier
below the model, not just a prompt instruction); append-only audit table with
an immutable trigger and an optional cryptographic hash chain; a DSAR right-to-
deletion workflow that walks every store row, summary, audit event, backup, and
external trace.

**Observability.** Prometheus / Datadog metrics (tool-call counts and latency
histograms, active conversations, passphrase failures, hallucinated-tool-call
attempts, token spend); structured logs with redaction shipping to a SIEM;
distributed tracing via OpenTelemetry with LangSmith (or self-hosted Langfuse)
reserved for prompts/completions; alerting on SLO breaches and security events.

**Reliability.** Graceful LLM-provider degradation (retry-with-jitter,
fallback model, circuit breaker, queue-with-retry); PgBouncer transaction
pooling; `/healthz` + `/readyz` with real downstream checks; server-driven
session timeout; cost circuit breakers at per-session, per-user, and global
scopes.

**LLM operations.** Adversarial eval suite as a deploy gate on every model
swap (already implemented locally; production hooks it into CI/CD); prompt
versioning + compliance review on customer-facing prompt changes; an output
filtering layer for PII echo, prompt-injection echo, UDAAP risk; per-environment
LLM provider isolation.

**Deployment.** Image signing + SBOM at build, verification at admission;
Kubernetes manifests with resource limits and HPA on a custom token-throughput
metric; blue/green or canary for prompt changes with eval-replay between
stages; alembic as a one-shot job ahead of app deployment; Vault or AWS
Secrets Manager + IRSA — no `.env` in production; default-deny egress
allowlisting only the LLM provider (or the egress proxy that fronts it).

**LLM hosting target.** Anthropic API → **Bedrock**: same Claude family, same
prompts, AWS-managed endpoint inside the production VPC, IAM-based access,
CloudTrail audit, Bedrock Guardrails for output policy enforcement. The
application code changes only the client constructor.

## How to explore this project

Suggested order:

1. **Read this report first**, then [docs/DEMO.md](DEMO.md). DEMO.md is six
   numbered scenarios — each names the flow, the expected behaviour, and the
   talking point that ties what's shown to the regulated-FS posture story.
   Run them in order.
2. **Skim the threat model** ([docs/THREAT_MODEL.md](THREAT_MODEL.md)) — the
   adversarial-coverage matrix in Section 4 is the contract that T406 enforces
   against `evals/dataset/traces.jsonl`.
3. **Open ADR-013** ([adr/013-production-architecture.md](adr/013-production-architecture.md))
   to see the production scoping: every layer, every effort tier, every
   non-negotiable item.
4. **Open the eval harness**:
   ```bash
   make evals                          # ~1s, deterministic
   cat reports/evals/<latest>.md       # category × scorer summary, per-trace results
   ```
5. **Run the demo**:
   ```bash
   cp .env.example .env                # fill ANTHROPIC_API_KEY + APP_PASSPHRASE
   make install
   make db-up && make db-migrate
   make run-ui                         # Streamlit at http://localhost:8501
   ```
   Walk through DEMO.md scenarios D1–D6. Watch the per-turn metadata strip and
   the sidebar status panel.
6. **Look at the agent code** in this order:
   - `src/store_assistant/agent/graph.py` — the StateGraph wiring.
   - `src/store_assistant/agent/nodes.py` — node implementations.
   - `src/store_assistant/agent/tools.py` — passphrase gate + tool factories.
   - `src/store_assistant/agent/intents.py` — termination + off-scope detection.
   - `src/store_assistant/data/repository.py` — async repository with
     integrity-error mapping.
7. **Run the test suite**: `make test` (174 tests in ~5s).
8. **Run the security scan**: `make security` (bandit + pip-audit + ruff S +
   strict mypy + secret scan + alembic check + DDL guard).

The signal points: the eval harness as a first-class artifact, the threat
model wired into CI as a contract rather than a document, the discipline of
sixteen ADRs, the observability surface visible in the first five seconds of
the UI, and the production-deferral plan that is concrete enough to staff.
