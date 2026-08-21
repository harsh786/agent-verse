"""Guardrails 2.0 evaluation engine."""

from __future__ import annotations

import datetime
import logging
import re
import uuid
from typing import Any

from app.guardrails_v2.models import (
    GuardrailAction,
    GuardrailLayer,
    GuardrailRule,
    GuardrailViolation,
    ViolationCategory,
)

_log = logging.getLogger(__name__)

# Simple pattern sets for deterministic checks
_PII_PATTERNS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "SSN"),
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b", "Visa card"),
    (r"\b5[1-5][0-9]{14}\b", "Mastercard"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "Email"),
    (r"\b(?:\+1)?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", "Phone"),
]

_SECRET_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI API key"),
    (r"sk-ant-[a-zA-Z0-9]{20,}", "Anthropic API key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub token"),
    (r"AIza[0-9A-Za-z-_]{35}", "Google API key"),
    (r'(?i)password\s*[=:]\s*["\']?[\w!@#$%^&*]+', "Password in text"),
]

_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"disregard\s+(all\s+)?previous",
    r"forget\s+(everything|all)",
    r"you\s+are\s+now\s+",
    r"pretend\s+(you\s+are|to\s+be)",
    r"act\s+as\s+",
    r"jailbreak",
    r"dan\s+mode",
]


