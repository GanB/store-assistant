"""Tests for store_assistant.ui._cache — T617 (cache TTL behaviour)."""

from __future__ import annotations

from store_assistant.ui._cache import cache_get_or_compute


def test_t617_cached_within_ttl_window_does_not_refetch() -> None:
    """T617: cache returns cached result within TTL window."""
    holder: dict[str, tuple[float, int]] = {}
    calls = {"n": 0}

    def fetcher() -> int:
        calls["n"] += 1
        return calls["n"]

    # First call at t=0 fetches and stores.
    assert cache_get_or_compute(holder, "k", fetcher, ttl_seconds=10, now=0) == 1

    # Second call at t=5 (within TTL=10) returns cached value.
    assert cache_get_or_compute(holder, "k", fetcher, ttl_seconds=10, now=5) == 1

    # Third call at t=11 (TTL elapsed) refetches.
    assert cache_get_or_compute(holder, "k", fetcher, ttl_seconds=10, now=11) == 2

    # Distinct keys are independent.
    other = cache_get_or_compute(
        holder, "other", fetcher, ttl_seconds=10, now=11
    )
    assert other == 3
