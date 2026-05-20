from __future__ import annotations

from evals.types import RunOutput, Score, Trace


def score(trace: Trace, run_output: RunOutput) -> Score:
    expected_tools = list(trace["expected"].get("expected_tools_called", []))
    actual_tools = [tc.get("name", "") for tc in run_output.get("tool_calls", [])]

    passed = actual_tools == expected_tools
    return Score(
        name="task_completion",
        passed=passed,
        reason=(
            "tool call sequence matches expected"
            if passed
            else f"expected {expected_tools}, got {actual_tools}"
        ),
        details={"expected": expected_tools, "actual": actual_tools},
    )
