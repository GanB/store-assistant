# ADR-013: Production architecture for regulated FS deployment

## Status

Accepted as the production-deferral plan. Implementation belongs to a real deployment engagement, not this demo.

## Context

The current implementation is a single-tenant local demo: one user, one passphrase, Postgres on the host, LangSmith for tracing, Streamlit on `localhost`. This is intentional — it lets the agent's behaviour and tests be reviewable in a single afternoon. Producing a regulated-FS-grade deployment requires layers that are deliberately out of scope here. This ADR enumerates what those layers are, why each one is non-negotiable in regulated FS, and the rough effort of bringing them in.

The audience is a Director of Applied AI / VP Engineering scoping a production engagement. The decisions are framed as a checklist that survives review by a security architect and a compliance counsel, not as an opinion piece.

The complementary documents:

- [docs/THREAT_MODEL.md](../THREAT_MODEL.md) — what threats this list addresses.
- [ADR-014](014-threat-model-methodology.md) — how the threat model stays current as this plan executes.
- [docs/security.md](../security.md) — the current security posture.

## Decision

The following layers are required for a production deployment in a regulated-financial-services environment. Each item names what to build, why it matters, and a rough effort tier (S = days, M = weeks, L = months). Anything labelled "non-negotiable" cannot be deferred even for a pilot; "deferrable" items can move to a phase 2 with documented risk acceptance.

### Identity and access

- **Per-user authentication via OIDC/SAML against the corporate IdP (M, non-negotiable).** The current "no auth" posture is a demo-only choice. Production needs an authenticated user identity bound to every conversation; the existing `thread_id` becomes `(user_id, thread_id)`. Why it matters: every audit and compliance regime presupposes an identified actor. Without it, retention and DSAR are not satisfiable.
- **Per-user passphrase replacing the global `APP_PASSPHRASE` (S, non-negotiable).** Today's single passphrase is per-deployment. Production stores a salted password hash on each `stores` row (or on a per-user record); retrieval verifies against that. The keyword-matched gate stays the same shape; the secret moves from `Settings` to a row.
- **Multi-tenancy isolation (M, non-negotiable for multi-customer deployments).** Either Postgres row-level security keyed on `tenant_id`, or a per-tenant schema. The agent and repo gain a `tenant_id` invariant on every query. This is the single biggest data-leak risk in a multi-customer deployment.
- **Service-to-service auth for tool calls (M, non-negotiable).** When tools become out-of-process (e.g., a remote retrieval service), inter-service calls go over mTLS or signed JWTs with a short TTL. SPIRE/SPIFFE for identity, cert-manager for issuance.
- **Rate limiting per user (S, non-negotiable).** Bucket on (user_id, action) for: passphrase attempts (e.g., 3/min, lock for 15min after 5 in 5min), total requests (e.g., 60/min), and total token spend (e.g., $X/day). Backed by Redis. The agent today has none of these.

### Data

- **Column-level encryption for `phone_e164` (M, non-negotiable for FS).** `pgcrypto` with KMS-managed DEKs per tenant; the application holds the KMS reference, not the key material. Decrypts at the repository boundary. Backup encryption is automatic when the column is encrypted on disk.
- **PII redaction in summaries before persisting (S, non-negotiable).** A dedicated PII classifier runs on the summary text before `summary_repo.save_summary`. Today's prompt-level instruction ("don't include phone numbers in the summary") is necessary but not sufficient — a model can still leak. Add a layer below the model.
- **Append-only audit table with immutable trigger (M, non-negotiable).** A separate `audit_events` table; INSERT-only privileges for the application role; UPDATE/DELETE blocked by a trigger that raises an exception. Every tool call writes one row with: actor, conversation, tool name, arg keys (not values), tool outcome, timestamp, optional cryptographic chain (`prev_hash`, `this_hash`).
- **Backup encryption with separate KMS key (S, non-negotiable).** Restore credential is split across two custodians; rotation aligned to the KMS rotation policy.
- **Data retention policies aligned to regulatory requirements (M, non-negotiable).** Retention windows are typically 3–7 years for FS; CFPB consent orders may extend that. Implement as a TTL on `audit_events` and `conversation_summaries`, with a deletion job that respects DSARs and legal holds.
- **Right-to-deletion workflow including LangSmith trace purge (M, non-negotiable for CCPA/state-law jurisdictions).** A DSAR-deletion endpoint that walks: every `stores` row by user, every `conversation_summaries` by `thread_id`, every audit event, every backup (logical-deletion-with-tombstone in active storage; restore-and-rewrite for backups beyond the deletion SLA), and every external trace. This is the single hardest item on the list.

### Observability

