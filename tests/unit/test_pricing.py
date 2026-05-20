"""Tests for store_assistant.pricing — T601, T602."""

from __future__ import annotations

from decimal import Decimal

from store_assistant.pricing import (
    CLAUDE_SONNET_4_5_CACHE_READ_PER_MTOK,
    CLAUDE_SONNET_4_5_CACHE_WRITE_PER_MTOK,
    CLAUDE_SONNET_4_5_INPUT_PER_MTOK,
    CLAUDE_SONNET_4_5_OUTPUT_PER_MTOK,
    compute_cost,
)


def test_t601_cost_matches_pricing_constants() -> None:
    """T601: TurnMetrics.cost_usd computed from token counts and constants."""
    cost = compute_cost(input_tokens=1_000_000, output_tokens=0)
    assert cost == CLAUDE_SONNET_4_5_INPUT_PER_MTOK

    cost = compute_cost(input_tokens=0, output_tokens=1_000_000)
    assert cost == CLAUDE_SONNET_4_5_OUTPUT_PER_MTOK

    # Linear blend at known token counts.
    cost = compute_cost(input_tokens=1_000, output_tokens=500)
    expected = (
        Decimal("1000") * CLAUDE_SONNET_4_5_INPUT_PER_MTOK
        + Decimal("500") * CLAUDE_SONNET_4_5_OUTPUT_PER_MTOK
    ) / Decimal("1000000")
    assert cost == expected.quantize(Decimal("0.000001"))


def test_t602_cost_handles_cache_tokens() -> None:
    """T602: compute_cost includes cache read and creation lines."""
    cost = compute_cost(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,
    )
    assert cost == CLAUDE_SONNET_4_5_CACHE_READ_PER_MTOK

    cost = compute_cost(
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=1_000_000,
    )
    assert cost == CLAUDE_SONNET_4_5_CACHE_WRITE_PER_MTOK

    # All four lines combined.
    cost = compute_cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
    )
    expected = (
        CLAUDE_SONNET_4_5_INPUT_PER_MTOK
        + CLAUDE_SONNET_4_5_OUTPUT_PER_MTOK
        + CLAUDE_SONNET_4_5_CACHE_READ_PER_MTOK
        + CLAUDE_SONNET_4_5_CACHE_WRITE_PER_MTOK
    )
    assert cost == expected.quantize(Decimal("0.000001"))
