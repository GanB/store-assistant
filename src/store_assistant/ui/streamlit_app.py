from __future__ import annotations

import asyncio
import json
from typing import Any, cast
from uuid import uuid4

import streamlit as st
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from store_assistant.agent.checkpointer import (
    CheckpointerNotConfiguredError,
    production_checkpointer,
)
from store_assistant.agent.graph import build_graph
from store_assistant.config import Settings, get_settings
from store_assistant.data.migration_check import (
    MigrationOutOfSyncError,
    assert_db_at_head,
)
from store_assistant.data.models import Store, TurnMetricsRow
from store_assistant.data.repository import (
    MetricsRepository,
    StoreRepository,
    SummaryRepository,
)
from store_assistant.data.schemas import SummaryRead
from store_assistant.health import (
    HealthResult,
    check_checkpoints_enabled,
    check_db_connected,
    check_migrations_at_head,
    check_tracing_enabled,
)
from store_assistant.langsmith_links import thread_url, trace_url
from store_assistant.logging_config import configure_logging
from store_assistant.state_projection import project_state_for_inspector
from store_assistant.threads import get_thread_state
from store_assistant.ui._cache import cache_get_or_compute

# This module is the demo UI. It exists for human walkthroughs of the agent
# and is intentionally not unit-tested — agent and data behaviour is exercised
# from the CLI/integration tests against the same code paths.

PAGE_TITLE = "Store Assistant"
PAGE_ICON = "🏪"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
USER_AVATAR = "👤"
ASSISTANT_AVATAR = "🤖"
END_CONVERSATION_PROMPT = "I'm done"


@st.cache_resource
def _bootstrap() -> Settings:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    try:
        asyncio.run(assert_db_at_head(settings))
    except MigrationOutOfSyncError as exc:
        st.error(
            "Database migration check failed. "
            f"Current revision: `{exc.current}`, expected head: `{exc.expected}`. "
            "Run `make db-migrate` "
            "(or `docker compose run --rm --entrypoint '' ui alembic upgrade head`) "
            "and reload."
        )
        st.stop()
    if settings.agent_checkpoint_db_url is None:
        st.error(
            "AGENT_CHECKPOINT_DB_URL is not set. The agent uses LangGraph's "
            "PostgresSaver for durable conversation state — set the env var "
            "to a Postgres connection string and reload (see ADR-015)."
        )
        st.stop()
    return settings


def _init_session_state() -> None:
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid4())
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "terminated" not in st.session_state:
        st.session_state.terminated = False
    if "summaries" not in st.session_state:
        st.session_state.summaries = []
    if "summaries_loaded" not in st.session_state:
        st.session_state.summaries_loaded = False
    if "pending_termination" not in st.session_state:
        st.session_state.pending_termination = False
    if "last_checkpoint_id" not in st.session_state:
        st.session_state.last_checkpoint_id = None
    if "metrics_cache" not in st.session_state:
        st.session_state.metrics_cache = {}
    if "totals_cache" not in st.session_state:
        # Maps thread_id -> (fetched_at, totals_dict). Bounded TTL keeps the
        # sidebar fresh-ish without re-running the aggregate query on every
        # Streamlit rerun.
        st.session_state.totals_cache = {}
    if "health_cache" not in st.session_state:
        # (fetched_at, list[HealthResult]). 60s TTL — health checks are
        # expensive enough that re-running them on every rerun would dominate
        # the panel's cost.
        st.session_state.health_cache = None


def _start_new_conversation() -> None:
    st.session_state.thread_id = str(uuid4())
    st.session_state.messages = []
    st.session_state.terminated = False
    # No need to clear an in-process checkpointer: state lives in Postgres
    # under the prior thread_id and stays there. A new thread_id is a fresh
    # keyspace.


def _last_assistant_text(messages: list[BaseMessage]) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content
    return None


async def _fetch_recent_summaries(
    settings: Settings,
    limit: int = 20,
) -> list[SummaryRead]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await SummaryRepository(session).list_recent(limit=limit)
    finally:
        await engine.dispose()


def _refresh_summaries(settings: Settings) -> None:
    try:
        st.session_state.summaries = asyncio.run(_fetch_recent_summaries(settings))
    except Exception:
        st.session_state.summaries = []
    st.session_state.summaries_loaded = True


