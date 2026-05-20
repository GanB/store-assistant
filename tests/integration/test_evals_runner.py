"""Integration tests for the eval runner (T311, T312, T313)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from evals.runner import main as runner_main

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = REPO_ROOT / "evals" / "dataset" / "traces.jsonl"


def _isolated_dataset(tmp_path: Path, traces: list[dict[str, Any]]) -> Path:
    target = tmp_path / "traces.jsonl"
    target.write_text(
        "\n".join(json.dumps(t) for t in traces) + "\n",
        encoding="utf-8",
    )
    return target


# ---------- T311 ----------
def test_t311_replay_run_against_full_dataset_produces_a_report(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    rc = runner_main(
        [
            "--mode",
            "replay",
            "--dataset",
            str(DATASET_PATH),
            "--out",
            str(out_dir),
        ]
    )
    assert rc == 0
    files = list(out_dir.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "evals: 23/23 passing (replay)" in text


# ---------- T312 ----------
def test_t312_runner_exits_nonzero_when_a_scorer_fails(tmp_path: Path) -> None:
    # Build a synthetic dataset with one trace whose tool sequence won't match.
    bad_trace = {
        "id": "EVBAD",
        "category": "happy_path",
        "description": "deliberately broken: expects save_store but fixture has none",
        "turns": [
            {"role": "user", "content": "Save Test Mart, 415-555-9999"},
            {"role": "assistant", "content": "I refuse."},
        ],
        "expected": {
            "expected_tools_called": ["save_store"],
            "passphrase_must_not_appear_in_outputs": True,
        },
    }
    dataset = _isolated_dataset(tmp_path, [bad_trace])
    out_dir = tmp_path / "out"
    rc = runner_main(
        [
            "--mode",
            "replay",
            "--dataset",
            str(dataset),
            "--out",
            str(out_dir),
        ]
    )
    assert rc == 1
    files = list(out_dir.glob("*.md"))
    assert files
    assert "FAIL" in files[0].read_text(encoding="utf-8")


# ---------- T313 ----------
def test_t313_live_mode_aborts_when_cost_estimate_exceeds_threshold_without_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Create a dataset large enough that the default 0.05/trace estimate exceeds
    # the 1.00 USD ceiling.
    many = []
    for i in range(40):
        many.append(
            {
                "id": f"EVCOST{i:03d}",
                "category": "happy_path",
                "description": "cost-estimate test",
                "turns": [{"role": "user", "content": "save Test Store, 415-555-9999"}],
                "expected": {},
            }
        )
    dataset = _isolated_dataset(tmp_path, many)
    out_dir = tmp_path / "out"
    monkeypatch.setenv("RUN_EVALS_LIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ci-not-used-in-this-test")
    rc = runner_main(
        [
            "--mode",
            "live",
            "--dataset",
            str(dataset),
            "--out",
            str(out_dir),
        ]
    )
    assert rc == 3, "runner should exit 3 when live cost estimate exceeds the ceiling"


def test_t313_live_mode_aborts_when_gate_env_var_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RUN_EVALS_LIVE", raising=False)
    rc = runner_main(
        ["--mode", "live", "--dataset", str(DATASET_PATH), "--out", str(tmp_path)]
    )
    assert rc == 2


@pytest.fixture(autouse=True)
def _cleanup_eval_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("EVAL_MODE", raising=False)
    yield
