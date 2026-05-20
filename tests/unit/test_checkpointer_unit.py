"""Unit-level checks on the checkpointer wiring (T501, T502, T509, T511).

These tests do not need a running Postgres. They cover static invariants
(no .setup() calls in src/), the threads.py public API shapes, and the
fail-fast behaviour when the env var is unset or the schema is missing.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from store_assistant.agent.checkpointer import (
    CheckpointerNotConfiguredError,
    _conn_url,
    production_checkpointer,
)
from store_assistant.config import Settings
from store_assistant.threads import (
    ThreadSummary,
    fork_thread,
    list_checkpoints,
    list_threads,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "src" / "store_assistant"


def _settings_without_checkpoint_url() -> Settings:
    return Settings(
        anthropic_api_key=SecretStr("test"),
        postgres_user="u",
        postgres_password=SecretStr("p"),
        postgres_db="d",
        app_passphrase=SecretStr("test-pass"),
        agent_checkpoint_db_url=None,
    )


def _real_pg_url() -> str:
    import os  # noqa: PLC0415

    return os.environ.get(
        "AGENT_CHECKPOINT_DB_URL",
        "postgresql://store_assistant:change_me@localhost:5433/store_assistant",
    )


class _SetupCallCollector(ast.NodeVisitor):
    """Walks a file's AST and records `<expr>.setup(...)` call sites.

    AST-based so docstring or comment text mentioning `.setup()` doesn't
    false-positive — only real method-call sites count.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offenses: list[tuple[Path, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "setup":
            self.offenses.append((self.path, node.lineno))
        self.generic_visit(node)


# ---------- T501 ----------
def test_t501_no_setup_calls_in_app_code() -> None:
    """The migration-only invariant: src/ must not call .setup() on any
    checkpointer. Schema lives in alembic; runtime calls to setup() would
    re-introduce DDL outside migrations.
    """
    offenses: list[tuple[Path, int]] = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = _SetupCallCollector(path)
        visitor.visit(tree)
        offenses.extend(visitor.offenses)
    assert not offenses, (
        "Checkpointer .setup() calls forbidden in src/ — schema is owned "
        "by alembic (ADR-009 / ADR-015):\n  "
        + "\n  ".join(f"{p}:{ln}" for p, ln in offenses)
    )


def test_t501_also_no_setup_calls_in_scripts() -> None:
    """Same invariant for scripts/."""
    offenses: list[tuple[Path, int]] = []
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = _SetupCallCollector(path)
        visitor.visit(tree)
        offenses.extend(visitor.offenses)
    assert not offenses, (
        "Checkpointer .setup() calls forbidden in scripts/:\n  "
        + "\n  ".join(f"{p}:{ln}" for p, ln in offenses)
    )


# ---------- T502 ----------
def test_t502_thread_summary_has_required_fields() -> None:
    """ThreadSummary's surface area is part of the threads.py public API and
    must include the fields the UI and CLI render.
    """
    required = {
        "thread_id",
        "created_at",
        "last_updated_at",
        "turn_count",
        "current_node",
        "is_terminated",
    }
    actual = set(ThreadSummary.__annotations__.keys())
    missing = required - actual
    assert not missing, f"ThreadSummary missing fields: {missing}"


# ---------- T511 ----------
def test_t511_unset_env_var_raises_actionable_error() -> None:
    settings = _settings_without_checkpoint_url()
    with pytest.raises(CheckpointerNotConfiguredError) as excinfo:
        _conn_url(settings)
    msg = str(excinfo.value)
    assert "AGENT_CHECKPOINT_DB_URL" in msg
    assert "make db-migrate" in msg or "alembic upgrade head" in msg


def test_t511_threads_helpers_also_fail_fast(tmp_path: Path) -> None:
    """Belt-and-braces: every threads.py entrypoint must surface the same
    error if the env var is missing.
    """
    settings = _settings_without_checkpoint_url()
    for coro in [
        list_threads(settings),
        list_checkpoints(settings, "any"),
        fork_thread(settings, "any"),
    ]:
        with pytest.raises(CheckpointerNotConfiguredError):
            asyncio.run(coro)


# ---------- T509 ----------
@pytest.mark.skipif(
    "AGENT_CHECKPOINT_DB_URL" not in __import__("os").environ,
    reason="needs a reachable Postgres",
)
def test_t509_startup_fails_when_schema_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the saver's search_path at a schema that doesn't exist. The
    probe SELECT FROM checkpoints (unqualified) finds no table on the
    search_path, raises UndefinedTable, and is converted to
    CheckpointerNotConfiguredError.
    """
    # `_conn_url` reads CHECKPOINT_SCHEMA at call time, so monkeypatching
    # the module attribute redirects its search_path without us having to
    # rebuild the URL by hand.
    monkeypatch.setattr(
        "store_assistant.agent.checkpointer.CHECKPOINT_SCHEMA",
        "agent_state_does_not_exist_xyz",
    )
    settings = Settings(
        anthropic_api_key=SecretStr("test"),
        postgres_user="u",
        postgres_password=SecretStr("p"),
        postgres_db="d",
        app_passphrase=SecretStr("test-pass"),
        agent_checkpoint_db_url=SecretStr(_real_pg_url()),
    )

    async def run() -> None:
        async with production_checkpointer(settings) as (_saver, _pool):
            pytest.fail("expected CheckpointerNotConfiguredError")

    with pytest.raises(CheckpointerNotConfiguredError) as excinfo:
        asyncio.run(run())
    assert "agent_state_does_not_exist_xyz" in str(excinfo.value)
    assert "make db-migrate" in str(excinfo.value)


