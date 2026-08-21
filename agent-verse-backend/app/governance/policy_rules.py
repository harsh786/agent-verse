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
