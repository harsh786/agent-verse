"""Declarative policy-as-code evaluator.

Rule format (JSON):
{
  "name": "block-external-email",
  "conditions": [
    {"field": "tool_name", "op": "contains", "value": "send_email"},
    {"field": "arguments.to", "op": "not_ends_with", "value": "@company.com"}
  ],
  "logic": "AND",
  "action": "deny",
  "message": "Emails only to @company.com"
}

Operators: eq, ne, contains, not_contains, ends_with, not_ends_with,
           starts_with, not_starts_with, in, not_in, gt, lt, gte, lte, regex
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PolicyRuleResult:
    allowed: bool
    rule_name: str = ""
    message: str = ""
    matched_conditions: list[str] = field(default_factory=list)


def _get_field(context: dict[str, Any], field_path: str) -> Any:
    parts = field_path.split(".")
    val: Any = context
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val


def _matches(val: Any, op: str, target: Any) -> bool:
    s = str(val or "").lower()
    t = str(target or "").lower()
    if op == "eq":
        return s == t
    if op == "ne":
        return s != t
    if op == "contains":
        return t in s
    if op == "not_contains":
        return t not in s
    if op == "ends_with":
        return s.endswith(t)
    if op == "not_ends_with":
        return not s.endswith(t)
    if op == "starts_with":
        return s.startswith(t)
    if op == "not_starts_with":
        return not s.startswith(t)
    if op == "in":
        items = [str(x).lower() for x in (target if isinstance(target, list) else [target])]
        return s in items
    if op == "not_in":
        items = [str(x).lower() for x in (target if isinstance(target, list) else [target])]
        return s not in items
    try:
        if op == "gt":
            return float(val or 0) > float(target or 0)
        if op == "lt":
            return float(val or 0) < float(target or 0)
        if op == "gte":
            return float(val or 0) >= float(target or 0)
        if op == "lte":
            return float(val or 0) <= float(target or 0)
    except (TypeError, ValueError):
        return False
    if op == "regex":
        try:
            return bool(re.search(str(target), str(val or "")))
        except re.error:
            return False
    return False


def evaluate_rule(rule: dict[str, Any], context: dict[str, Any]) -> PolicyRuleResult:
    conditions: list[dict] = rule.get("conditions", [])
    logic: str = rule.get("logic", "AND").upper()
    action: str = rule.get("action", "deny")
    name: str = rule.get("name", "unnamed")
    message: str = rule.get("message", f"Blocked by policy: {name}")

    if not conditions:
        return PolicyRuleResult(allowed=True)

    results = [
        _matches(_get_field(context, c.get("field", "")), c.get("op", "eq"), c.get("value"))
        for c in conditions
    ]
    triggered = all(results) if logic == "AND" else any(results)

    if triggered and action == "deny":
        return PolicyRuleResult(
            allowed=False,
            rule_name=name,
            message=message,
            matched_conditions=[c.get("field", "") for c in conditions],
        )
    return PolicyRuleResult(allowed=True, rule_name=name)


def evaluate_rules(rules: list[dict[str, Any]], context: dict[str, Any]) -> PolicyRuleResult:
    for rule in rules:
        result = evaluate_rule(rule, context)
        if not result.allowed:
            return result
    return PolicyRuleResult(allowed=True)


# ── Enforcement on execution paths (POL-01) ──────────────────────────────────
# The rules were CRUD + dry-run only: no execution path read them. Every tool
# call (AgentGraph executor and the workflow GovernedToolGate) now evaluates the
# tenant's active rules. Rules are cached per process for RULES_CACHE_TTL_S, so a
# change made on one replica binds on every replica/worker within that window
# (and immediately on the replica that made it, which invalidates its cache).

RULES_CACHE_TTL_S = 15.0
_RULES_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class PolicyRulesUnavailableError(RuntimeError):
    """The tenant's policy rules could not be loaded (and none are cached)."""


def invalidate_policy_rules(tenant_id: str | None = None) -> None:
    if tenant_id is None:
        _RULES_CACHE.clear()
    else:
        _RULES_CACHE.pop(tenant_id, None)


async def load_active_policy_rules(db_factory: Any, tenant_id: str) -> list[dict[str, Any]]:
    """The tenant's active rule documents (stale cache on a DB error, else raise)."""
    import time

    now = time.monotonic()
    hit = _RULES_CACHE.get(tenant_id)
    if hit is not None and now - hit[0] < RULES_CACHE_TTL_S:
        return hit[1]
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db_factory() as session, sqlalchemy_rls_context(session, tenant_id):
            rows = (
                await session.execute(
                    text(
                        "SELECT rule_json FROM policy_rules "
                        "WHERE tenant_id = :tid AND is_active = true ORDER BY name"
                    ),
                    {"tid": tenant_id},
                )
            ).fetchall()
    except Exception as exc:
        if hit is not None:
            return hit[1]  # stale but real
        raise PolicyRulesUnavailableError(str(exc)) from exc
    rules = [r[0] for r in rows if isinstance(r[0], dict)]
    _RULES_CACHE[tenant_id] = (now, rules)
    return rules


async def policy_rules_denial(
    db_factory: Any, tenant_id: str, context: dict[str, Any]
) -> str | None:
    """Why the tenant's policy-as-code rules deny *context*, or ``None``.

    An unloadable rule set denies (fail closed): the tenant may have a deny rule
    for exactly this call.
    """
    if db_factory is None or not tenant_id:
        return None
    try:
        rules = await load_active_policy_rules(db_factory, tenant_id)
    except PolicyRulesUnavailableError:
        return "policy rules could not be loaded; failing closed"
    if not rules:
        return None
    result = evaluate_rules(rules, context)
    if result.allowed:
        return None
    return f"denied by policy rule '{result.rule_name}': {result.message}"
