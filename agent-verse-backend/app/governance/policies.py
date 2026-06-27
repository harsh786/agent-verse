"""Policy engine — evaluates tool calls against named policies.

Policies layer on top of the permission matrix:
  - A policy declares lists of denied tools.
  - Multiple policies stack (most restrictive wins).
  - Tool matching supports exact names or glob prefixes.
"""

from __future__ import annotations

import enum
import fnmatch
from dataclasses import dataclass, field

from app.tenancy.context import TenantContext


class PolicyResult(enum.StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class Policy:
    name: str
    description: str
    denied_tools: list[str] = field(default_factory=list)
    approval_tools: list[str] = field(default_factory=list)
    scope: str = "global"
    allowed_hours_utc: tuple[int, int] | None = None  # (start_hour, end_hour) UTC
    allowed_weekdays: list[int] | None = None  # 0=Monday ... 6=Sunday
    tenant_id: str = ""


@dataclass
class GovernancePolicy:
    """Lightweight policy record used for tenant-scoped policy isolation.

    This is distinct from :class:`Policy` (which drives the engine evaluation
    logic) and serves as a simple data holder for per-tenant policy records.
    """

    name: str
    action: str
    tool_pattern: str = ""
    tenant_id: str = ""


class PolicyEngine:
    """Evaluates tool calls against a set of policies.

    Policies are intentionally stateless — they don't reference tenant context
    directly; the caller scopes which policies apply to a tenant.
    """

    def __init__(self, policies: list[Policy] | None = None) -> None:
        self._policies: list[Policy] = policies or []

    def add_policy(self, policy: Policy) -> None:
        self._policies.append(policy)

    def _is_within_time_window(self, policy: Policy) -> bool:
        """Returns True if current UTC time is within policy's allowed window."""
        from datetime import UTC, datetime
        if policy.allowed_hours_utc is None and policy.allowed_weekdays is None:
            return True  # No time restriction
        now = datetime.now(UTC)
        if policy.allowed_weekdays is not None:
            if now.weekday() not in policy.allowed_weekdays:
                return False
        if policy.allowed_hours_utc is not None:
            start_h, end_h = policy.allowed_hours_utc
            if not (start_h <= now.hour < end_h):
                return False
        return True

    def evaluate(self, tool_name: str, *, tenant_ctx: TenantContext) -> PolicyResult:
        for policy in self._policies:
            if not self._is_within_time_window(policy):
                continue  # Time window not active, skip this policy
            for pattern in policy.denied_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    return PolicyResult.DENY
            for pattern in policy.approval_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    return PolicyResult.REQUIRE_APPROVAL
        return PolicyResult.ALLOW
