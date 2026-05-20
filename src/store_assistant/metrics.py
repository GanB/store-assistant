"""Per-turn metrics dataclasses surfaced to the agent layer and the UI.

Critical security invariant — ToolCallSummary.arg_keys carries the *names* of
arguments passed to a tool but never the values. A tool argument value can be
the user's passphrase (the get_store_phone tool has a `passphrase` parameter);
surfacing the keys is fine, surfacing the values would leak. T607 enforces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal


@dataclass
class ToolCallSummary:
    """Trace of a single tool invocation that ran during a turn.

    arg_keys is the list of argument names the tool received — never the
    values. The values can carry user secrets (passphrase, phone numbers
    that the agent is in the middle of normalising) and must not appear in
    metrics rows that are queryable by the UI.
    """

    tool_name: str
    arg_keys: list[str]
    duration_ms: int
    success: bool


@dataclass
class TurnMetrics:
    """Everything we record about a single agent turn.

    Persisted to the turn_metrics table; queried by the UI for the per-turn
    metadata strip and the session-totals panel.
    """

    turn_index: int
    thread_id: str
    checkpoint_id: str
    node_name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: Decimal
    latency_ms: int
    tool_calls: list[ToolCallSummary] = field(default_factory=list)
    langsmith_run_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