def _resume_thread(settings: Settings, thread_id: str) -> None:
    """Load a thread's state from the checkpointer into the active session.

    This is the same code path that runs on a 'reload from last checkpoint' —
    the only thing the Streamlit session ever holds in memory is a
    presentational view; the source of truth is Postgres.
    """
    state = asyncio.run(get_thread_state(settings, thread_id))
    st.session_state.thread_id = thread_id
    st.session_state.messages = [
        (m["role"], m["content"]) for m in state["messages"]
    ]
    st.session_state.terminated = state["is_terminated"]
    st.session_state.last_checkpoint_id = state["last_checkpoint_id"]
    _invalidate_metrics_cache(thread_id)


def _simulate_worker_restart(settings: Settings) -> None:
    """Wipe Streamlit's view of the active thread and reload from Postgres.

    Demonstrates that nothing is lost across a process restart — the
    in-memory presentational state is rebuilt from the checkpointer.
    """
    thread_id = st.session_state.thread_id
    st.session_state.messages = []
    st.session_state.terminated = False
    st.session_state.last_checkpoint_id = None
    _resume_thread(settings, thread_id)


async def _process_turn(
    settings: Settings,
    thread_id: str,
    user_input: str,
) -> dict[str, Any]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with (
            factory() as session,
            production_checkpointer(settings) as (checkpointer, _pool),
        ):
            store_repo = StoreRepository(session)
            summary_repo = SummaryRepository(session)
            metrics_repo = MetricsRepository(session)
            graph = build_graph(
                settings,
                store_repo,
                summary_repo,
                checkpointer=checkpointer,
                metrics_repo=metrics_repo,
            )
            config = {"configurable": {"thread_id": thread_id}}
            result: dict[str, Any] = await graph.ainvoke(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
            )
            return result
    finally:
        await engine.dispose()


def _render_session_panel(settings: Settings) -> None:
    """Three lines: Thread (8-char prefix, full uuid on hover), Model, Turns."""
    thread_id = st.session_state.thread_id
    short = f"{thread_id[:8]}..."
    turns = _turn_count_from_messages(st.session_state.messages)
    st.markdown("### Session")
    # Inline HTML so the full UUID is available on hover via the title attr.
    # Streamlit's normal caption() would HTML-escape this, so we use markdown
    # with unsafe_allow_html and limit the dynamic content to alnum+dashes
    # (uuid4) to keep the surface boring.
    st.markdown(
        (
            f'<div style="font-size:0.85em;color:#888;">'
            f'Thread:&nbsp;&nbsp;<span title="{thread_id}" '
            f'style="font-family:monospace;">{short}</span><br/>'
            f'Model:&nbsp;&nbsp;&nbsp;<code>{settings.app_llm_model}</code><br/>'
            f"Turns:&nbsp;&nbsp;&nbsp;{turns}"
            f"</div>"
        ),
        unsafe_allow_html=True,
    )


def _turn_count_from_messages(messages: list[tuple[str, str]]) -> int:
    """One turn = one user message (the assistant pair follows). The session
    state stores them flattened into a single list, so dividing by two would
    miss in-flight turns; counting human-side messages is exact."""
    return sum(1 for role, _ in messages if role == ROLE_USER)


def summary_button_label(*, is_terminated: bool) -> str:
    """Pure helper exposed for T615 — keeps the rendering decision testable
    without spinning up Streamlit."""
    return "View summary" if is_terminated else "Resume"


def should_show_reload_button(turn_count: int) -> bool:
    """Pure helper exposed for T616 — Reload-from-last-checkpoint only makes
    sense when there is a checkpointed turn to reload from."""
    return turn_count > 0


