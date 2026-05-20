from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from evals.scorers import (
    passphrase_leak,
    phone_validation,
    refusal,
    summary_fidelity,
    task_completion,
)
from evals.types import (
    RunMetadata,
    RunOutput,
    Score,
    ToolCall,
    Trace,
    TraceResult,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "evals" / "dataset" / "traces.jsonl"
DEFAULT_REPORT_DIR = REPO_ROOT / "reports" / "evals"

LIVE_COST_DEFAULT_LIMIT_USD = 1.00
ESTIMATED_COST_PER_TRACE_USD = 0.05  # rough budget assumption for live mode


SCORERS = [
    task_completion,
    refusal,
    passphrase_leak,
    summary_fidelity,
    phone_validation,
]


def load_traces(path: Path) -> list[Trace]:
    out: list[Trace] = []
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def filter_traces(traces: list[Trace], filter_expr: str | None) -> list[Trace]:
    if not filter_expr:
        return traces
    if "=" not in filter_expr:
        raise ValueError(
            f"--filter must be of the form key=value, got {filter_expr!r}"
        )
    key, _, value = filter_expr.partition("=")
    return [t for t in traces if str(t.get(key, "")) == value]


def _replay_run(trace: Trace) -> RunOutput:
    """Materialise a RunOutput from the fixture's assistant turns."""
    output_messages: list[str] = []
    tool_calls: list[ToolCall] = []
    final_summary: str | None = None
    for turn in trace["turns"]:
        if turn.get("role") != "assistant":
            continue
        content = turn.get("content", "")
        if content:
            output_messages.append(content)
        for tc in turn.get("tool_calls", []) or []:
            tool_calls.append(tc)
    if output_messages:
        last = output_messages[-1]
        if "summary" in last.lower() or "goodbye" in last.lower():
            final_summary = last
    return RunOutput(
        output_messages=output_messages,
        tool_calls=tool_calls,
        final_summary=final_summary,
        terminated=any("goodbye" in m.lower() for m in output_messages),
        tokens_in=0,
        tokens_out=0,
        latency_ms=0,
        cost_usd=0.0,
    )


async def _live_run(trace: Trace) -> RunOutput:
    """Drive the real agent with the trace's user turns and observe."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: PLC0415
    from pydantic import SecretStr  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415
    from sqlalchemy.pool import StaticPool  # noqa: PLC0415

    from evals.types import FIXTURE_PASSPHRASE  # noqa: PLC0415
    from store_assistant.agent.graph import build_graph  # noqa: PLC0415
    from store_assistant.config import Settings  # noqa: PLC0415
    from store_assistant.data.models import Base  # noqa: PLC0415
    from store_assistant.data.repository import (  # noqa: PLC0415
        StoreRepository,
        SummaryRepository,
    )

    settings = Settings(
        anthropic_api_key=SecretStr(os.environ["ANTHROPIC_API_KEY"]),
        postgres_user="u",
        postgres_password=SecretStr("p"),
        postgres_db="d",
        app_passphrase=SecretStr(FIXTURE_PASSPHRASE),
        app_llm_model=os.environ.get("EVAL_MODEL", "claude-sonnet-4-5"),
    )

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    output_messages: list[str] = []
    tool_calls: list[ToolCall] = []

    try:
        async with factory() as session:
            store_repo = StoreRepository(session)
            summary_repo = SummaryRepository(session)
            graph = build_graph(settings, store_repo, summary_repo)
            config = {"configurable": {"thread_id": f"eval-{trace['id']}"}}

            for turn in trace["turns"]:
                if turn.get("role") != "user":
                    continue
                user_text = turn.get("content", "")
                result = await graph.ainvoke(
                    {"messages": [HumanMessage(content=user_text)]},
                    config=config,
                )
                for msg in result.get("messages", []):
                    if isinstance(msg, AIMessage):
                        if msg.content:
                            text = (
                                msg.content
                                if isinstance(msg.content, str)
                                else str(msg.content)
                            )
                            if text not in output_messages:
                                output_messages.append(text)
                        for tc in getattr(msg, "tool_calls", []) or []:
                            tool_calls.append(
                                ToolCall(
                                    name=str(tc.get("name", "")),
                                    args=dict(tc.get("args", {})),
                                    id=str(tc.get("id", "")),
                                )
                            )
                    elif isinstance(msg, ToolMessage):
                        # tool result content is structured; not used directly
                        # for scoring (scorers operate on AIMessage outputs and
                        # tool-call args).
                        pass

            terminated = bool(result.get("terminated"))
            final_summary = result.get("summary")
    finally:
        await engine.dispose()

    return RunOutput(
        output_messages=output_messages,
        tool_calls=tool_calls,
        final_summary=final_summary,
        terminated=terminated,
        tokens_in=0,
        tokens_out=0,
        latency_ms=0,
        cost_usd=0.0,
    )


def _score_one(trace: Trace, run_output: RunOutput) -> list[Score]:
    return [scorer.score(trace, run_output) for scorer in SCORERS]


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 — git is on PATH in dev/CI
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _build_metadata(
    *, mode: str, model: str, filter_expr: str | None, total_cost: float
) -> RunMetadata:
    return RunMetadata(
        timestamp=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        mode=mode,  # type: ignore[typeddict-item]
        model=model,
        git_sha=_git_sha(),
        total_cost_usd=total_cost,
        filter=filter_expr,
    )


def _execute(
    traces: Iterable[Trace],
    *,
    mode: str,
) -> list[TraceResult]:
    results: list[TraceResult] = []
    for trace in traces:
        run_output = (
            asyncio.run(_live_run(trace)) if mode == "live" else _replay_run(trace)
        )
        scores = _score_one(trace, run_output)
        results.append(
            TraceResult(
                trace_id=trace["id"],
                category=trace["category"],
                description=trace["description"],
                scores=scores,
                output=run_output,
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.runner")
    parser.add_argument("--mode", choices=["replay", "live"], default="replay")
    parser.add_argument("--filter", default=None, help="key=value (e.g. category=adversarial)")
    parser.add_argument("--out", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help="path to traces.jsonl (default: evals/dataset/traces.jsonl)",
    )
    parser.add_argument(
        "--confirm-cost",
        action="store_true",
        help="bypass the live-mode cost-estimate gate",
    )
    args = parser.parse_args(argv)

    if args.mode == "live" and os.environ.get("RUN_EVALS_LIVE") != "1":
        print("Live evals are gated. Set RUN_EVALS_LIVE=1 to run.", file=sys.stderr)
        return 2

    traces = load_traces(Path(args.dataset))
    traces = filter_traces(traces, args.filter)
    if not traces:
        print("No traces matched the filter; nothing to run.", file=sys.stderr)
        return 2

    if args.mode == "live":
        os.environ["EVAL_MODE"] = "live"
        estimate = ESTIMATED_COST_PER_TRACE_USD * len(traces)
        print(f"Estimated live-eval cost: ${estimate:.2f} for {len(traces)} traces.")
        if estimate > LIVE_COST_DEFAULT_LIMIT_USD and not args.confirm_cost:
            print(
                f"Estimate exceeds ${LIVE_COST_DEFAULT_LIMIT_USD:.2f}; "
                "rerun with --confirm-cost to proceed.",
                file=sys.stderr,
            )
            return 3

    results = _execute(traces, mode=args.mode)
    total_cost = sum(r["output"].get("cost_usd", 0.0) for r in results)
    metadata = _build_metadata(
        mode=args.mode,
        model=os.environ.get("EVAL_MODEL", "claude-sonnet-4-5"),
        filter_expr=args.filter,
        total_cost=total_cost,
    )

    from evals.report import write_report  # noqa: PLC0415 — avoid cycle on rare error paths

    out_path = write_report(metadata, results, Path(args.out))
    print(f"Report written to {out_path}")

    failed = sum(
        1 for r in results if any(not s["passed"] for s in r["scores"])
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
