# ADR-009: All schema changes go through Alembic migrations

## Status

Accepted.

## Context

There are several ways for a schema change to enter a database: an Alembic migration, a hand-written init SQL file mounted into a container, raw DDL embedded in application startup code, ORM auto-migration (`Base.metadata.create_all`), or an engineer running `psql` against an environment by hand. Mixing these breaks the invariant that the database state is reproducible from the migration history. When that invariant breaks, environments drift, rollbacks become unsafe, and reviewers can no longer audit schema history from one place.

## Decision

**All database schema changes flow through Alembic migrations. No exceptions.** This is enforced by both convention and tooling.

Prohibitions:

- No init SQL files in the repository. The `docker-compose.yml` for Postgres mounts only a named data volume; nothing is mapped to `/docker-entrypoint-initdb.d`.
- No raw DDL (`CREATE TABLE`, `ALTER TABLE`, `DROP TABLE`, `CREATE INDEX`, `CREATE EXTENSION`, `CREATE TYPE`, `GRANT`, etc.) in application code under `src/`.
- No `Base.metadata.create_all()` or any ORM auto-migration on startup. Production code must not create tables.
- No manual `psql` DDL against any environment. If a hotfix is needed, write a migration, commit it, deploy it.

Reference data the application requires for correct operation (lookup tables, default rows, seed data referenced by domain logic) is loaded by Alembic data migrations, not by application bootstrap code.

CI enforcement (planned in a later prompt):

- `alembic check` runs on every PR to detect model/migration drift.
- A grep guard fails the build if raw DDL keywords appear in `src/`.
- The migration round-trip (`alembic downgrade base && alembic upgrade head`) runs in CI to verify reversibility.
- Postgres extensions (`uuid-ossp`, etc.) are created by the first migration, never by an init script.

Workflow:

1. Edit `src/store_assistant/data/models.py`.
2. `make db-migrate-create name="describe_change"` — autogenerate the revision.
3. Open the generated file under `alembic/versions/`, review the diff, edit if needed (autogenerate is a starting point, not a finished migration).
4. `make db-migrate` to apply locally; run the test suite.
5. Commit the model change and the migration file together in one commit.

## Consequences

Positive: single source of truth for schema. Every environment can be rebuilt deterministically. Rollbacks are scripted and tested. PR review can audit schema history in one directory. Drift is impossible if CI checks pass. Onboarding to the database layer is reading `alembic/versions/` in order.

Negative: a small upfront cost on every schema change (write and review the migration). Engineers used to "edit the model and let it auto-create" must adjust. Data migrations require care for production-scale tables (locking, batching), but that care is desirable, not overhead.

## Alternatives Considered

- **Init SQL + Alembic for later changes**: rejected — splits the schema across two systems, defeats the "single source of truth" property.
- **ORM auto-create on startup**: rejected — non-reversible, no diff visibility, breaks as soon as the schema needs anything Alembic doesn't autogenerate.
- **Schema management via dbt or a migration platform**: rejected — heavier tooling than this project warrants and would still need to coexist with application-owned schema.
