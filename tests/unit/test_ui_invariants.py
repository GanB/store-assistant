"""UI surface guards: T612, T615, T616, T618, T619, T620.

These are static-content tests — they keep removed/restricted patterns
from creeping back through future changes."""

from __future__ import annotations

from pathlib import Path

import pytest

from store_assistant import threads
from store_assistant.ui.streamlit_app import (
    should_show_reload_button,
    summary_button_label,
)

PKG_ROOT = Path(__file__).resolve().parents[2] / "src" / "store_assistant"
UI_FILE = PKG_ROOT / "ui" / "streamlit_app.py"


def _ui_source() -> str:
    return UI_FILE.read_text()


def _pkg_files() -> list[Path]:
    return [
        p
        for p in PKG_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and p.is_file()
    ]


def test_t612_no_hardcoded_threshold_in_ui() -> None:
    """T612: badge interpolates from settings; no `threshold: 3` literal."""
    src = _ui_source()
    assert "threshold: 3" not in src, (
        "Hardcoded 'threshold: 3' found in UI module — must read from "
        "settings.app_off_scope_threshold so the badge tracks config"
    )
    # Sanity: the dynamic interpolation is wired.
    assert "app_off_scope_threshold" in src


@pytest.mark.parametrize(
    ("is_terminated", "expected"),
    [(True, "View summary"), (False, "Resume")],
)
def test_t615_summary_button_label(is_terminated: bool, expected: str) -> None:
    """T615: View summary on terminated, Resume on active."""
    assert summary_button_label(is_terminated=is_terminated) == expected


@pytest.mark.parametrize(
    ("turn_count", "expected"),
    [(0, False), (1, True), (5, True)],
)
def test_t616_reload_button_visibility(turn_count: int, expected: bool) -> None:
    """T616: Reload from last checkpoint hidden when turn_count == 0."""
    assert should_show_reload_button(turn_count) is expected


def test_t618_no_simulate_worker_restart_label() -> None:
    """T618: the rebranded label is gone everywhere in the package."""
    for f in _pkg_files():
        assert "Simulate worker restart" not in f.read_text(), (
            f"Stale label 'Simulate worker restart' found in {f}"
        )


def test_t619_no_checkpoint_inspector_label() -> None:
    """T619: the removed UI panel's label is gone from the package."""
    for f in _pkg_files():
        assert "Checkpoint inspector" not in f.read_text(), (
            f"Stale 'Checkpoint inspector' label found in {f}"
        )


def test_t620_no_fork_button_label_but_api_alive() -> None:
    """T620: 'Fork' button label gone; fork_thread() still importable."""
    src = _ui_source()
    # We tolerate substrings like 'forked' or 'forking' in code comments;
    # what we forbid is the literal button label "Fork" with the surrounding
    # surface. A pragmatic guard: there is no st.button("Fork", ...) anywhere.
    assert 'st.button("Fork"' not in src
    assert "st.button('Fork'" not in src
    # API still callable.
    assert callable(threads.fork_thread)
