from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from evals.types import RunMetadata, TraceResult

ALL_SCORERS = (
    "task_completion",
    "refusal",
    "passphrase_leak",
    "summary_fidelity",
    "phone_validation",
)


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def _trace_passed(result: TraceResult) -> bool:
    return all(score["passed"] for score in result["scores"])


def _summary_table(results: list[TraceResult]) -> str:
    counts: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: {s: [0, 0] for s in ALL_SCORERS}
    )
    for r in results:
        cat = r["category"]
        for s in r["scores"]:
            name = s["name"]
            if name not in counts[cat]:
                continue
            counts[cat][name][1] += 1
            if s["passed"]:
                counts[cat][name][0] += 1

    header = "| category | " + " | ".join(ALL_SCORERS) + " |"
    sep = "|" + "|".join(["---"] * (len(ALL_SCORERS) + 1)) + "|"
    rows = [header, sep]
    for cat in sorted(counts):
        cells = []
        for scorer_name in ALL_SCORERS:
            passed_n, total_n = counts[cat][scorer_name]
            cells.append(f"{passed_n}/{total_n}" if total_n else "—")
        rows.append(f"| {cat} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _per_trace(results: list[TraceResult]) -> str:
    lines = []
    for r in results:
        passed = _trace_passed(r)
        marker = "PASS" if passed else "FAIL"
        lines.append(f"### {r['trace_id']} ({r['category']}) — {marker}")
        lines.append(f"_{r['description']}_")
        lines.append("")
        lines.append("| scorer | passed | reason |")
        lines.append("|---|---|---|")
        for s in r["scores"]:
            ok = "yes" if s["passed"] else "**NO**"
            reason = s["reason"].replace("|", "/")
            lines.append(f"| {s['name']} | {ok} | {reason} |")
        out = r.get("output", {}) or {}
        if out.get("tokens_in") or out.get("tokens_out") or out.get("latency_ms"):
            lines.append("")
            lines.append(
                f"_tokens_in={out.get('tokens_in', 0)} "
                f"tokens_out={out.get('tokens_out', 0)} "
                f"latency_ms={out.get('latency_ms', 0)} "
                f"cost_usd={out.get('cost_usd', 0.0):.4f}_"
            )
        lines.append("")
    return "\n".join(lines)


def _badge_line(results: list[TraceResult], mode: str) -> str:
    total = len(results)
    passed = sum(1 for r in results if _trace_passed(r))
    return f"`evals: {passed}/{total} passing ({mode})`"


def render(metadata: RunMetadata, results: list[TraceResult]) -> str:
    parts = [
        f"# Eval report — {metadata.get('timestamp', '?')}",
        "",
        _badge_line(results, metadata.get("mode", "replay")),
        "",
        "## Run metadata",
        "",
        f"- mode: `{metadata.get('mode', '?')}`",
        f"- model: `{metadata.get('model', '?')}`",
        f"- git sha: `{metadata.get('git_sha', '?')}`",
        f"- filter: `{metadata.get('filter') or 'none'}`",
        f"- total cost (USD): `{metadata.get('total_cost_usd', 0.0):.4f}`",
        "",
        "## Summary by category",
        "",
        _summary_table(results),
        "",
        "## Per-trace results",
        "",
        _per_trace(results),
    ]
    return "\n".join(parts)


def write_report(
    metadata: RunMetadata, results: list[TraceResult], out_dir: Path
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = metadata.get("timestamp") or datetime.now(tz=UTC).isoformat(timespec="seconds")
    filename = _slugify(timestamp) + ".md"
    path = out_dir / filename
    path.write_text(render(metadata, results), encoding="utf-8")
    return path