class GuardrailsEngine:
    """Evaluates content against guardrail rules."""

    def __init__(self) -> None:
        self._rules: dict[str, list[GuardrailRule]] = {}  # tenant_id → rules
        self._violations: dict[str, list[GuardrailViolation]] = {}  # tenant_id → violations
        self._provider: Any = None

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def add_rule(self, rule: GuardrailRule) -> None:
        self._rules.setdefault(rule.tenant_id, []).append(rule)

    def get_rules(self, tenant_id: str, layer: GuardrailLayer | None = None) -> list[GuardrailRule]:
        rules = [r for r in self._rules.get(tenant_id, []) if r.enabled]
        if layer:
            rules = [r for r in rules if layer in r.layers]
        return rules

    def get_violations(self, tenant_id: str, limit: int = 100) -> list[GuardrailViolation]:
        violations = list(reversed(self._violations.get(tenant_id, [])))
        return violations[:limit]

    async def evaluate(
        self,
        content: str,
        layer: GuardrailLayer,
        tenant_id: str,
        goal_id: str | None = None,
        step_description: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate content against all active rules for the given layer."""
        rules = self.get_rules(tenant_id, layer)
        violations = []
        redacted_content = content
        blocked = False
        hitl_required = False

        for rule in rules:
            result = await self._evaluate_rule(rule, content)
            if result["triggered"]:
                violation = GuardrailViolation(
                    violation_id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    layer=layer.value,
                    action_taken=rule.action.value,
                    category=result.get("category", "unknown"),
                    content_preview=self._safe_preview(content),
                    severity=rule.severity,
                    goal_id=goal_id,
                    step_description=step_description,
                    created_at=datetime.datetime.now(datetime.UTC).isoformat(),
                )
                violations.append(violation)
                self._violations.setdefault(tenant_id, []).append(violation)

                if rule.action == GuardrailAction.BLOCK:
                    blocked = True
                elif rule.action == GuardrailAction.REQUIRE_HITL:
                    hitl_required = True
                elif rule.action == GuardrailAction.REDACT:
                    redacted_content = self._redact(redacted_content, result.get("matches", []))

        return {
            "blocked": blocked,
            "hitl_required": hitl_required,
            "violation_count": len(violations),
            "violations": [
                {
                    "violation_id": v.violation_id,
                    "rule_name": v.rule_name,
                    "category": v.category,
                    "action": v.action_taken,
                    "severity": v.severity,
                }
                for v in violations
            ],
            "redacted_content": redacted_content if not blocked else None,
        }

    async def simulate(self, content: str, layer: str, tenant_id: str) -> dict[str, Any]:
        """Simulate guardrail evaluation without recording violations."""
        try:
            layer_enum = GuardrailLayer(layer)
        except ValueError:
            return {"error": f"Invalid layer: {layer}"}

        rules = self.get_rules(tenant_id, layer_enum)
        would_trigger = []

        for rule in rules:
            result = await self._evaluate_rule(rule, content)
            if result["triggered"]:
                would_trigger.append(
                    {
                        "rule_name": rule.name,
                        "action": rule.action.value,
                        "category": result.get("category", "unknown"),
                        "severity": rule.severity,
                        "matches": result.get("matches", []),
                    }
                )

        return {
            "would_block": any(w["action"] == "block" for w in would_trigger),
            "would_require_hitl": any(w["action"] == "require_hitl" for w in would_trigger),
            "triggered_rules": would_trigger,
        }

    async def _evaluate_rule(self, rule: GuardrailRule, content: str) -> dict[str, Any]:
        """Evaluate a single rule against content."""
        if rule.rule_type == "pii_detection":
            return self._check_pii(content, rule.categories)
        elif rule.rule_type == "keyword_block":
            return self._check_keywords(content, rule.config)
        elif rule.rule_type == "prompt_injection":
            return self._check_injection(content)
        elif rule.rule_type == "regex_match":
            return self._check_regex(content, rule.config)
        elif rule.rule_type == "toxicity":
            return await self._check_toxicity_llm(content)
        return {"triggered": False, "matches": [], "category": ""}

    def _check_pii(self, content: str, categories: list) -> dict[str, Any]:
        matches = []
        for pattern, label in _PII_PATTERNS:
            for _m in re.finditer(pattern, content):
                matches.append({"match": "***REDACTED***", "type": label})
        # Also check secrets
        if ViolationCategory.SECRETS in categories or not categories:
            for pattern, label in _SECRET_PATTERNS:
                for _m in re.finditer(pattern, content):
                    matches.append({"match": "***SECRET***", "type": label})
        return {"triggered": len(matches) > 0, "matches": matches, "category": "pii"}

    def _check_keywords(self, content: str, config: dict) -> dict[str, Any]:
        keywords = config.get("keywords", [])
        matched = [kw for kw in keywords if kw.lower() in content.lower()]
        return {"triggered": len(matched) > 0, "matches": matched, "category": "keywords"}

    def _check_injection(self, content: str) -> dict[str, Any]:
        content_lower = content.lower()
        for pattern in _INJECTION_PATTERNS:
            if re.search(pattern, content_lower):
                return {"triggered": True, "matches": [pattern], "category": "prompt_injection"}
        return {"triggered": False, "matches": [], "category": "prompt_injection"}

    def _check_regex(self, content: str, config: dict) -> dict[str, Any]:
        pattern = config.get("pattern", "")
        if not pattern:
            return {"triggered": False, "matches": [], "category": "regex"}
        try:
            matches = re.findall(pattern, content)
            return {"triggered": len(matches) > 0, "matches": matches, "category": "regex"}
        except re.error:
            return {"triggered": False, "matches": [], "category": "regex"}

    async def _check_toxicity_llm(self, content: str) -> dict[str, Any]:
        if self._provider is None:
            return {"triggered": False, "matches": [], "category": "toxicity"}
        try:
            from app.providers.base import CompletionRequest, Message

            prompt = f"Is this text toxic? Answer only yes or no.\n\nText: {content[:200]}"
            resp = await self._provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=5,
                )
            )
            is_toxic = "yes" in resp.content.lower()
            return {"triggered": is_toxic, "matches": [], "category": "toxicity"}
        except Exception:
            return {"triggered": False, "matches": [], "category": "toxicity"}

    def _safe_preview(self, content: str, max_len: int = 100) -> str:
        """Return a safe preview with sensitive data redacted."""
        preview = content[:max_len]
        for pattern, _ in _PII_PATTERNS + _SECRET_PATTERNS:
            preview = re.sub(pattern, "***", preview)
        return preview + ("..." if len(content) > max_len else "")

    def _redact(self, content: str, matches: list) -> str:
        """Redact matched patterns from content."""
        redacted = content
        for pattern, _ in _PII_PATTERNS + _SECRET_PATTERNS:
            redacted = re.sub(pattern, "***REDACTED***", redacted)
        return redacted


# Module-level singleton
guardrails_engine = GuardrailsEngine()
