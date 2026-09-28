"""Enforcement of the per-agent permissions persisted in ``agent_permissions``.

``PUT /agents/{id}/permissions`` wrote rows to ``agent_permissions`` but the
executor only consulted the in-memory, per-tenant ``PermissionMatrix`` — the
persisted per-agent rules were never enforced. This module loads them (per
tenant + agent, under RLS, with a short per-process TTL cache so every replica
and Celery worker converges on the DB state) and resolves a tool call to an
:class:`ActionLevel`.

Resolution: an exact ``tool_name`` rule wins over glob rules; among globs the
most specific (longest pattern) wins. A rule whose ``scope_pattern`` does not
match the call's scope value resolves to DENY. An unrecognised stored level is
treated as DENY (fail closed). No matching rule → ``None`` (no per-agent
opinion; the other governance layers decide).
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass
from typing import Any

from app.governance.permissions import ActionLevel
from app.observability.logging import get_logger

logger = get_logger(__name__)

CACHE_TTL_S = 30.0

_LEVEL_ALIASES: dict[str, ActionLevel] = {
    "allow": ActionLevel.ALLOW,
    "allow_log": ActionLevel.ALLOW_LOG,
    "approval": ActionLevel.APPROVAL,
    "require_approval": ActionLevel.APPROVAL,
    "deny": ActionLevel.DENY,
    "block": ActionLevel.DENY,
}


class AgentPermissionsUnavailableError(RuntimeError):
    """The agent's persisted permissions could not be loaded (and none cached)."""


@dataclass(frozen=True)
class AgentPermissionRule:
    tool_name: str
    level: ActionLevel
    daily_limit: int | None = None
    per_goal_limit: int | None = None
    scope_pattern: str | None = None


# (tenant_id, agent_id) -> (loaded_at_monotonic, rules)
_CACHE: dict[tuple[str, str], tuple[float, tuple[AgentPermissionRule, ...]]] = {}


def invalidate_agent_permissions(tenant_id: str, agent_id: str | None = None) -> None:
    """Drop this process's cached rules (call after a permissions write)."""
    if agent_id is None:
        for key in [k for k in _CACHE if k[0] == tenant_id]:
            _CACHE.pop(key, None)
    else:
        _CACHE.pop((tenant_id, agent_id), None)


def _parse_level(raw: Any) -> ActionLevel:
    level = _LEVEL_ALIASES.get(str(raw or "").strip().lower())
    if level is None:
        logger.warning("agent_permission_unknown_level_denied", level=str(raw)[:40])
        return ActionLevel.DENY
    return level


async def load_agent_permissions(
    db_factory: Any, tenant_id: str, agent_id: str
) -> tuple[AgentPermissionRule, ...]:
    """Rules for (tenant, agent). Stale cache is served on a DB error; with no
    cache the error is raised as :class:`AgentPermissionsUnavailableError`."""
    key = (tenant_id, agent_id)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT tool_name, level, daily_limit, per_goal_limit, scope_pattern "
                        "FROM agent_permissions WHERE tenant_id = :tid AND agent_id = :aid"
                    ),
                    {"tid": tenant_id, "aid": agent_id},
                )
            ).fetchall()
    except Exception as exc:
        logger.warning(
            "agent_permissions_load_failed",
            tenant_id=tenant_id,
            agent_id=agent_id,
            error=str(exc)[:200],
        )
        if hit is not None:
            return hit[1]
        raise AgentPermissionsUnavailableError(
            f"permissions for agent {agent_id} unavailable"
        ) from exc
    rules = tuple(
        AgentPermissionRule(
            tool_name=str(r[0] or "*"),
            level=_parse_level(r[1]),
            daily_limit=int(r[2]) if r[2] else None,
            per_goal_limit=int(r[3]) if r[3] else None,
            scope_pattern=str(r[4]) if r[4] else None,
        )
        for r in rows
    )
    _CACHE[key] = (now, rules)
    return rules


def match_rule(
    rules: tuple[AgentPermissionRule, ...], tool_name: str
) -> AgentPermissionRule | None:
    exact = [r for r in rules if r.tool_name == tool_name]
    if exact:
        return exact[0]
    lowered = tool_name.lower()
    globs = [r for r in rules if fnmatch.fnmatch(lowered, r.tool_name.lower())]
    if not globs:
        return None
    return max(globs, key=lambda r: len(r.tool_name.replace("*", "")))


def resolve_level(
    rules: tuple[AgentPermissionRule, ...],
    tool_name: str,
    *,
    scope_value: str | None = None,
    goal_call_count: int = 0,
) -> tuple[ActionLevel | None, AgentPermissionRule | None, str]:
    """Return (level, matched_rule, reason). ``level`` None = no per-agent rule."""
    rule = match_rule(rules, tool_name)
    if rule is None:
        return None, None, "no_rule"
    if (
        scope_value is not None
        and rule.scope_pattern
        and rule.scope_pattern != "*"
        and not fnmatch.fnmatch(scope_value, rule.scope_pattern)
    ):
        return ActionLevel.DENY, rule, "scope_mismatch"
    if rule.per_goal_limit is not None and goal_call_count >= rule.per_goal_limit:
        return ActionLevel.DENY, rule, "per_goal_limit_reached"
    return rule.level, rule, "rule"


__all__ = [
    "AgentPermissionRule",
    "AgentPermissionsUnavailableError",
    "invalidate_agent_permissions",
    "load_agent_permissions",
    "match_rule",
    "resolve_level",
]
