"""Grant model + scope matching."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime


def scope_matches(granted_scopes: tuple[str, ...], requested: str) -> bool:
    """True if any granted scope glob covers the requested tool.

    Scopes are tool-name globs: ``"*"`` covers everything, ``"jira.*"`` covers
    ``"jira.search"``; an exact scope covers only that tool. Matching is
    case-insensitive.
    """
    req = requested.strip().lower()
    if not req:
        return False
    return any(fnmatch.fnmatchcase(req, scope.strip().lower()) for scope in granted_scopes)


@dataclass(frozen=True)
class Grant:
    """Time-limited, revocable authority delegated to an agent.

    ``grantor`` is the human/org that granted it; ``grantee_agent_id`` the agent
    it authorizes. ``scopes`` are tool-name globs; ``max_cost_usd`` optionally
    caps spend authorized by this grant (None = uncapped).
    """

    grant_id: str
    tenant_id: str
    grantor: str
    grantee_agent_id: str
    scopes: tuple[str, ...]
    not_before: datetime
    expires_at: datetime
    max_cost_usd: float | None = None
    revoked: bool = False
    parent_grant_id: str | None = None  # set for delegated (narrowed) grants
    metadata: dict[str, str] = field(default_factory=dict)

    def is_active(self, now: datetime) -> bool:
        return (not self.revoked) and self.not_before <= now < self.expires_at

    def covers(self, tool_name: str, now: datetime, *, cost_usd: float = 0.0) -> bool:
        """True if this grant currently authorizes ``tool_name`` within its cost cap."""
        if not self.is_active(now):
            return False
        if self.max_cost_usd is not None and cost_usd > self.max_cost_usd:
            return False
        return scope_matches(self.scopes, tool_name)


__all__ = ["Grant", "scope_matches"]
