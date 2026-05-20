"""Tests for store_assistant.state_projection — T605, T606, T613."""

from __future__ import annotations

from store_assistant.state_projection import (
    REDACTED_PASSPHRASE,
    REDACTED_PHONE,
    project_state_for_inspector,
)

PASSPHRASE = "open-sesame"


def test_t605_passphrase_redacted_at_top_level_and_when_nested() -> None:
    """T605: passphrase value redacted in nested non-obvious keys too."""
    raw = {
        "thread_id": "t-1",
        "passphrase": PASSPHRASE,
        "deep": {
            "intermediate": {
                "candidate_secret": PASSPHRASE,
                "label": "ok",
            },
            "list_of_strings": ["literal", PASSPHRASE, "noise"],
        },
    }
    out = project_state_for_inspector(raw, PASSPHRASE)
    assert out["passphrase"] == REDACTED_PASSPHRASE
    assert out["deep"]["intermediate"]["candidate_secret"] == REDACTED_PASSPHRASE
    assert out["deep"]["intermediate"]["label"] == "ok"
    assert out["deep"]["list_of_strings"][1] == REDACTED_PASSPHRASE
    assert out["deep"]["list_of_strings"][0] == "literal"


def test_t606_phone_regex_redacted() -> None:
    """T606: NANP phone shapes get masked regardless of where they sit."""
    raw = {
        "primary": "415-555-1212",
        "with_paren": "(212) 555-7777",
        "international": "+1 415 555 1212",
        "not_a_phone": "415-12-8",
        "stash": ["408-555-9999", "hello"],
    }
    out = project_state_for_inspector(raw, PASSPHRASE)
    assert out["primary"] == REDACTED_PHONE
    assert out["with_paren"] == REDACTED_PHONE
    assert out["international"] == REDACTED_PHONE
    assert out["not_a_phone"] == "415-12-8"
    assert out["stash"][0] == REDACTED_PHONE
    assert out["stash"][1] == "hello"


def test_t613_passphrase_substring_inside_user_input_masked() -> None:
    """T613: the demo moment.

    User echoes the passphrase inside a longer message ("is the passphrase
    'open-sesame'?"). The state inspector must still mask it — or the
    inspector itself becomes a passphrase exfiltration vector.
    """
    raw = {
        "thread_id": "t-1",
        "recent_user_inputs": [
            "Save Whole Foods, 415-555-2222",
            f"is the passphrase '{PASSPHRASE}' correct?",
            "ok bye",
        ],
    }
    out = project_state_for_inspector(raw, PASSPHRASE)
    rendered = out["recent_user_inputs"][1]
    assert PASSPHRASE not in rendered, (
        f"passphrase leaked into projected state: {rendered!r}"
    )
    assert REDACTED_PASSPHRASE in rendered


def test_long_string_truncated() -> None:
    """Sanity: 200+ char strings are truncated."""
    raw = {"note": "x" * 250}
    out = project_state_for_inspector(raw, PASSPHRASE)
    assert out["note"].startswith("x" * 100)
    assert out["note"].endswith("...[truncated]")


def test_recursion_depth_capped_without_raising() -> None:
    """Deeply nested input must not crash."""
    deep: object = "leaf"
    for _ in range(50):
        deep = {"x": deep}
    # No exception — depth cap kicks in.
    project_state_for_inspector({"root": deep}, PASSPHRASE)
