"""LangSmith deep-link builders.

We never iframe LangSmith — its X-Frame-Options and auth model would
make that brittle. The UI links out to LangSmith in a new tab.

URL formats are versioned by the LangSmith app and have shifted before;
verify against the actual app on each upgrade. The current shapes:

  Run:           /o/<org>/projects/p/<project>/r/<run_id>
  Thread filter: /o/<org>/projects/p/<project>?filter=<jsonquoted>

If LANGSMITH_ORG isn't set we fall back to the org-less shape so the
link still resolves; if LANGSMITH_PROJECT isn't set we return None so
callers can hide the link rather than render a broken URL.
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote

_BASE = "https://smith.langchain.com"


def _env(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _project_segment(org: str, project: str) -> str:
    return f"/o/{quote(org, safe='')}/projects/p/{quote(project, safe='')}"


def trace_url(run_id: str) -> str | None:
    """Deep-link to a specific run in LangSmith.

    Returns None unless both LANGSMITH_ORG and LANGSMITH_PROJECT are set
    — building a /o/.../p/.../r/... URL with an unknown org segment
    yields a broken link, so we'd rather show nothing than a 404. T610
    pins this contract.
    """
    project = _env("LANGSMITH_PROJECT")
    org = _env("LANGSMITH_ORG")
    if not project or not org:
        return None
    return f"{_BASE}{_project_segment(org, project)}/r/{quote(run_id, safe='')}"


def thread_url(thread_id: str) -> str | None:
    """Deep-link to a LangSmith project filtered to a thread_id.

    Same pairing requirement as trace_url. The filter parameter is
    JSON-encoded and URL-quoted so spaces and braces survive the round
    trip.
    """
    project = _env("LANGSMITH_PROJECT")
    org = _env("LANGSMITH_ORG")
    if not project or not org:
        return None
    filter_json = json.dumps({"thread_id": thread_id}, separators=(",", ":"))
    return (
        f"{_BASE}{_project_segment(org, project)}?filter="
        f"{quote(filter_json, safe='')}"
    )
