"""Tests for store_assistant.health — T609 (health check shapes)."""

from __future__ import annotations

from typing import Any

import pytest

from store_assistant import health


@pytest.mark.asyncio
async def test_t609_db_unreachable_returns_failed_result(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """T609 (DB down): check_db_connected reports ok=False with detail."""

    class _ConnCtx:
        async def __aenter__(self) -> _ConnCtx:
            raise OSError("connection refused")

        async def __aexit__(self, *args: Any) -> None:
            return None

    class _Engine:
        def connect(self) -> _ConnCtx:
            return _ConnCtx()

        async def dispose(self) -> None:
            return None

    monkeypatch.setattr(
        health, "create_async_engine", lambda *a, **k: _Engine()
    )
    result = await health.check_db_connected(settings)
    assert result.ok is False
    assert result.label == "DB connected"
    assert result.detail and "OSError" in result.detail


@pytest.mark.asyncio
async def test_t609_migrations_drift_returns_drift_detail(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """T609 (drift): mismatched current vs expected → ok=False, DRIFT detail."""
    monkeypatch.setattr(health, "_expected_head", lambda: "0099")

    class _Conn:
        async def __aenter__(self) -> _Conn:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def run_sync(self, fn: Any) -> str | None:
            del fn
            return "0001"

    class _Engine:
        def connect(self) -> _Conn:
            return _Conn()

        async def dispose(self) -> None:
            return None

    monkeypatch.setattr(
        health, "create_async_engine", lambda *a, **k: _Engine()
    )
    result = await health.check_migrations_at_head(settings)
    assert result.ok is False
    assert result.label == "Migrations at head"
    assert result.detail is not None
    assert "DRIFT" in result.detail
    assert "0001" in result.detail
    assert "0099" in result.detail


def test_t609_tracing_check_returns_shape_when_envs_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T609 (tracing): env-only check returns ok=False + missing detail."""
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    result = health.check_tracing_enabled()
    assert result.ok is False
    assert result.label == "Tracing enabled"
    assert result.detail is not None
    assert "LANGSMITH_API_KEY" in result.detail
    assert "LANGSMITH_PROJECT" in result.detail


def test_t609_tracing_check_ok_when_envs_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    monkeypatch.setenv("LANGSMITH_PROJECT", "demo")
    result = health.check_tracing_enabled()
    assert result.ok is True
    assert result.label == "Tracing enabled"