def _render_sidebar(settings: Settings) -> None:
    with st.sidebar:
        _render_session_panel(settings)

        if st.button("Start new conversation", use_container_width=True):
            _start_new_conversation()
            _refresh_summaries(settings)
            st.rerun()
        turn_count = _turn_count_from_messages(st.session_state.messages)
        if should_show_reload_button(turn_count) and st.button(
            "Reload from last checkpoint",
            use_container_width=True,
        ):
            _simulate_worker_restart(settings)
            st.toast("Reloaded from Postgres checkpoint.")
            st.rerun()
        if should_show_reload_button(turn_count):
            st.caption(
                "Demonstrates conversation recovery after process restart."
            )
        st.divider()
        # Sidebar panels separated by st.divider() so the visual rhythm is
        # consistent regardless of whether totals or status rows are present.
        # Session totals hides itself on a fresh thread; its divider above
        # keeps the rhythm even when the panel renders nothing.
        _render_session_totals_panel(settings)
        st.divider()
        _render_status_panel(settings)
        st.divider()
        _render_guardrails_panel(settings)
        st.divider()
        col_label, col_refresh = st.columns([3, 1])
        with col_label:
            st.markdown("### Recent conversations")
        with col_refresh:
            if st.button("↻", help="Refresh history", use_container_width=True):
                _refresh_summaries(settings)
                st.rerun()

        summaries: list[SummaryRead] = st.session_state.get("summaries", [])
        if not summaries:
            st.caption("_No summaries yet. Complete a conversation to see it here._")
            return
        # A row in conversation_summaries only lands when the agent reaches the
        # generate_summary node, which only runs after termination — so every
        # entry here represents a terminated thread. The render helper takes
        # is_terminated explicitly so it stays unit-testable for both branches.
        for summary in summaries:
            _render_recent_conversation_entry(
                settings, summary, is_terminated=True
            )


def _render_recent_conversation_entry(
    settings: Settings,
    summary: SummaryRead,
    *,
    is_terminated: bool,
) -> None:
    ts = summary.created_at.strftime("%Y-%m-%d %H:%M")
    short_thread = (
        f"{summary.thread_id[:8]}…" if summary.thread_id else "unknown"
    )
    with st.expander(f"{ts} — {short_thread}", expanded=False):
        if summary.thread_id:
            st.caption(f"thread: `{summary.thread_id}`")
        st.write(summary.summary_text)
        button_label = summary_button_label(is_terminated=is_terminated)
        if is_terminated:
            if summary.thread_id and st.button(
                button_label,
                key=f"view-{summary.thread_id}",
                use_container_width=True,
            ):
                st.session_state[f"viewing-{summary.thread_id}"] = True
            if summary.thread_id and st.session_state.get(
                f"viewing-{summary.thread_id}"
            ):
                st.caption(f"ended: {ts}")
                st.caption("status: terminated (read-only)")
        elif summary.thread_id and st.button(
            button_label,
            key=f"resume-{summary.thread_id}",
            use_container_width=True,
        ):
            _resume_thread(settings, summary.thread_id)
            st.rerun()


SESSION_TOTALS_TTL_SECONDS = 5
HEALTH_CHECK_TTL_SECONDS = 60


async def _run_all_health_checks(settings: Settings) -> list[HealthResult]:
    db = await check_db_connected(settings)
    mig = await check_migrations_at_head(settings)
    cp = await check_checkpoints_enabled(settings)
    tr = check_tracing_enabled()
    return [db, mig, cp, tr]


def _health_results_cached(settings: Settings) -> list[HealthResult]:
    def _fetch() -> list[HealthResult]:
        try:
            return asyncio.run(_run_all_health_checks(settings))
        except Exception as exc:
            return [
                HealthResult(
                    ok=False, label="Health checks", detail=f"failed ({exc!r})"
                )
            ]

    # Single-cell cache; we wrap it in a dict keyed by a constant so the
    # generic helper applies. The dict lives in session_state under
    # health_cache_holder; the legacy `health_cache` slot is unused.
    if "health_cache_holder" not in st.session_state:
        st.session_state.health_cache_holder = {}
    return cast(
        list[HealthResult],
        cache_get_or_compute(
            st.session_state.health_cache_holder,
            "all",
            _fetch,
            HEALTH_CHECK_TTL_SECONDS,
        ),
    )


