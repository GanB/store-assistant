from __future__ import annotations

from typing import Any, Literal, TypedDict

Category = Literal[
    "happy_path",
    "validation",
    "passphrase",
    "termination",
    "adversarial",
    "summary",
    "resilience",
]

FIXTURE_PASSPHRASE = "fixture-passphrase-do-not-use"  # noqa: S105 — fixture sentinel, not a real credential


class ToolCall(TypedDict, total=False):
    name: str
    args: dict[str, Any]
    id: str


class Turn(TypedDict, total=False):
    role: Literal["user", "assistant"]
    content: str
    tool_calls: list[ToolCall]


class Expected(TypedDict, total=False):
    task_completion: bool
    should_refuse: bool
    expected_tools_called: list[str]
    passphrase_must_not_appear_in_outputs: bool
    expected_summary_contains: list[str]
    expected_phones_accepted: list[str]
    expected_phones_rejected: list[str]


class Trace(TypedDict):
    id: str
    category: Category
    description: str
    turns: list[Turn]
    expected: Expected


class Score(TypedDict):
    name: str
    passed: bool
    reason: str
    details: dict[str, Any]


class RunOutput(TypedDict, total=False):
    """The observed behaviour of the agent for one trace.

    In replay mode this is materialised from the fixture's assistant turns;
    in live mode it is observed by running the user turns through the real
    graph and capturing outputs.
    """

    output_messages: list[str]
    tool_calls: list[ToolCall]
    final_summary: str | None
    terminated: bool
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float


class TraceResult(TypedDict, total=False):
    trace_id: str
    category: str
    description: str
    scores: list[Score]
    output: RunOutput


class RunMetadata(TypedDict, total=False):
    timestamp: str
    mode: Literal["replay", "live"]
    model: str
    git_sha: str
    total_cost_usd: float
    filter: str | None


class RunReport(TypedDict):
    metadata: RunMetadata
    results: list[TraceResult]
