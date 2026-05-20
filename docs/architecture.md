# Architecture

## Overview

The store assistant is a LangGraph-orchestrated conversational agent that captures store records (name, owner, phone) and lets owners retrieve them later behind a passphrase gate. A user speaks to the agent through Streamlit (or the CLI), the LangGraph state machine routes each turn through a reasoning node and a tool node, validators normalise input, a thin async repository persists data to PostgreSQL via SQLAlchemy 2.0, and structured traces stream to LangSmith for inspection. Schema is owned exclusively by Alembic.

## Component diagram

```mermaid
flowchart LR
    User([User])
    UI[UI<br/>Streamlit / CLI]
    Agent[Agent<br/>LangGraph StateGraph]
    Tools[Tools<br/>save / retrieve / terminate]
    Validation[Validation<br/>phonenumbers + Pydantic]
    Repo[Repository<br/>StoreRepository, SummaryRepository]
    DB[(PostgreSQL 16)]
    Migrations[Alembic migrations]
    Trace[LangSmith]

    User --> UI
    UI --> Agent
    Agent --> Tools
    Tools --> Validation
    Tools --> Repo
    Repo --> DB
    Migrations --> DB
    Agent -. spans .-> Trace
    Tools -. spans .-> Trace
```

## Sequence — save flow (happy path)

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as UI
    participant G as Graph (LLM node)
    participant T as save_store tool
    participant V as Phone validator
    participant R as StoreRepository
    participant DB as Postgres

    U->>UI: "I want to save my store"
    UI->>G: invoke(state)
    G-->>UI: ask for store name
    U->>UI: "Sunrise Grocers, owner Asha, +1 555 123 4567"
    UI->>G: invoke(state with message)
    G->>T: tool_call(save_store, args)
    T->>V: normalise(phone)
    V-->>T: E.164 phone
    T->>R: save_store(dto)
    R->>DB: INSERT INTO stores ...
    DB-->>R: row id
    R-->>T: StoreDTO
    T-->>G: ToolMessage(saved)
    G-->>UI: "Saved Sunrise Grocers."
    UI-->>U: confirmation
```

## Sequence — retrieve flow with passphrase gate

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as UI
    participant G as Graph (LLM node)
    participant T as retrieve_store tool
    participant Auth as Passphrase check
    participant R as StoreRepository
    participant DB as Postgres

    U->>UI: "Look up Sunrise Grocers"
    UI->>G: invoke(state)
    G-->>UI: ask for passphrase
    U->>UI: provides passphrase
    UI->>G: invoke(state)
    G->>T: tool_call(retrieve_store, args)
    T->>Auth: verify(passphrase)
    alt passphrase correct
        Auth-->>T: ok
        T->>R: get_store_by_phone(...)
        R->>DB: SELECT ... WHERE phone = $1
        DB-->>R: row
        R-->>T: StoreDTO
        T-->>G: ToolMessage(record)
        G-->>UI: render record
    else passphrase wrong
        Auth-->>T: deny
        T-->>G: ToolMessage(access_denied)
        G-->>UI: generic denial (no existence leak)
    end
```

## Sequence — termination flow

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as UI
    participant G as Graph (LLM node)
    participant T as terminate tool
    participant S as Summary builder
    participant R as SummaryRepository
    participant DB as Postgres

    U->>UI: "I'm done, thanks"
    UI->>G: invoke(state)
    G->>G: detect terminate intent
    G->>T: tool_call(terminate, args)
    T->>S: build_summary(state)
    S-->>T: SummaryDTO (JSONB-shaped)
    T->>R: record_summary(dto)
    R->>DB: INSERT INTO conversation_summaries ...
    DB-->>R: ok
    R-->>T: ok
    T-->>G: ToolMessage(terminated)
    G-->>UI: closing message + summary
    UI-->>U: end of conversation
```

## State machine

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> awaiting_phone: save intent detected
    idle --> awaiting_passphrase: retrieve intent detected
    idle --> terminated: terminate intent

    awaiting_phone --> awaiting_phone: phone invalid (re-prompt)
    awaiting_phone --> idle: store saved
    awaiting_phone --> terminated: terminate intent

    awaiting_passphrase --> awaiting_passphrase: passphrase wrong (bounded retries)
    awaiting_passphrase --> idle: passphrase correct, record returned
    awaiting_passphrase --> terminated: terminate intent or retries exhausted

    terminated --> [*]
```

States are slot-driven: the graph routes on `state.pending_slot` and `state.authenticated` rather than on free-text matching. The transition labels above describe intent; the actual edge predicates live in `agent/graph.py`.

## Component responsibilities

| Component | Responsibility |
|---|---|
| **UI** (`ui/streamlit_app.py`, `main.py` CLI) | Render messages, capture user input, drive one graph invocation per turn. No business logic. |
| **Agent** (`agent/graph.py`, `agent/state.py`) | Build and run the LangGraph `StateGraph`. Define nodes, edges, and the typed `AgentState`. Bound iterations via `APP_MAX_GRAPH_ITERATIONS`. |
| **Tools** (`agent/tools.py`) | `save_store`, `retrieve_store`, `terminate`. Receive Pydantic-validated arguments, call validation + repository, return structured `ToolMessage` payloads. |
| **Validation** (`validation/phone.py`, `validation/passphrase.py`) | Phone normalisation to E.164 via `phonenumbers`. Constant-time passphrase comparison against `APP_PASSPHRASE`. Reusable across tools and tests. |
| **Repository** (`data/repositories.py`) | Async, DTO-in / DTO-out persistence. Hides SQLAlchemy from agent code. Single place for query optimisation. |
| **Database** (Postgres 16) | Source of truth. Owned exclusively by Alembic. Schema changes never originate elsewhere (see ADR-009). |
| **Migrations** (`alembic/`) | All DDL, plus required reference data and extension creation. Async config in `alembic/env.py`, URL sourced from `Settings`. |
| **Config** (`config.py`) | `Settings` Pydantic model loading from `.env` and the environment. Single typed entrypoint for every secret and tunable. |
| **Tracing** (LangSmith callback) | Optional. Enabled by `LANGSMITH_TRACING=true`. No application code change to opt in. |

## Tech stack

- **Language**: Python 3.11
- **Dependency manager**: uv
- **LLM orchestration**: LangGraph; LangChain for tools, prompts, and the model client
- **LLM**: Anthropic Claude Sonnet 4.5 via `langchain-anthropic`; configurable via `APP_LLM_MODEL`
- **Database**: PostgreSQL 16 (Docker, `postgres:16-alpine`)
- **DB access**: SQLAlchemy 2.0 async + asyncpg
- **Migrations**: Alembic (async template)
- **Validation / settings**: Pydantic v2 + pydantic-settings
- **Phone normalisation**: `phonenumbers`
- **UI**: Streamlit
- **Logging**: structlog
- **Tracing**: LangSmith (development); Langfuse / OpenTelemetry planned for production
- **Testing**: pytest, pytest-asyncio, pytest-cov, pytest-mock
- **Quality / security**: ruff, mypy (strict), bandit, pip-audit
- **Runtime packaging**: multi-stage Dockerfile, non-root user, named volume only
