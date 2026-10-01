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


_RESTRICTIVENESS = {ActionLevel.DENY: 2, ActionLevel.APPROVAL: 1}


def match_rule(
    rules: tuple[AgentPermissionRule, ...], tool_name: str
) -> AgentPermissionRule | None:
    """The rule governing ``tool_name``.

    The tool may be addressed as the bare tool (any connection), the
    connection-qualified tool (``orders_db__mongodb_find``) or the connection id
    (``<connector id>/mongodb_find``) — see app.mcp.tool_naming. An exact rule
    wins (the most specific form first), then the longest glob; equally specific
    globs resolve to the MORE restrictive level, so a broad allow never
    overrides an equally specific deny.
    """
    from app.mcp.tool_naming import governance_names

    names = governance_names(tool_name)
    for name in names:
        exact = [r for r in rules if r.tool_name == name]
        if exact:
            return exact[0]
    best: AgentPermissionRule | None = None
    best_key: tuple[int, int, int] | None = None
    for index, name in enumerate(names):
        lowered = name.lower()
        for rule in rules:
            if not fnmatch.fnmatch(lowered, rule.tool_name.lower()):
                continue
            key = (
                len(rule.tool_name.replace("*", "")),
                _RESTRICTIVENESS.get(rule.level, 0),
                -index,
            )
            if best_key is None or key > best_key:
                best, best_key = rule, key
    return best


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


# (tenant, agent, tool, utc-date) -> calls; used only when no Redis is wired.
_LOCAL_DAILY: dict[tuple[str, str, str, str], int] = {}


class DailyLimitUnavailableError(RuntimeError):
    """The shared daily-call counter could not be read/updated (refuse the call)."""


async def reserve_daily_call(
    redis: Any, tenant_id: str, agent_id: str, tool_name: str, limit: int
) -> bool:
    """Count one call against the rule's ``daily_limit``; False when exhausted.

    ``daily_limit`` was loaded and stored but never checked (only the per-goal
    limit was), so "10 calls/day" allowed unlimited calls across goals. The
    counter is per (tenant, agent, tool, UTC day): Redis when wired — shared by
    every replica and worker — else in-process. A Redis error raises
    :class:`DailyLimitUnavailableError` so the caller can fail closed.
    """
    from datetime import UTC, datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    if redis is None:
        key_local = (tenant_id, agent_id, tool_name, day)
        used = _LOCAL_DAILY.get(key_local, 0)
        if used >= limit:
            return False
        _LOCAL_DAILY[key_local] = used + 1
        return True
    key = f"agent_perm_daily:{tenant_id}:{agent_id}:{tool_name}:{day}"
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, 2 * 86400)
        if count > limit:
            await redis.decr(key)
            return False
        return True
    except Exception as exc:
        raise DailyLimitUnavailableError(str(exc)) from exc


__all__ = [
    "AgentPermissionRule",
    "AgentPermissionsUnavailableError",
    "DailyLimitUnavailableError",
    "invalidate_agent_permissions",
    "load_agent_permissions",
    "match_rule",
    "reserve_daily_call",
    "resolve_level",
]
