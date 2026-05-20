# Store Assistant

A LangGraph-orchestrated conversational agent that captures store records (name,
owner, phone) and retrieves them later behind a passphrase gate. Records are saved
through structured tool calls, validated, and persisted in PostgreSQL via Alembic-
managed schema. This repository demonstrates
production-shaped engineering for a regulated-financial-services context: an
explicit state machine, real persistence, an offline eval harness, a STRIDE +
OWASP-LLM-Top-10 threat model, and a CI pipeline that runs lint, unit /
integration tests, migration round-trip, DDL guard, and security scan on every
push.

## Quickstart

```bash
git clone <url> && cd store-assistant
cp .env.example .env                # then fill ANTHROPIC_API_KEY and APP_PASSPHRASE
make install                        # uv-managed venv, ~30s on first run
make db-up && make db-migrate       # Postgres in Docker + initial migration
make run-ui                         # Streamlit demo at http://localhost:8501
```

CLI surface: `make run-cli`. Full container stack: `docker compose up postgres ui`.

## What's inside

A reader landing here for the first time should read these in order:

- [docs/REPORT.md](docs/REPORT.md) — executive summary, scope traceability,
  trade-offs, production deferrals. The single document that frames the work.
- [docs/DEMO.md](docs/DEMO.md) — six demo scenarios; each names the talking
  point that ties what's shown to the posture story.
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — STRIDE per component, twelve
  LLM-specific threats with attack scenarios, eval-coverage matrix
  (CI-enforced), and regulated-FS compliance posture.
- [docs/adr/](docs/adr/) — sixteen Architecture Decision Records covering
  every non-trivial design choice. ADR-013 is the production-architecture
  checklist; ADR-015 is the checkpointing design; ADR-016 is the UI
  observability surface.
- [docs/architecture.md](docs/architecture.md) — component diagram, save /
  retrieve / terminate sequence diagrams, state machine, responsibilities table.
- [docs/observability.md](docs/observability.md) — structured-event catalogue
  and redaction policy.
- [evals/](evals/) — 23-trace versioned dataset, five scorers, replay + live
  modes; gates every push to `ganesh-dev`.
- [src/store_assistant/](src/store_assistant/) — agent (`agent/`), data layer
  (`data/`), validation (`validation/`), config, logging, threads CLI, UI
  (`ui/streamlit_app.py`).

## Tests and evals

```bash
make test                   # 174 unit + integration tests, in-memory engine, ~5s
make test-live              # opt-in: real Anthropic API call, ~10s, costs cents
make evals                  # 23 replay traces, deterministic, ~1s
make evals-live             # opt-in: live LangGraph + Anthropic API
make security               # bandit + pip-audit + ruff S + mypy + secrets + DDL guard
```

Tests verify correctness; evals verify behavioural quality (refusal posture,
passphrase non-leak, completion, summary fidelity, validation reprompts).
Replay-mode evals run on every push via the CI `evals` job; live mode never
runs in CI by design.

## Project status

Latest run:

- **Tests**: 174 / 174 passing (`make test`).
- **Evals**: 23 / 23 passing in replay mode (`make evals`); the most recent
  report is in [`reports/evals/`](reports/evals/).
- **CI**: six-job pipeline on `.github/workflows/ci.yml` — lint, unit,
  integration, migration round-trip + drift, DDL guard, security scan, evals.

For the full write-up — what was built, what was deferred, how to evaluate the
project, and what would change for production — see
[docs/REPORT.md](docs/REPORT.md).
