"""Tests for store_assistant.metrics — T607.

T607 enforces that ToolCallSummary carries arg_keys but NOT arg values,
both at the type level (no `arg_values` attribute) and at runtime (a
constructed instance never grows that attribute through normal use)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from store_assistant.metrics import ToolCallSummary, TurnMetrics


def test_t607_tool_call_summary_has_arg_keys_not_values() -> None:
    """T607: arg_keys yes, arg_values no — type-level + runtime check."""
    field_names = {f.name for f in dataclasses.fields(ToolCallSummary)}
    assert "arg_keys" in field_names, "ToolCallSummary must expose arg_keys"
    assert "arg_values" not in field_names, (
        "ToolCallSummary must NOT expose arg_values — values can carry "
        "the user passphrase or unredacted phone numbers"
    )

    # Runtime: trying to set arg_values on a constructed instance via the
    # constructor signature must raise — dataclass __init__ rejects unknown
    # kwargs, so we verify that path explicitly.
    with pytest.raises(TypeError):
        ToolCallSummary(  # type: ignore[call-arg]
            tool_name="x",
            arg_keys=["y"],
            duration_ms=0,
            success=True,
            arg_values=["secret"],
        )

    # And the canonical constructed instance only carries arg names.
    tc = ToolCallSummary(
        tool_name="get_store_phone",
        arg_keys=["name", "passphrase"],
        duration_ms=0,
        success=True,
    )
    as_dict = dataclasses.asdict(tc)
    assert "arg_keys" in as_dict
    assert "arg_values" not in as_dict
    # Even after asdict, no place lurks a values list.
    assert tc.arg_keys == ["name", "passphrase"]


def test_turn_metrics_round_trips() -> None:
    """Sanity construction — used by other tests / fixtures."""
    tm = TurnMetrics(
        turn_index=1,
        thread_id="t-1",
        checkpoint_id="cp-1",
        node_name="llm_node",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd=Decimal("0.001000"),
        latency_ms=42,
        tool_calls=[],
        langsmith_run_id=None,
    )
    assert tm.thread_id == "t-1"
    assert tm.cost_usd == Decimal("0.001000")
