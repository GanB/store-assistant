"""Tiny TTL cache helper used by the Streamlit sidebar panels.

Streamlit reruns the script on every interaction, so any per-turn
aggregate query would fire several times per visible user action without
caching. This helper is a one-line memoiser keyed by a string and
parameterised by a TTL — pure, no streamlit imports — so the cache
behaviour is unit-testable without spinning up a Streamlit context.
"""

from __future__ import annotations

import time
from collections.abc import Callable, MutableMapping
from typing import Any


def cache_get_or_compute(
    holder: MutableMapping[str, tuple[float, Any]],
    key: str,
    fetcher: Callable[[], Any],
    ttl_seconds: float,
    now: float | None = None,
) -> Any:
    """Return holder[key]'s value if it was set within ttl_seconds, else
    call fetcher() and store the result keyed by `key` with timestamp now.

    `now` is parameter-injected (defaulting to time.monotonic()) so the
    test suite can drive the TTL boundary deterministically.
    """
    t = now if now is not None else time.monotonic()
    cached = holder.get(key)
    if cached is not None and (t - cached[0]) < ttl_seconds:
        return cached[1]
    val = fetcher()
    holder[key] = (t, val)
    return val
