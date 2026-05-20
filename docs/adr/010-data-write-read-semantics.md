# ADR-010: Data write/read semantics — duplicates, lookup case, whitespace

## Status

Accepted.

## Context

The store assistant exposes two data operations: `save_store(name, phone)` and `get_store_phone(name, passphrase)`. Three small but consequential design questions sit underneath them:

1. **What happens when `save_store` is called with a name that already exists?** The two reasonable answers are (a) reject with a typed error, or (b) silently upsert and overwrite the prior phone. They have very different posture.
2. **Is name lookup case-sensitive?** A user typing `bob's auto` after saving `Bob's Auto` reasonably expects to get the record back.
3. **Should leading / trailing whitespace on the name be preserved or stripped?** The user usually doesn't intend it.

For a system gated by a passphrase and ultimately destined for a regulated environment, the answers should not be defaults — they should be deliberate.

## Decision

1. **Duplicate save → reject, never upsert.** `StoreRepository.save_store` raises `StoreAlreadyExistsError` on the unique-constraint violation. The tool surfaces a "store already exists" message; the agent asks the user to confirm before any further action. There is no path through the agent today that overwrites an existing record's phone.
2. **Name lookup is case-insensitive.** `get_store_by_name` and `exists` compare `lower(stored_name) == lower(query_name)` using SQLAlchemy's `func.lower()` — produced inside the parameterised query, no string interpolation. The unique constraint on `stores.name` remains exact-match, so saving `Bob's Auto` and `bob's auto` is still a constraint violation (rejected per #1).
3. **Whitespace on save is stripped.** `save_store_tool` calls `name.strip()` before validation and persistence. Lookup also strips. The agent never sees `"  Bob's Auto  "` as a different store from `"Bob's Auto"`.

## Consequences

Positive:

- **Audit clarity** — every save is either a new record or an explicit rejection. No silent overwrites means no "the phone changed and we don't know when" gaps in the audit log.
- **Symmetric duplicate handling** — saving and retrieving treat case the same way (lookup matches across cases; save still rejects across cases). A user can't accidentally create two records that look identical to the eye but differ by case.
- **No injection risk from the case-insensitive change** — `func.lower()` is a column-side function applied inside a parameterised query; there is no string interpolation, no chance of SQL injection.

Negative:

- **`func.lower()` doesn't use the existing index on `name`.** At the current demo's data volume this is irrelevant. In production the right fix is a functional index: `CREATE INDEX ix_stores_name_lower ON stores (lower(name))`. Documented as a future migration.
- **Rejection over upsert is more friction for the user** when they legitimately want to update a phone. That workflow doesn't exist today and is intentionally out of scope; an `update_store_phone` tool gated by passphrase would be the right addition rather than relaxing this rule.

## Alternatives Considered

- **Upsert on duplicate name**: rejected. In a regulated context, silent overwrites are a data-integrity hazard. The right shape is "reject duplicates; introduce an explicit update path when needed."
- **Store names already lowercased**: rejected. Loses the user's display casing. Requires either a separate `display_name` column or accepting that all retrievals echo back lower-case — a UX cost without commensurate benefit.
- **Trigram / fuzzy matching on lookup**: rejected for the retrieve path. A passphrase-gated retrieval that also accepts approximate matches widens the side channel — a typo against a near-neighbour name leaks information. Typo handling is delegated to the LLM via a system-prompt rule that asks the user to confirm spelling on `not_found`.
