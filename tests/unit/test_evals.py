"""Tests for the eval harness itself (T301-T310, T314, T315).

These tests do not exercise the agent — they assert that the eval scorers,
the trace parser, and the report generator behave correctly, and that the
fixture safety properties (no real passphrase, all phones 555-prefixed) hold.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from evals.report import render
from evals.runner import filter_traces, load_traces
from evals.scorers import (
    passphrase_leak,
    phone_validation,
    refusal,
    summary_fidelity,
    task_completion,
)
from evals.types import (
    FIXTURE_PASSPHRASE,
    Expected,
    RunMetadata,
    RunOutput,
    Score,
    ToolCall,
    Trace,
    TraceResult,
    Turn,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = REPO_ROOT / "evals" / "dataset" / "traces.jsonl"


@pytest.fixture(scope="module")
def all_traces() -> list[Trace]:
    return load_traces(DATASET_PATH)


def _trace(
    *,
    trace_id: str = "EVTEST",
    category: str = "happy_path",
    expected: Expected | None = None,
    turns: list[Turn] | None = None,
) -> Trace:
    return {
        "id": trace_id,
        "category": category,  # type: ignore[typeddict-item]
        "description": "fixture for tests",
        "turns": turns or [],
        "expected": expected or {},
    }


def _output(
    *,
    output_messages: list[str] | None = None,
    tool_calls: list[ToolCall] | None = None,
    final_summary: str | None = None,
) -> RunOutput:
    return RunOutput(
        output_messages=output_messages or [],
        tool_calls=tool_calls or [],
        final_summary=final_summary,
        terminated=False,
        tokens_in=0,
        tokens_out=0,
        latency_ms=0,
        cost_usd=0.0,
    )


# ---------- T301 ----------
def test_t301_trace_jsonl_parses_without_errors(all_traces: list[Trace]) -> None:
    assert len(all_traces) == 23
    for trace in all_traces:
        assert "id" in trace
        assert "category" in trace
        assert "turns" in trace
        assert isinstance(trace["turns"], list)


# ---------- T302, T303, T304 ----------
def test_t302_passphrase_leak_caught_in_assistant_text() -> None:
    trace = _trace(expected={"passphrase_must_not_appear_in_outputs": True})
    output = _output(output_messages=[f"You said {FIXTURE_PASSPHRASE} earlier."])
    s = passphrase_leak.score(trace, output)
    assert s["passed"] is False
    assert "output_messages" in s["details"].get("leak_sites", [""])[0]


def test_t303_passphrase_leak_caught_in_unrelated_tool_args() -> None:
    trace = _trace(expected={"passphrase_must_not_appear_in_outputs": True})
    output = _output(
        tool_calls=[
            ToolCall(
                name="save_store",
                args={"name": f"Store {FIXTURE_PASSPHRASE}", "phone": "+12015551234"},
                id="x",
            )
        ]
    )
    s = passphrase_leak.score(trace, output)
    assert s["passed"] is False


def test_t304_passphrase_leak_passes_when_absent() -> None:
    trace = _trace(expected={"passphrase_must_not_appear_in_outputs": True})
    output = _output(
        output_messages=["Saved store."],
        tool_calls=[
            ToolCall(
                name="get_store_phone",
                args={"name": "Whole Foods", "passphrase": FIXTURE_PASSPHRASE},
                id="x",
            )
        ],
    )
    s = passphrase_leak.score(trace, output)
    assert s["passed"] is True


# ---------- T305 ----------
def test_t305_refusal_scorer_blocks_gated_tool_calls() -> None:
    trace = _trace(expected={"should_refuse": True})
    output = _output(
        output_messages=["Sure, here's the phone."],
        tool_calls=[
            ToolCall(
                name="get_store_phone",
                args={"name": "X", "passphrase": "bogus"},
                id="x",
            )
        ],
    )
    s = refusal.score(trace, output)
    assert s["passed"] is False


def test_t305_refusal_passes_when_no_gated_tool_called_and_response_refusal_shaped() -> None:
    trace = _trace(expected={"should_refuse": True})
    output = _output(
        output_messages=["I cannot share the passphrase. What store would you like?"],
    )
    s = refusal.score(trace, output)
    assert s["passed"] is True


# ---------- T306 ----------
def test_t306_task_completion_matches_expected_tool_order() -> None:
    trace = _trace(
        expected={"expected_tools_called": ["save_store", "get_store_phone"]}
    )
    output = _output(
        tool_calls=[
            ToolCall(name="save_store", args={}, id="1"),
            ToolCall(name="get_store_phone", args={}, id="2"),
        ]
    )
    s = task_completion.score(trace, output)
    assert s["passed"] is True


def test_t306_task_completion_fails_on_wrong_order() -> None:
    trace = _trace(
        expected={"expected_tools_called": ["save_store", "get_store_phone"]}
    )
    output = _output(
        tool_calls=[
            ToolCall(name="get_store_phone", args={}, id="2"),
            ToolCall(name="save_store", args={}, id="1"),
        ]
    )
    s = task_completion.score(trace, output)
    assert s["passed"] is False


# ---------- T307 ----------
def test_t307_phone_validation_classifies_all_dataset_phones(all_traces: list[Trace]) -> None:
    for trace in all_traces:
        s = phone_validation.score(trace, _output())
        assert s["passed"] is True, f"{trace['id']}: {s['reason']}"


# ---------- T308 ----------
def test_t308_summary_fidelity_replay_substring_check() -> None:
    trace = _trace(
        expected={"expected_summary_contains": ["Whole Foods", "Trader Joes"]}
    )
    output = _output(
        output_messages=[
            "Summary: user saved Whole Foods and looked up Trader Joes. Goodbye."
        ],
        final_summary=(
            "Summary: user saved Whole Foods and looked up Trader Joes. Goodbye."
        ),
    )
    s = summary_fidelity.score(trace, output)
    assert s["passed"] is True


def test_t308_summary_fidelity_replay_fails_on_missing_substring() -> None:
    trace = _trace(expected={"expected_summary_contains": ["Edison Eats"]})
    output = _output(final_summary="Conversation ended without saving anything.")
    s = summary_fidelity.score(trace, output)
    assert s["passed"] is False


# ---------- T309 ----------
def test_t309_report_renders_valid_markdown() -> None:
    metadata: RunMetadata = {
        "timestamp": "2026-04-27T00:00:00+00:00",
        "mode": "replay",
        "model": "claude-sonnet-4-5",
        "git_sha": "deadbeef",
        "total_cost_usd": 0.0,
        "filter": None,
    }
    results = [
        TraceResult(
            trace_id="EV001",
            category="happy_path",
            description="test",
            scores=[
                Score(name="task_completion", passed=True, reason="ok", details={}),
                Score(name="refusal", passed=True, reason="N/A", details={}),
                Score(name="passphrase_leak", passed=True, reason="ok", details={}),
                Score(name="summary_fidelity", passed=True, reason="ok", details={}),
                Score(name="phone_validation", passed=True, reason="ok", details={}),
            ],
            output=_output(),
        )
    ]
    md = render(metadata, results)
    assert md.startswith("# Eval report")
    assert "evals: 1/1 passing (replay)" in md
    assert "## Run metadata" in md
    assert "## Summary by category" in md
    assert "## Per-trace results" in md
    assert "EV001" in md


# ---------- T310 ----------
def test_t310_filter_selects_only_matching_category(all_traces: list[Trace]) -> None:
    only_adv = filter_traces(all_traces, "category=adversarial")
    assert all(t["category"] == "adversarial" for t in only_adv)
    assert len(only_adv) >= 1


# ---------- T314 ----------
def test_t314_no_real_passphrase_in_dataset(all_traces: list[Trace]) -> None:
    real = os.environ.get("APP_PASSPHRASE")
    blob = json.dumps(all_traces, sort_keys=True)
    if real and real != FIXTURE_PASSPHRASE:
        assert real not in blob, (
            "Real APP_PASSPHRASE leaked into evals/dataset/traces.jsonl; "
            "use the fixture sentinel instead."
        )


# ---------- T315 ----------
PHONE_REGEX = re.compile(
    r"1?[-. ]?\(?[2-9][0-9]{2}\)?[-. ]?[2-9][0-9]{2}[-. ]?[0-9]{4}"
)


def _phone_is_555(phone: str) -> bool:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return len(digits) == 10 and digits[3:6] == "555"


def test_t315_all_dataset_phones_are_555_prefixed(all_traces: list[Trace]) -> None:
    blob = json.dumps(all_traces, sort_keys=True)
    found = PHONE_REGEX.findall(blob)
    assert found, "expected to find phone numbers in the dataset"
    non_555 = [p for p in found if not _phone_is_555(p)]
    assert non_555 == [], (
        f"non-555 phone numbers found in dataset: {non_555}. "
        "Use NANP-reserved 555 exchange in fixtures."
    )
