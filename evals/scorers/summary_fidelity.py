from __future__ import annotations

import json
import os
import re
from typing import Any

from evals.types import RunOutput, Score, Trace

JUDGE_SAMPLES = 3
JUDGE_PASS_THRESHOLD = 4
JUDGE_SCORE_MIN = 1
JUDGE_SCORE_MAX = 5
JUDGE_PROMPT = (
    "You are an evaluator scoring how faithfully a conversation summary "
    "represents the actual conversation. Score from 1 (no fidelity) to 5 "
    "(perfect fidelity). Reply with strict JSON: "
    '{"score": <int 1-5>, "reason": "<short reason>"}.\n\n'
    "Conversation transcript:\n{transcript}\n\nSummary:\n{summary}\n"
)


def _replay_score(trace: Trace, run_output: RunOutput) -> Score:
    expected_substrings = trace["expected"].get("expected_summary_contains", [])
    summary = run_output.get("final_summary") or _join_assistant_text(run_output)
    if not expected_substrings:
        return Score(
            name="summary_fidelity",
            passed=True,
            reason="no summary substrings expected",
            details={"applicable": False, "mode": "replay"},
        )
    if not summary:
        return Score(
            name="summary_fidelity",
            passed=False,
            reason="expected summary substrings but no summary produced",
            details={"mode": "replay", "expected": expected_substrings},
        )
    missing = [s for s in expected_substrings if s.lower() not in summary.lower()]
    if missing:
        return Score(
            name="summary_fidelity",
            passed=False,
            reason=f"summary missing substring(s): {missing}",
            details={"mode": "replay", "missing": missing, "summary": summary},
        )
    return Score(
        name="summary_fidelity",
        passed=True,
        reason="summary contains every expected substring",
        details={"mode": "replay", "expected": expected_substrings},
    )


def _join_assistant_text(run_output: RunOutput) -> str:
    return " ".join(run_output.get("output_messages", []))


def _build_transcript(trace: Trace) -> str:
    lines = []
    for turn in trace["turns"]:
        role = turn.get("role", "?")
        content = turn.get("content", "")
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _judge_once(transcript: str, summary: str) -> tuple[int, str]:
    """Calls the judge model and returns (score, reason). Imports are lazy so
    the rest of the harness never needs the LLM client.
    """
    from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
    from langchain_core.messages import HumanMessage  # noqa: PLC0415

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    judge_model = os.environ.get("EVAL_JUDGE_MODEL", "claude-sonnet-4-5")
    llm = ChatAnthropic(
        model=judge_model,
        anthropic_api_key=api_key,  # type: ignore[arg-type]
        temperature=0,
        max_tokens=200,
    )
    prompt = JUDGE_PROMPT.format(transcript=transcript, summary=summary)
    response = llm.invoke([HumanMessage(content=prompt)])
    text = response.content if isinstance(response.content, str) else str(response.content)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return 0, "judge returned no JSON"
    try:
        parsed: dict[str, Any] = json.loads(match.group())
    except json.JSONDecodeError:
        return 0, "judge returned invalid JSON"
    raw = parsed.get("score", 0)
    try:
        score_value = int(raw)
    except (TypeError, ValueError):
        score_value = 0
    return score_value, str(parsed.get("reason", ""))


def _live_score(trace: Trace, run_output: RunOutput) -> Score:
    summary = run_output.get("final_summary") or _join_assistant_text(run_output)
    if not summary:
        return Score(
            name="summary_fidelity",
            passed=False,
            reason="no summary produced",
            details={"mode": "live"},
        )

    transcript = _build_transcript(trace)
    samples: list[tuple[int, str]] = []
    for _ in range(JUDGE_SAMPLES):
        try:
            samples.append(_judge_once(transcript, summary))
        except Exception as exc:  # judge failures must not crash the run
            samples.append((0, f"judge error: {exc!s}"))

    valid_scores = [
        s for s, _ in samples if JUDGE_SCORE_MIN <= s <= JUDGE_SCORE_MAX
    ]
    if not valid_scores:
        return Score(
            name="summary_fidelity",
            passed=False,
            reason="judge produced no valid scores",
            details={"mode": "live", "samples": samples},
        )
    median = sorted(valid_scores)[len(valid_scores) // 2]
    passed = median >= JUDGE_PASS_THRESHOLD
    return Score(
        name="summary_fidelity",
        passed=passed,
        reason=f"judge median score = {median}",
        details={"mode": "live", "median": median, "samples": samples},
    )


def score(trace: Trace, run_output: RunOutput) -> Score:
    if os.environ.get("EVAL_MODE") == "live":
        return _live_score(trace, run_output)
    return _replay_score(trace, run_output)