def _render_guardrails_panel(settings: Settings) -> None:
    """Static six-line summary of the structural guardrails the agent ships
    with. Tests enforce the actual invariants; this badge is the
    human-readable receipt. The off-scope threshold is interpolated from
    settings, never hard-coded — see T612."""
    threshold = settings.app_off_scope_threshold
    lines = [
        "✓ Phone validation (deterministic, libphonenumber)",
        "✓ Passphrase gate (constant-time, never in traces)",
        f"✓ Off-scope termination (threshold: {threshold})",
        "✓ Parameterized DB queries (SQLAlchemy)",
        "✓ Secrets via env vars (no hardcoded creds)",
        "✓ State checkpointed (Postgres, durable)",
    ]
    st.markdown("### Active guardrails")
    st.markdown(
        "<div style='font-size:0.85em;font-family:monospace;color:#22a06b;'>"
        + "<br/>".join(lines)
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_status_panel(settings: Settings) -> None:
    results = _health_results_cached(settings)
    st.markdown("### Status")
    rows: list[str] = []
    for r in results:
        prefix = "✓" if r.ok else "✗"
        colour = "#22a06b" if r.ok else "#d8472b"
        detail = f" — {r.detail}" if r.detail else ""
        if r.label == "Tracing enabled" and r.ok:
            ls_url = thread_url(st.session_state.thread_id)
            link = (
                f" (<a href='{ls_url}' target='_blank'>open in LangSmith ↗</a>)"
                if ls_url
                else ""
            )
        else:
            link = ""
        rows.append(
            f"<span style='color:{colour};'>{prefix} {r.label}{detail}{link}</span>"
        )
    st.markdown(
        "<div style='font-size:0.85em;font-family:monospace;'>"
        + "<br/>".join(rows)
        + "</div>",
        unsafe_allow_html=True,
    )


async def _fetch_session_totals(
    settings: Settings, thread_id: str
) -> dict[str, Any]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await MetricsRepository(session).session_totals(thread_id)
    finally:
        await engine.dispose()


def _session_totals_cached(
    settings: Settings, thread_id: str
) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        try:
            return asyncio.run(_fetch_session_totals(settings, thread_id))
        except Exception:
            return {
                "turns": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cost_usd": 0,
                "avg_latency_ms": 0,
            }

    return cast(
        dict[str, Any],
        cache_get_or_compute(
            st.session_state.totals_cache,
            thread_id,
            _fetch,
            SESSION_TOTALS_TTL_SECONDS,
        ),
    )


def _render_session_totals_panel(settings: Settings) -> None:
    totals = _session_totals_cached(settings, st.session_state.thread_id)
    if not totals.get("turns"):
        return
    in_tok = totals["total_input_tokens"]
    out_tok = totals["total_output_tokens"]
    cost = totals["total_cost_usd"]
    avg_lat = _format_latency(int(totals["avg_latency_ms"]))
    st.markdown("### Session totals")
    st.markdown(
        (
            f"<div style='font-size:0.85em;color:#888;font-family:monospace;'>"
            f"Turns:&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{totals['turns']}<br/>"
            f"Total tokens: {in_tok:,} in / {out_tok:,} out<br/>"
            f"Total cost:&nbsp;&nbsp;&nbsp;${cost:.4f}<br/>"
            f"Avg latency:&nbsp;&nbsp;{avg_lat}"
            f"</div>"
        ),
        unsafe_allow_html=True,
    )


async def _fetch_saved_stores_for_thread(
    settings: Settings, thread_id: str
) -> list[str]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            stmt = select(Store.name).where(Store.thread_id == thread_id)
            result = await session.execute(stmt)
            return [row for (row,) in result.all()]
    finally:
        await engine.dispose()


def _build_state_for_inspector(settings: Settings) -> dict[str, Any]:
    """Curated, redacted view of the agent state for the inspector.

    Pulls together the bits the operator wants to see (thread id, message
    count, terminated flag, what's been saved during this thread, the most
    recent user inputs) and runs the dict through state_projection so the
    passphrase and any phone-shaped strings get masked. Keeping the source
    fields explicit here means we never accidentally leak a raw LangGraph
    state shape that grew a sensitive field after the inspector was
    written."""
    thread_id = st.session_state.thread_id
    try:
        saved = asyncio.run(_fetch_saved_stores_for_thread(settings, thread_id))
    except Exception:
        saved = []
    recent_user_inputs = [
        content
        for role, content in st.session_state.messages[-6:]
        if role == ROLE_USER
    ]
    raw_state: dict[str, Any] = {
        "thread_id": thread_id,
        "messages_count": len(st.session_state.messages),
        "off_scope_strikes": 0,
        "passphrase": settings.app_passphrase.get_secret_value(),
        "saved_stores_in_session": saved,
        "current_node": "(idle)",
        "is_terminated": st.session_state.terminated,
        "recent_user_inputs": recent_user_inputs,
    }
    return project_state_for_inspector(
        raw_state, passphrase=settings.app_passphrase.get_secret_value()
    )


def _render_state_inspector(settings: Settings) -> None:
    with st.expander("Inspect agent state", expanded=False):
        thread_id = st.session_state.thread_id
        last_cp = st.session_state.last_checkpoint_id
        last_cp_label = (
            f"{last_cp[:8]}..." if last_cp else "(none)"
        )
        st.markdown(
            f"<div style='font-size:0.85em;font-family:monospace;'>"
            f"Current node:&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<code>(idle)</code><br/>"
            f"Last checkpoint:&nbsp;&nbsp;<code>{last_cp_label}</code><br/>"
            f"Pending tool calls: 0"
            f"</div>",
            unsafe_allow_html=True,
        )
        projected = _build_state_for_inspector(settings)
        st.markdown("Conversation state:")
        st.code(json.dumps(projected, indent=2, default=str), language="json")
        if not last_cp:
            st.caption(
                f"_thread `{thread_id[:8]}...` has no persisted checkpoint yet._"
            )


async def _fetch_thread_metrics(
    settings: Settings, thread_id: str
) -> list[TurnMetricsRow]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await MetricsRepository(session).list_for_thread(thread_id)
    finally:
        await engine.dispose()


def _metrics_for_active_thread(settings: Settings) -> list[TurnMetricsRow]:
    """Cached per-rerun metrics list for the active thread."""
    thread_id = st.session_state.thread_id
    cache = st.session_state.metrics_cache
    if thread_id in cache:
        return cast(list[TurnMetricsRow], cache[thread_id])
    try:
        rows = asyncio.run(_fetch_thread_metrics(settings, thread_id))
    except Exception:
        rows = []
    cache[thread_id] = rows
    return rows


def _invalidate_metrics_cache(thread_id: str) -> None:
    st.session_state.metrics_cache.pop(thread_id, None)
    st.session_state.totals_cache.pop(thread_id, None)


def _group_metrics_per_user_turn(
    rows: list[TurnMetricsRow],
) -> list[list[TurnMetricsRow]]:
    """Bucket per-LLM-call metric rows into user-driven turns.

    A user-driven turn ends with an LLM call that produced *no* tool calls
    (i.e., the assistant's final reply for that turn). Rows in the same
    bucket all belong to the same user message; the last row in the bucket
    is what the metadata strip displays.
    """
    groups: list[list[TurnMetricsRow]] = []
    current: list[TurnMetricsRow] = []
    for row in rows:
        current.append(row)
        if not row.tool_calls:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


_LATENCY_SECONDS_THRESHOLD_MS = 1000


def _format_latency(latency_ms: int) -> str:
    if latency_ms >= _LATENCY_SECONDS_THRESHOLD_MS:
        return f"{latency_ms / _LATENCY_SECONDS_THRESHOLD_MS:.1f}s"
    return f"{latency_ms}ms"


def _format_metric_strip(row: TurnMetricsRow) -> str:
    if row.cache_read_tokens > 0:
        tokens = (
            f"{row.input_tokens} in (cache: {row.cache_read_tokens}) "
            f"/ {row.output_tokens} out"
        )
    else:
        tokens = f"{row.input_tokens} in / {row.output_tokens} out"
    cost = f"${row.cost_usd:.4f}"
    latency = _format_latency(row.latency_ms)
    node = row.node_name or "-"
    parts = [
        f"tokens: {tokens}",
        cost,
        latency,
        f"node: <code>{node}</code>",
    ]
    return "  ·  ".join(parts)


def _metric_strip_html(row: TurnMetricsRow) -> str:
    """Build the inline HTML for a per-turn metadata strip.

    Returned with a leading two-newline separator and an inline `<div>`
    block so it can be concatenated to the agent's content text and
    rendered in a single `st.markdown` call. That is the entire point —
    a sibling `st.markdown` call from inside `st.chat_message` has, in
    practice, occasionally rendered outside the bubble in some
    Streamlit / browser combinations, putting the strip above the
    following user message. Inlining the strip into the same markdown
    body removes that variance.
    """
    body = _format_metric_strip(row)
    link = trace_url(row.langsmith_run_id) if row.langsmith_run_id else None
    if link:
        body = f"{body}  ·  <a href='{link}' target='_blank'>view trace ↗</a>"
    return (
        "\n\n"
        "<div style='font-size:0.85em;color:#888;font-family:monospace;"
        "margin-top:0.4em;'>"
        f"{body}"
        "</div>"
    )


def _avatar_for(role: str) -> str:
    return USER_AVATAR if role == ROLE_USER else ASSISTANT_AVATAR


def _render_history(settings: Settings) -> None:
    metric_groups = _group_metrics_per_user_turn(
        _metrics_for_active_thread(settings)
    )
    ai_index = 0
    for role, content in st.session_state.messages:
        with st.chat_message(role, avatar=_avatar_for(role)):
            if role == ROLE_ASSISTANT:
                strip_html = ""
                if ai_index < len(metric_groups) and metric_groups[ai_index]:
                    strip_html = _metric_strip_html(metric_groups[ai_index][-1])
                ai_index += 1
                # Single markdown call — the strip is part of the same
                # rendered string as the agent's reply. It cannot leak
                # outside the chat_message container because it is no
                # longer a sibling render.
                st.markdown(content + strip_html, unsafe_allow_html=True)
            else:
                st.markdown(content)


def _handle_user_input(
    settings: Settings,
    user_input: str,
) -> None:
    st.session_state.messages.append((ROLE_USER, user_input))
    # Don't inline-render the user message — _render_history will render
    # it via the post-turn st.rerun() below, in the same code path that
    # handles the agent reply + metadata strip. Single render path keeps
    # the strip placement deterministic.

    with (
        st.chat_message(ROLE_ASSISTANT, avatar=ASSISTANT_AVATAR),
        st.spinner("Thinking…"),
    ):
        try:
            result = asyncio.run(
                _process_turn(
                    settings,
                    st.session_state.thread_id,
                    user_input,
                )
            )
        except CheckpointerNotConfiguredError as exc:
            st.error(f"Checkpointer not configured: {exc}")
            return
        except Exception as exc:
            st.error(f"Something went wrong: {exc}")
            return

        reply = _last_assistant_text(result.get("messages", [])) or "(no response)"
        # Don't render the reply inline here. Append to history, drop the
        # metrics cache, and rerun — _render_history will then display the
        # reply WITH its metadata strip in a single consistent code path.
        # Rendering inline followed by rerun caused a brief flash of the
        # message without its strip; appending and rerunning skips that.
        st.session_state.messages.append((ROLE_ASSISTANT, reply))
        _invalidate_metrics_cache(st.session_state.thread_id)

        # Refresh the displayed last_checkpoint_id from Postgres after the
        # turn so the status badge reflects what actually got persisted.
        # A failure here is purely cosmetic — the next turn will refresh
        # again — so we swallow it rather than disrupt the chat flow.
        try:
            state = asyncio.run(get_thread_state(settings, st.session_state.thread_id))
            st.session_state.last_checkpoint_id = state["last_checkpoint_id"]
        except Exception:  # noqa: S110 — cosmetic refresh, see comment above
            pass

        if result.get("terminated"):
            st.session_state.terminated = True
            _refresh_summaries(settings)
        st.rerun()


def main() -> None:
    # Must be the first Streamlit call. layout=wide gives the metadata strip
    # and inspector room to render without wrapping.
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=PAGE_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    settings = _bootstrap()
    _init_session_state()

    if not st.session_state.summaries_loaded:
        _refresh_summaries(settings)

    _render_sidebar(settings)

    st.title(PAGE_TITLE)
    st.caption("Save and retrieve store records via conversation.")

    _render_history(settings)

    if st.session_state.terminated:
        st.info("Conversation ended. Start a new one from the sidebar.")
        return

    if st.session_state.pending_termination:
        st.session_state.pending_termination = False
        _handle_user_input(settings, END_CONVERSATION_PROMPT)
        return

    user_input = st.chat_input("Type your message…")
    if user_input:
        _handle_user_input(settings, user_input)

    # Subdued End conversation control sits immediately under the input so it
    # is always reachable but never the visual focus. Visible from turn 1
    # (including with zero turns) — terminating an empty thread is a no-op
    # the existing flow handles cleanly via the same code path that the
    # explicit "I'm done" utterance triggers.
    _, _, end_col = st.columns([3, 3, 2])
    with end_col:
        if st.button(
            "End conversation",
            key="end-conversation",
            use_container_width=True,
        ):
            st.session_state.pending_termination = True
            st.rerun()

    _render_state_inspector(settings)


main()
