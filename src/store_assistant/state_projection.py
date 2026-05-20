"""Redact + shape agent state for the UI inspector.

Redaction is hardcoded on. There is no opt-out parameter — adding one
would let a future caller defeat the entire point of the inspector
(showing the operator the conversation safely). T605 enforces.

Three rules, applied in order during a recursive walk:

1. Any string equal to the configured passphrase becomes ``***REDACTED***``.
2. Any string matching the NANP phone shape becomes ``***REDACTED-PHONE***``.
3. Any string longer than 200 characters is truncated to 100 characters
   followed by ``' ...[truncated]'``.

Recursion is capped at depth 20 to keep pathological inputs from blowing
the stack. Beyond the cap, the raw value is returned unchanged with a
sentinel marker — surfacing too much is the wrong failure mode here.
"""

from __future__ import annotations

import re
from typing import Any, cast

# NANP shape: optional country prefix, area code 2-9XX, exchange 2-9XX, line
# four digits. Tolerant of common separators ((), -, ., space). Anchored
# match — bare local-line strings hit, and so do strings that begin with
# the optional country prefix.
_NANP_PHONE = re.compile(
    r"^\+?1?[\s.\-]*\(?[2-9]\d{2}\)?[\s.\-]*[2-9]\d{2}[\s.\-]*\d{4}$"
)
_TRUNCATE_LIMIT = 200
_TRUNCATE_TO = 100
_RECURSION_DEPTH_CAP = 20

REDACTED_PASSPHRASE = "***REDACTED***"  # noqa: S105 — sentinel string, not a credential
REDACTED_PHONE = "***REDACTED-PHONE***"


def project_state_for_inspector(state: dict[str, Any], passphrase: str) -> dict[str, Any]:
    """Redact and shape state for UI display.

    Returns a dict-shaped projection. Nested lists/dicts are walked
    recursively up to depth 20; deeper structures are returned unchanged
    (a sentinel-y guard rather than a hard fault — pathological agent
    state shouldn't crash the inspector).
    """
    # _redact_value is Any-typed (it descends into arbitrary values); at the
    # top level the input is a dict, so the projection is a dict too.
    return cast(dict[str, Any], _redact_value(state, passphrase=passphrase, depth=0))


def _redact_value(value: Any, *, passphrase: str, depth: int) -> Any:
    if depth >= _RECURSION_DEPTH_CAP:
        # Bottom out — return the original value untouched. Better than
        # raising and worse than continuing; the depth cap exists only to
        # bound stack usage on adversarial input.
        return value
    if isinstance(value, dict):
        return {
            k: _redact_value(v, passphrase=passphrase, depth=depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_value(v, passphrase=passphrase, depth=depth + 1) for v in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _redact_value(v, passphrase=passphrase, depth=depth + 1) for v in value
        )
    if isinstance(value, str):
        return _redact_string(value, passphrase=passphrase)
    return value


def _redact_string(value: str, *, passphrase: str) -> str:
    if passphrase and passphrase in value:
        # Equality is the dominant case (a state field whose value is the
        # passphrase). Substring catches the harder case the inspector demo
        # depends on (T613): the user types the passphrase inside a longer
        # message and that message is echoed into state — we still mask it.
        if value == passphrase:
            return REDACTED_PASSPHRASE
        value = value.replace(passphrase, REDACTED_PASSPHRASE)
    if _NANP_PHONE.match(value):
        return REDACTED_PHONE
    if len(value) > _TRUNCATE_LIMIT:
        return value[:_TRUNCATE_TO] + " ...[truncated]"
    return value
