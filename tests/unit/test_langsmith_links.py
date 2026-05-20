"""Tests for store_assistant.langsmith_links — T610."""

from __future__ import annotations

import pytest

from store_assistant import langsmith_links


def test_t610_trace_url_none_when_org_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """T610: missing LANGSMITH_ORG → None (regardless of project)."""
    monkeypatch.delenv("LANGSMITH_ORG", raising=False)
    monkeypatch.setenv("LANGSMITH_PROJECT", "demo")
    assert langsmith_links.trace_url("run-abc") is None


def test_t610_trace_url_none_when_project_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_ORG", "acme")
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert langsmith_links.trace_url("run-abc") is None


def test_t610_trace_url_when_both_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_ORG", "acme")
    monkeypatch.setenv("LANGSMITH_PROJECT", "demo")
    url = langsmith_links.trace_url("run-abc")
    assert url is not None
    assert url.startswith("https://smith.langchain.com/o/acme/projects/p/demo/r/")
    assert url.endswith("/run-abc")


def test_thread_url_when_both_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_ORG", "acme")
    monkeypatch.setenv("LANGSMITH_PROJECT", "demo")
    url = langsmith_links.thread_url("a-b-c")
    assert url is not None
    assert "/o/acme/projects/p/demo" in url
    assert "filter=" in url


def test_thread_url_none_when_either_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_ORG", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert langsmith_links.thread_url("a") is None
    monkeypatch.setenv("LANGSMITH_PROJECT", "demo")
    assert langsmith_links.thread_url("a") is None
