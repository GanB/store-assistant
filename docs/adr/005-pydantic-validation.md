# ADR-005: Pydantic v2 for validation, settings, and tool schemas

## Status

Accepted.

## Context

The system has three places where typed validation matters: configuration loaded from environment variables, the argument schemas the LLM sees when it calls a tool, and the data transfer objects (DTOs) crossing the boundary between the agent layer, the repository, and the UI. These could be implemented with three different mechanisms (e.g., `os.environ` parsing for config, JSON Schema dictionaries for tool arguments, dataclasses for DTOs), but that fragments validation and forces three separate places to keep in sync as the schema evolves.

## Decision

Use Pydantic v2 throughout:

- **Settings** (`store_assistant.config.Settings`) extend `pydantic_settings.BaseSettings`. Environment variables are loaded, type-coerced, and validated at startup. Secrets use `SecretStr` to keep them out of accidental `repr` output and logs.
- **Tool argument schemas** are Pydantic models passed directly to LangChain's `@tool`/`StructuredTool` interface. LangChain converts these to the JSON Schema the LLM sees. The same model is used to validate the LLM's emitted arguments before the tool body runs.
- **DTOs** at the agent ↔ repository boundary are Pydantic models. The repository accepts and returns DTOs, never raw ORM objects, so business code is not coupled to SQLAlchemy session lifetime.

## Consequences

Positive: one schema definition is the source of truth across LLM-facing JSON Schema, runtime validation, type hints, and IDE autocomplete. Pydantic v2 is fast (Rust core) and the validation errors are precise enough to feed back into the LLM as structured feedback when it emits malformed arguments. `SecretStr` and `Field(repr=False)` reduce the chance of leaking secrets into logs or traces.

Negative: Pydantic models are slightly heavier than dataclasses at construction time, which is irrelevant at our request rates. Some coupling to Pydantic's API across many modules — a future swap to attrs or msgspec would be a project-wide change.

## Alternatives Considered

- **Dataclasses + manual validation**: rejected — duplicates the JSON Schema for tools, no native env loading.
- **attrs / msgspec**: rejected — faster but lacks the `pydantic_settings` ecosystem and the LangChain tool integration that already understands Pydantic models.
- **TypedDicts**: rejected — no runtime validation, which matters when LLM-emitted arguments enter the system.
