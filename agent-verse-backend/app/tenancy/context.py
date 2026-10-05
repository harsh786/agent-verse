"""Tenant identity, plan tiers, and per-plan limits."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class PlanTier(enum.StrEnum):
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class PlanLimits:
    requests_per_minute: int
    goals_per_day: int
    max_agents: int
    max_api_keys: int
    max_knowledge_collections: int
    goal_timeout_seconds: int


PLAN_LIMITS: dict[PlanTier, PlanLimits] = {
    PlanTier.FREE: PlanLimits(
        requests_per_minute=60,
        goals_per_day=25,
        max_agents=3,
        max_api_keys=2,
        max_knowledge_collections=1,
        goal_timeout_seconds=3600,
    ),
    PlanTier.STARTER: PlanLimits(
        requests_per_minute=120,
        goals_per_day=100,
        max_agents=10,
        max_api_keys=5,
        max_knowledge_collections=10,
        goal_timeout_seconds=7200,
    ),
    PlanTier.PROFESSIONAL: PlanLimits(
        requests_per_minute=600,
        goals_per_day=1_000,
        max_agents=50,
        max_api_keys=20,
        max_knowledge_collections=50,
        goal_timeout_seconds=28_800,
    ),
    PlanTier.ENTERPRISE: PlanLimits(
        requests_per_minute=10_000,
        goals_per_day=50_000,
        max_agents=1_000,
        max_api_keys=100,
        max_knowledge_collections=200,
        goal_timeout_seconds=86_400,
    ),
}


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable identity injected into every authenticated request."""

    tenant_id: str
    plan: PlanTier
    api_key_id: str
    # RBAC roles assigned to this API key / SSO user (expanded from role hierarchy)
    roles: tuple[str, ...] = field(default_factory=tuple)
    # Scopes the API key itself was created with. Empty = no key-level restriction
    # (the roles' scopes apply). Non-empty = the key may use ONLY these scopes, on
    # top of its roles' scopes (TenantMiddleware enforces the intersection).
    scopes: tuple[str, ...] = field(default_factory=tuple)
    # Set when the request authenticated with an agent-scoped API key
    # (``av_agent_*``): the key's agent binding and tool/connector restrictions.
    # It travels with the goal (goals.execution_context) so the worker's tool
    # gate enforces the same restrictions.
    agent_key: AgentKeyRestriction | None = None
    # Set when the request authenticated as a PERSON (an SSO user session):
    # the global ``users.id``. ``None`` for API keys / agent credentials.
    user_id: str | None = None


def _matches(names: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
    import fnmatch

    return any(fnmatch.fnmatchcase(n, p) for n in names for p in patterns)


def _opt_tuple(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value)
    return ()


@dataclass(frozen=True, slots=True)
class AgentKeyRestriction:
    """What an agent-scoped API key may do: one agent, a bounded tool set.

    ``allowed_tools`` / ``allowed_connectors`` of ``None`` mean "no restriction";
    an empty tuple allows nothing. ``denied_tools`` always wins. Patterns are
    fnmatch globs matched against every governance name of a tool (bare,
    connection-qualified and ``<connector id>/<tool>``).
    """

    key_id: str
    agent_id: str
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] = ()
    allowed_connectors: tuple[str, ...] | None = None
    # Fail-closed marker: the goal submitter's restriction could not be read,
    # so no tool may run.
    deny_all: bool = False

    def tool_denial(self, tool_name: str) -> str | None:
        """None when the key may call *tool_name*, else the reason it may not."""
        from app.mcp.tool_naming import governance_names

        if self.deny_all:
            return "the agent key restriction for this goal could not be verified"
        names = governance_names(tool_name)
        if _matches(names, self.denied_tools):
            return f"tool '{tool_name}' is denied to agent key {self.key_id}"
        if self.allowed_tools is not None and not _matches(names, self.allowed_tools):
            return f"tool '{tool_name}' is not in agent key {self.key_id}'s allowed tools"
        if self.allowed_connectors is not None:
            connectors = {n.split("/", 1)[0] for n in names if "/" in n}
            if not connectors & set(self.allowed_connectors):
                return (
                    f"tool '{tool_name}' is not served by a connector agent key "
                    f"{self.key_id} may use"
                )
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "agent_id": self.agent_id,
            "allowed_tools": None if self.allowed_tools is None else list(self.allowed_tools),
            "denied_tools": list(self.denied_tools),
            "allowed_connectors": (
                None if self.allowed_connectors is None else list(self.allowed_connectors)
            ),
            "deny_all": self.deny_all,
        }

    @classmethod
    def from_dict(cls, data: object) -> AgentKeyRestriction:
        """Rebuild from :meth:`to_dict`; anything malformed denies every tool."""
        if not isinstance(data, dict) or not data.get("key_id") or not data.get("agent_id"):
            return cls(key_id="unknown", agent_id="unknown", deny_all=True)
        return cls(
            key_id=str(data["key_id"]),
            agent_id=str(data["agent_id"]),
            allowed_tools=_opt_tuple(data.get("allowed_tools")),
            denied_tools=_opt_tuple(data.get("denied_tools")) or (),
            allowed_connectors=_opt_tuple(data.get("allowed_connectors")),
            deny_all=bool(data.get("deny_all", False)),
        )