- **Metrics emitted to Prometheus / Datadog (S, non-negotiable).** Counter: tool-call counts by tool/outcome. Gauge: active conversations. Histogram: tool-call latency p50/p95/p99, LLM-call latency p50/p95/p99. Counter: passphrase failures by user. Counter: token usage (input/output). Counter: hallucinated-tool-call attempts.
- **Structured logs with redaction shipping to a SIEM (S, non-negotiable).** Already present locally via `structlog` + `RedactingProcessor`. Production adds a forwarder (Fluent Bit / Vector) and the SIEM ingestion contract with the security operations centre.
- **Alerting on SLO breaches and security-relevant events (S, non-negotiable).** Alert rules: passphrase-failure spike (>5 in 60s for one user); cost-per-conversation outlier (top 1%); hallucinated-tool-call attempts (any non-zero spike); LLM provider error rate > 1% over 5min; tool error rate > 0.5%.
- **Distributed tracing via OpenTelemetry, with LangSmith reserved for LLM-specific (M, non-negotiable for the audit story).** OTel for everything that isn't an LLM call (HTTP, DB, tool internals); LangSmith (or Langfuse self-hosted) for prompts/completions. The two view together. SaaS LangSmith does not satisfy data-residency for production; self-hosted Langfuse is the production target ([ADR-008](008-langsmith-tracing.md) flags this).
- **SIEM forwarding for security events (S, non-negotiable for FS).** Auth events, passphrase failures, unknown-tool-call attempts, redactor activations, audit-log writes — these are security events, not just operational logs. The SIEM ingestion contract is separate from the operational log contract.

### Reliability

- **Production-grade checkpointer — _now done_.** `MemorySaver` has been replaced with LangGraph's `AsyncPostgresSaver` against a dedicated `agent_state` schema, alembic-managed (no `.setup()` at runtime). Conversations survive restart; fork and right-to-deletion are first-class operations via `app/threads.py`. See [ADR-015](015-checkpointing-strategy.md) for the design and [docs/THREAT_MODEL.md](../THREAT_MODEL.md) §5.7 for the right-to-deletion contract. **Still deferred for production**: multi-region replication of `agent_state`, point-in-time recovery for the schema, append-only enforcement on `agent_state.checkpoints` via DB triggers, and the backup-arm + LangSmith-arm of right-to-deletion (the in-database arm is implemented).
- **Graceful LLM provider degradation (M, non-negotiable).** Retry with exponential backoff and jitter; fallback model (e.g., a smaller/older Claude) when the primary is unavailable; circuit breaker that fails fast after N consecutive errors; queue-with-retry for non-real-time paths. Degradation must produce a structured "service unavailable" response, never an empty completion that the agent then misinterprets.
- **Connection pooling with proper limits (S, non-negotiable).** PgBouncer in transaction-pooling mode in front of Postgres; SQLAlchemy pool sized below PgBouncer's per-database pool; per-user connection cap to prevent a single user from monopolising.
- **Health checks at `/healthz` and `/readyz` (S, non-negotiable).** `/healthz` is liveness (process is up); `/readyz` is readiness (DB reachable, LLM provider reachable, IdP reachable). Kubernetes uses these for restart and traffic shaping.
- **Session timeout and cleanup (S, non-negotiable).** Conversations idle for > 30 min are terminated server-side, summary persisted, checkpointer state purged. Today's "Start new conversation" button is user-driven; production needs the server-driven equivalent.
- **Cost circuit breakers — per-session, per-user, global (M, non-negotiable for production).** Hard caps at each scope. Per-session: $X/conversation, terminates if exceeded. Per-user: $Y/day, blocks new conversations. Global: $Z/min across the deployment, sheds new conversations to a back-pressure queue.

### LLM-specific operational concerns

- **Model version pinning + drift detection (S, non-negotiable).** `APP_LLM_MODEL` is already pinned; production pins to a specific model snapshot. On every model-version change (planned or vendor-pushed), the adversarial eval suite (P11) runs; a non-zero pass-rate regression blocks the upgrade. This is the regression net for prompt-level drift.
- **Adversarial eval suite running on every model swap (S, non-negotiable).** Already implemented via `evals/` (replay) and `make evals-live` (live). Production hooks this into the CI/CD pipeline as a deploy gate.
- **Prompt versioning in source control — prompts as code (S, non-negotiable).** Already implemented (`agent/prompts.py`). Production adds prompt-change PR review by a compliance reviewer for any prompt that affects the customer-facing surface.
- **Output filtering layer (M, non-negotiable for FS).** A small fast classifier runs on every assistant message before it is rendered. Catches: PII echo (esp. phone numbers), profanity, prompt-injection echo, unverified factual claims, and misrepresentations of account state or regulatory entitlements (UDAAP risk). Hits are blocked or rewritten via a fixed template; both modes are logged.
- **Human-in-the-loop escalation on N consecutive guardrail trips (M, deferrable for low-stakes paths).** After N (e.g., 3) consecutive output-classifier blocks within one conversation, escalate to a human agent or terminate with an explanation. Prevents both DoS and frustration loops.
- **Per-environment LLM provider isolation (S, non-negotiable).** Dev keys ≠ staging keys ≠ prod keys; staging traffic does not hit the prod LLM provider account. Cost auditing happens per environment.

