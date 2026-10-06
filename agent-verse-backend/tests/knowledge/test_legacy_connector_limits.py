"""Regression: legacy knowledge connector limits had no upper bound.

``max_files`` / ``max_pages`` / ``max_issues`` / ``max_messages`` / ``max_chars``
on the legacy ``/knowledge/ingest/*`` routes were plain ``int`` fields — any
value (10**9, or negative) was accepted and passed straight to the ingestors,
so one request could crawl an entire Confluence/Jira/Slack/GitHub estate into
the tenant's collection (and its embedding bill). The request models now bound
them, and the ingestors clamp independently (defense in depth).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.api import knowledge as api
from app.knowledge.ingestors.limits import (
    MAX_CONFLUENCE_PAGES,
    MAX_GITHUB_FILES,
    MAX_JIRA_ISSUES,
    MAX_SLACK_MESSAGES,
    clamp_limit,
)

_SECRET = SecretStr("t")


@pytest.mark.parametrize(
    ("model", "field", "cap", "extra"),
    [
        # RepoIngestRequest is bounded by RepositoryLimits in its handler (400).
        (
            api.GitHubIngestRequest,
            "max_files",
            MAX_GITHUB_FILES,
            {"collection_id": "c", "owner": "o", "repo": "r"},
        ),
        (
            api.ConfluenceIngestRequest,
            "max_pages",
            MAX_CONFLUENCE_PAGES,
            {"collection_id": "c", "base_url": "u", "space_key": "s", "token": _SECRET, "user": "u"},
        ),
        (
            api.JiraIngestRequest,
            "max_issues",
            MAX_JIRA_ISSUES,
            {"collection_id": "c", "base_url": "u", "project_key": "p", "token": _SECRET, "user": "u"},
        ),
        (
            api.SlackIngestRequest,
            "max_messages",
            MAX_SLACK_MESSAGES,
            {"collection_id": "c", "channel_id": "C1", "token": _SECRET},
        ),
    ],
)
def test_request_limits_are_bounded(model: Any, field: str, cap: int, extra: dict[str, Any]) -> None:
    assert getattr(model(**extra, **{field: cap}), field) == cap
    with pytest.raises(ValidationError):
        model(**extra, **{field: cap + 1})
    with pytest.raises(ValidationError):
        model(**extra, **{field: 0})


def test_rpa_max_chars_is_bounded() -> None:
    with pytest.raises(ValidationError):
        api.RpaUrlIngestRequest(collection_id="c", urls=["https://x"], max_chars=10**9)


def test_clamp_limit() -> None:
    assert clamp_limit(10**9, 500) == 500
    assert clamp_limit(-5, 500) == 1
    assert clamp_limit(20, 500) == 20
