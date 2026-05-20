# Security

This document describes the threat model the assistant defends against, where each mitigation lives in the code, and what is explicitly deferred to a production deployment.

## Threat model

| # | Threat | Where it could land | Mitigation in this repo |
|---|---|---|---|
| 1 | **Prompt injection** — attacker convinces the LLM to ignore the system prompt or to call tools with attacker-controlled values | Free-text user input flowing into the model | Tool inputs are typed Pydantic schemas (`agent/tool_schemas.py`); the LLM cannot construct arbitrary calls — only `save_store` and `get_store_phone` are bound. Phones are validated by `validate_and_normalize_phone` before any storage. The system prompt explicitly forbids inventing data. Output is never executed; tool results are structured `*Result` Pydantic models, not free text. |
| 2 | **Secret leakage** in logs, traces, or summaries | Any code that touches API keys, passphrases, or phone numbers | All secrets are typed as `pydantic.SecretStr` in `config.py` so accidental `repr()` returns `**********`. Structured logs run through `RedactingProcessor` (`logging_config.py`) which replaces values for keys matching `passphrase`, `password`, `secret`, `token`, `api_key`, `apikey`, or `phone`. Call sites emit *metadata* only (tool argument keys, not values; store names but not phones). Summary prompt explicitly forbids including phone numbers. `.env` is gitignored; `.env.example` ships placeholder values only. |
| 3 | **SQL injection** | Any database access path | Repositories (`data/repository.py`) use SQLAlchemy 2.0's parameterised expression API exclusively (`select(Store).where(Store.name == name)`). No raw SQL strings, no `text()` with interpolation, no string concatenation. The migration-only rule (ADR-009) keeps DDL out of application code entirely. |
| 4 | **Authorization bypass** of retrieval | `get_store_phone` tool path | The passphrase is compared with `hmac.compare_digest` before any repository call (`agent/tools.py`). On mismatch, the repository is *not* consulted, so a wrong-passphrase response cannot leak whether a record exists (existence oracle is closed). The `test_get_store_phone_with_wrong_passphrase_does_not_call_repo` test enforces this. |
| 5 | **Resource exhaustion via runaway loops** | Tool-call → LLM → tool-call cycles | `APP_MAX_GRAPH_ITERATIONS` (default 20) is checked in `agent/nodes.check_termination_node`; once exceeded the graph routes to summary then `END`. Covered by `test_max_iterations_safeguard_terminates`. LangGraph's own `recursion_limit` is the second line of defence. |
| 6 | **Phone validation bypass** (smuggling unsanitised numbers into storage) | Save flow | `phonenumbers` parses, runs both `is_possible_number` and `is_valid_number`, and emits E.164. The `save_store_tool` only persists the normalised value; an invalid input returns `success=False` and a reprompt cue. `test_save_store_with_invalid_phone_does_not_call_repo` is the regression test. |
| 7 | **Unsanctioned schema changes** (DDL bypassing migrations) | Any new persistence code | The migration-only rule (ADR-009) is enforced by a CI job that greps `src/` for raw DDL keywords (`CREATE TABLE`, `ALTER TABLE`, `CREATE INDEX`, `CREATE EXTENSION`, etc.) and fails the build on any match. Reference and seed data live in migrations. |
| 8 | **Dependency vulnerabilities** | Any third-party package | `pip-audit` runs in CI with `--skip-editable`. New advisories without an upstream fix are listed in `scripts/pip-audit-ignored.txt` with rationale; entries must be removed when a fix becomes available. `bandit` runs at MEDIUM severity threshold. `ruff --select S` runs the flake8-bandit security ruleset. |
| 9 | **Sensitive data in summaries** | `generate_summary_node` | Summary prompt explicitly forbids phone numbers, passphrases, or speculation. Summary text passes through the structured-log redactor when persisted. Production would add a dedicated PII classifier on the summary text before it is written. |
| 10 | **Supply-chain compromise** | Build / CI | `uv.lock` pins every transitive dependency to a content-addressable hash; `uv sync --frozen` is the only path used in CI and Docker. Base images are pinned (`python:3.11-slim`, `postgres:16-alpine`). No image is built from `:latest`. |

## Where each defence lives

```
agent/tools.py           — passphrase gate, hmac.compare_digest, structured tool boundaries
agent/tool_schemas.py    — Pydantic schemas the LLM must conform to for every tool call
agent/state.py + nodes.py + graph.py — explicit state machine, max-iterations safeguard, termination on intent
validation/phone.py      — phone number normalisation + validity check
data/repository.py       — parameterised SQLAlchemy queries only; mapped errors
config.py                — required-env-vars-no-defaults, SecretStr for sensitive values
logging_config.py        — RedactingProcessor in the structlog pipeline
docker/backend.Dockerfile — non-root appuser, multi-stage build, pinned base image
alembic/                 — single source of truth for schema (migration-only rule)
scripts/security_scan.sh — bandit, pip-audit, ruff S, mypy, secret scan, drift check, DDL guard
.github/workflows/ci.yml — runs lint, tests, security scan, migration round-trip and drift, DDL guard
.gitleaks.toml           — gitleaks config that excludes .venv and other generated dirs
```

## What is deferred to production

Listed here for transparency about what is intentionally out-of-scope for the current implementation but would be required in production:

- **WAF** in front of any HTTP surface (Cloudflare / AWS WAF) — bot, DDoS, basic injection filtering before the app sees the request.
- **Rate limiting** per user / per session / per IP, including LLM-call cost-based throttling.
- **Output classifier for compliance** — CFPB UDAAP requirements, applicable state regulations, and any vertical-specific disclosure rules. Block or rewrite outputs that misrepresent facts or omit required disclosures. Out of scope for the current demo; non-negotiable for production in a regulated FS context.
- **PII-aware summary classifier** — dedicated model that flags / redacts customer identifiers before the summary is persisted, beyond the prompt-level instruction in this codebase.
- **Audit log** — every tool call written with full inputs and outputs to a separate, immutable, retention-policy-aware store, with tamper-evident chaining. Required for CFPB review.
- **mTLS** between services in the cluster; client certificates rotated by SPIRE or cert-manager.
- **Network segmentation** — Postgres in private subnets, app pods in a separate subnet, ingress only via load balancer security groups.
- **Secrets in AWS Secrets Manager / Vault** rather than `.env`. Rotated automatically; mounted into containers via IAM roles for service accounts (IRSA), never as plaintext env in the Helm chart.
- **Self-hosted tracing** (Langfuse) for traffic that contains regulated content; LangSmith stays for synthetic / lower-environment flows only.
- **Image signing & SBOM** — cosign signatures verified at deploy by the admission controller; SBOM generated at build time.
- **Penetration testing & red-team review** before go-live, with periodic re-tests on the production deployment.