### Deployment

- **Containerisation (already done, in-place).** The project ships a multi-stage Dockerfile with a non-root user. Production keeps this and adds image signing (cosign) + SBOM generation at build time + verification at admission.
- **Kubernetes manifests with resource limits + HPA (S, non-negotiable).** Memory and CPU `requests`/`limits` set; HPA on CPU and a custom token-throughput metric; pod disruption budget; node selectors for sensitive workloads.
- **Blue-green or canary deployment for prompt changes (M, non-negotiable for prompt risk).** Prompt edits are riskier than code edits because their effect is non-deterministic. Canary 5% → 25% → 100% over 24h with eval-replay run between stages and live-eval run at the 25% mark.
- **Database migrations in a separate pipeline (S, non-negotiable).** Alembic runs as a one-shot ECS / Argo job before the app deployment is admitted. The application image does not run migrations on startup. The existing migration health check ([ADR-009](009-migration-only-schema-changes.md)) becomes the readiness gate.
- **Secrets management via Vault or AWS Secrets Manager + IRSA (S, non-negotiable for FS).** No `.env` in production containers. Secrets injected via the IdP-bound service account; rotated automatically; access audited.
- **Network policies restricting egress to LLM provider endpoints only (S, non-negotiable).** Default-deny egress; allowlist the LLM provider's IP ranges (or the egress proxy that fronts them); allowlist the tracing backend if self-hosted, none if not. Internal service mesh permits tool-to-tool traffic only on the registered routes.

## Rationale

Regulated FS imposes constraints a generic SaaS deployment doesn't face. The four that drive this list:

1. **Audit and discovery.** Every customer-facing communication is a record. Retention windows are years, not days. The "append-only audit table" + "right-to-deletion workflow" combination addresses this.
2. **PII at-rest and in-flight.** Phone numbers are PII; the conversation transcript is PII-tainted; the summary is PII-tainted. Column-level encryption + no third-party trace egress closes the in-flight side. KMS-encrypted backups close the at-rest side.
3. **UDAAP exposure from hallucination.** A model that fabricates an account fact or product detail creates a deceptive-act exposure regardless of intent. Output filtering + prompt-as-code review + adversarial-eval gate are the layered response.
4. **Data residency and right-to-deletion.** SaaS tracing (LangSmith) cannot satisfy either at production scale. Self-hosted Langfuse + DSAR tooling are the answer.

A non-FS deployment can defer some of this — output filtering may be lighter, audit retention shorter, multi-tenant isolation simpler. The list above is calibrated for FS specifically.

## Consequences

**Positive.** Full posture for a regulated deployment. Every item maps to either a specific compliance regime, a documented threat (see [docs/THREAT_MODEL.md](../THREAT_MODEL.md)), or a known operational hazard.

**Negative.** Significant build effort. The "non-negotiable" tier alone is multi-quarter. The "deferrable" items can be phased.

**Trade-off.** Any subset can be deferred for a pilot or POC, but every deferral comes with a documented risk and a planned closure date. The risk register lives alongside this ADR; it is not implicit.

## Alternatives considered

### Buy vs build — managed agent platforms

Several vendors offer managed conversational-agent platforms that supply some of the layers above out of the box (auth, audit, observability, sometimes evals). Rejected for regulated-FS deployment because:

- **Data residency.** Most managed offerings do not contractually guarantee residency in jurisdictions FS deployments require, or do so only at the highest pricing tier with limits on configurability.
- **Audit format.** Managed-platform audit logs are vendor-defined; aligning them to FS retention/disclosure requirements is not always possible.
- **Custom tools.** This system has bespoke tools that touch a customer-owned database. Managed platforms vary in how cleanly they integrate non-platform-resident state.
- **LLM provider isolation.** Managed offerings often share LLM provider accounts across tenants; FS deployments require dedicated provider accounts for cost-isolation and audit reasons.

A managed offering could be reasonable for a non-FS deployment of similar functionality; for FS, the build is the right call.

### Strip features to ship faster

Considered. Rejected for FS specifically: the auth + audit + PII layers are not optional even for a pilot. A pilot without those is a demo, not a pilot — it cannot be put in front of real consumers without exposure. The agent and tool layers can be intentionally narrow (current state) and the rest of the system can be production-shaped; that's the path this list describes.

### Adopt the FS deployment plan unchanged for non-FS deployments

Rejected as over-engineering. A non-FS deployment can simplify: skip column-level encryption if data is not PII; use a SaaS tracing backend; use a SaaS auth provider directly without an internal IdP. The list here is calibrated for FS — applying it to a non-regulated deployment increases cost without commensurate benefit.
