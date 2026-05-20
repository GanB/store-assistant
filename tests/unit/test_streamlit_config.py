"""T622: lock down .streamlit/config.toml invariants.

The config carries the neutral theme + minimal toolbar mode that the
demo UI depends on. Drift here would silently change the visual surface
on the next deploy, which the existing snapshot tests do not catch.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / ".streamlit" / "config.toml"


def test_t622_streamlit_config_exists_with_minimal_toolbar() -> None:
    assert CONFIG_PATH.is_file(), (
        f"Expected {CONFIG_PATH.relative_to(REPO_ROOT)} to exist; the demo UI "
        "depends on it for the neutral theme and minimal toolbar."
    )
    parsed = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    client = parsed.get("client") or {}
    assert client.get("toolbarMode") == "minimal", (
        "[client] toolbarMode must be 'minimal' so the rerun / share "
        "controls don't dominate the chat surface."
    )


def test_t622_streamlit_config_has_neutral_theme() -> None:
    """The theme block must declare a neutral, non-branded palette.

    We don't snapshot the exact colour values — those are tunable as
    long as the base remains light and no branded primaries leak in.
    What we lock down is the SHAPE (the keys are present) and the
    base ('light').
    """
    parsed = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    theme = parsed.get("theme") or {}
    assert theme.get("base") == "light"
    for key in (
        "primaryColor",
        "backgroundColor",
        "secondaryBackgroundColor",
        "textColor",
        "font",
    ):
        assert key in theme, f"theme missing required key {key!r}"
