"""Upper bounds for the legacy knowledge connectors (``/knowledge/ingest/*``).

The request models validate against these; the ingestors clamp to them too, so
a caller that bypasses the API models cannot crawl an unbounded estate.
"""

from __future__ import annotations

MAX_GITHUB_FILES = 2_000
MAX_CONFLUENCE_PAGES = 5_000
MAX_JIRA_ISSUES = 5_000
MAX_SLACK_MESSAGES = 10_000
MAX_RPA_CHARS = 500_000


def clamp_limit(value: int, cap: int) -> int:
    """*value* bounded to ``[1, cap]``."""
    return max(1, min(int(value), cap))


__all__ = [
    "MAX_CONFLUENCE_PAGES",
    "MAX_GITHUB_FILES",
    "MAX_JIRA_ISSUES",
    "MAX_RPA_CHARS",
    "MAX_SLACK_MESSAGES",
    "clamp_limit",
]
