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


class PolicyEngine:
    """Evaluates tool calls against a set of policies.

    Policies are intentionally stateless — they don't reference tenant context
    directly; the caller scopes which policies apply to a tenant.
    """

    def __init__(self, policies: list[Policy] | None = None) -> None:
        self._policies: list[Policy] = policies or []

    def add_policy(self, policy: Policy) -> None:
        self._policies.append(policy)

    def evaluate(self, tool_name: str, *, tenant_ctx: TenantContext) -> PolicyResult:
        for policy in self._policies:
            for pattern in policy.denied_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    return PolicyResult.DENY
            for pattern in policy.approval_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    return PolicyResult.REQUIRE_APPROVAL
        return PolicyResult.ALLOW
