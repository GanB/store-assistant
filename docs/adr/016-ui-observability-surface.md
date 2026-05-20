# ADR-016: UI observability surface

## Status

Accepted.

## Context

After P11 (eval harness), P12 (threat model), and P13 (checkpointing), the agent had real instrumentation underneath — per-turn tracing in LangSmith, Postgres-backed checkpoints, an offline eval suite — but the UI exposed almost none of it. Anyone walking through the demo had to take it on faith that the platform-quality pieces were there.

The previous UI also leaked LangGraph internals (a "Checkpoint inspector" panel listing per-step ULIDs with Fork buttons) which confused more than it helped: forking is an audit/replay primitive, not an end-user feature, and the per-step view rewards understanding LangGraph's internal state shape rather than the agent's behaviour.

The system needed two related changes in one pass: prune the UI surface that required narration to land, and add a focused observability layer that demonstrates that the agent's unit economics, auditability, and security posture are first-class concerns.

## Decision

The Streamlit UI now ships a bounded, principled observability surface that exposes existing instrumentation rather than introducing new mechanisms.

The surfaces:

1. **Per-turn metadata strip** below each agent message — tokens (with cache breakdown when present), USD cost, latency, the LangGraph node name, and a deep-link to the LangSmith trace when both `LANGSMITH_ORG` and `LANGSMITH_PROJECT` are set. Subdued styling — instrumentation, not content.
2. **Session totals panel** in the sidebar — turns, total in/out tokens, total cost, average latency for the active thread. Hidden when `turns == 0`.
3. **Status panel** with four shallow, cached health checks — DB connected, migrations at head, checkpoint schema visible, tracing env vars set. Each row colours red on failure with a short reason; results cache for 60s.
4. **Active guardrails panel** — six static lines listing the structural guardrails the agent ships with (deterministic phone validation, constant-time passphrase gate, off-scope termination at a configurable threshold, parameterised DB queries, env-only secrets, durable checkpoints). Tests enforce the actual invariants; the badge is the human-readable receipt.
5. **Collapsible state inspector** below the chat input — current node, last checkpoint id, pending tool count, and a redacted JSON view of curated state through `state_projection.project_state_for_inspector`.
6. **LangSmith deep-links** rather than iframe embedding (run-level on the metadata strip, thread-level on the Status panel's tracing row).

The surfaces removed or restricted:

- The "Checkpoint inspector" panel (per-step listing with Fork buttons) is gone. `threads.list_checkpoints` and `threads.fork_thread` remain importable as public APIs for replay/audit use cases (see ADR-015).
- The "Simulate worker restart" button is renamed to **"Reload from last checkpoint"** with a small explanatory caption, and is hidden until the active thread has at least one turn.
- The "End conversation" control is now a single subdued button beneath the chat input, visible from turn 1.
- Sidebar entries for already-terminated conversations show **"View summary"** (read-only) instead of "Resume".

## Rationale

- **Token/cost visibility makes agent unit economics legible.** A reader can see the per-turn cost without leaving the chat and develop calibrated intuitions about the cost-to-quality trade-off. This is a Principal-level concern; making it visible normalises thinking about it across the team.
- **Trace deep-link rather than iframe.** LangSmith's `X-Frame-Options` blocks embedding, and even if it didn't, the auth model would force a login flow within the iframe. A new-tab deep-link is robust to LangSmith's frame policy, opens at the right context, and does not require the operator to be authenticated when the link is rendered.
- **Removing the checkpoint inspector UI is a feature.** The checkpointer is internal mechanism, not user-facing surface. Exposing per-step ULIDs and Fork buttons in a demo trains readers to think about LangGraph rather than the agent. The capability is preserved as a programmatic API; the UI demo no longer surfaces it.
- **Redaction in the state inspector is the security demo.** The inspector exists to show the operator the conversation safely. The redactor masks the passphrase (equality and substring), NANP-shaped phone numbers, and overly-long strings — every one of those is a leak vector that disappears the moment the operator clicks "Inspect agent state".
- **Threshold values come from config, not hardcoded copy.** The off-scope termination threshold renders from `Settings.app_off_scope_threshold` so the badge tracks runtime configuration. T612 grep-locks the UI module against hardcoded threshold copy.

## Consequences

### Positive

- Instrumentation is visible in the first five seconds of using the app — no narration required.
- Per-turn unit economics are queryable, persisted, and auditable. The `turn_metrics` table is a first-class artefact, not a derived view.
- Right-to-deletion (ADR-015) extends naturally to per-turn observability rows; `MetricsRepository.delete_for_thread` is the metrics arm of the deletion.
- The redaction projection forces a curated view through a single chokepoint — nobody can ad-hoc render raw state into the UI without going through the redactor.

### Negative

- **Per-turn DB write cost.** Each LLM call now writes one `turn_metrics` row inside the same session as the store/summary writes for the turn. Locally measured ~1–3 ms per turn; well under the LLM call latency that dominates the budget.
- **UI is denser.** Five sidebar panels and a collapsible inspector. Mitigated by the panel decomposition, subdued typography, and turn-gated visibility (totals panel hides on a fresh thread, reload button hides until there's a turn to reload).
- **Pricing constants need updating on model swap.** `store_assistant.pricing` is the single source of truth; bumping the model in `Settings.app_llm_model` requires a paired update of the pricing constants. Verify against `console.anthropic.com/pricing` on every change.

### Operational notes

- **Session totals cache 5s; health checks cache 60s.** Both are memoised in `st.session_state` via `ui/_cache.py:cache_get_or_compute`. The cache is invalidated by every successful turn (so the totals panel reflects the new row immediately), and on resume / reload-from-checkpoint.
- **LangSmith URL formats are versioned by the LangSmith app.** `langsmith_links` builds canonical `/o/<org>/projects/p/<project>/...` URLs and returns `None` when either `LANGSMITH_ORG` or `LANGSMITH_PROJECT` is unset. T610 pins the contract.
- **State inspector redaction is hardcoded on.** There is no opt-out parameter, by design. T605 enforces; adding such a flag would defeat the inspector's purpose.

## Alternatives considered

- **LangSmith iframe embed** — rejected. `X-Frame-Options` blocks third-party embeds and the auth model would force an extra login flow inside the iframe. Tab-link is robust to both.
- **Login + admin role + RBAC for the inspector** — rejected. Scope creep; outside the current scope. Redaction is the security primitive that lets the inspector be safe by default.
- **Grafana dashboard for token/cost metrics** — rejected. An infrastructure dependency not justified at this scale; the per-turn strip and session totals panel cover the same surface for the demo and for early production.
- **Keeping the checkpoint inspector UI** — rejected. Internal mechanism, leaks LangGraph machinery into the end-user surface. The exposed APIs and tests prove the capability without confusing readers.
- **Per-turn metrics computed on read** — rejected. Computing tokens/cost on read pushes pricing knowledge into the UI; persisting the `cost_usd` once at write keeps `store_assistant.pricing` as the single source of truth and keeps reads cheap.
