"""Guardrails 2.0 evaluation engine."""

from __future__ import annotations

import datetime
import logging
import re
import uuid
from typing import Any

from app.guardrails_v2.models import (
    COMPLIANCE_BUNDLES,
    ComplianceBundle,
    GuardrailAction,
    GuardrailLayer,
    GuardrailRule,
    GuardrailViolation,
    ViolationCategory,
)

_log = logging.getLogger(__name__)

# Combined secret-format regex used by the baseline ``regex_match`` rule so that
# an unconfigured tenant still blocks obvious credential leakage in tool args /
# outputs. Kept in sync with ``_SECRET_PATTERNS`` above (OpenAI/Anthropic keys,
# GitHub tokens, Google API keys, generic AWS access-key ids).
_SECRET_REGEX = (
    r"(?:sk-ant-[a-zA-Z0-9]{20,}|sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}"
    r"|AIza[0-9A-Za-z_\-]{35}|AKIA[0-9A-Z]{16})"
)

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

# Plain-text injection phrases used when scanning *de-obfuscated* variants of the
# content (base64 / rot13 / leetspeak / homoglyph). Kept phrase-based (not regex)
# because the decoded text is normalised before matching.
_INJECTION_PHRASES = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous",
    "disregard all previous",
    "forget everything",
    "forget all",
    "you are now",
    "pretend you are",
    "pretend to be",
    "act as",
    "reveal the system prompt",
    "system prompt",
    "jailbreak",
    "dan mode",
)

# Common Unicode confusables (Cyrillic / Greek look-alikes and full-width forms)
# folded to their ASCII equivalent so homoglyph-obfuscated injections are caught.
# NFKC alone does not map Cyrillic → Latin, so we carry an explicit table.
_HOMOGLYPH_MAP = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
        "і": "i", "ѕ": "s", "ԁ": "d", "ո": "n", "к": "k", "м": "m", "т": "t",
        "н": "h", "в": "b", "ѐ": "e", "ё": "e",
        "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
        "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
        "ο": "o", "ι": "i", "ν": "v", "α": "a", "ρ": "p", "τ": "t", "υ": "u",
    }
)

# Leetspeak substitutions (digits / symbols → letters).
_LEET_MAP = str.maketrans("4310!7$", "aeiolts")


def _baseline_rules(tenant_id: str) -> list[GuardrailRule]:
    """Baseline BLOCK rules every tenant gets by default (defect 5).

    Rule ids are deterministic (``gr-default:<tenant>:<suffix>``) so seeding is
    idempotent. Rule types match ``GuardrailsEngine._evaluate_rule`` dispatch.
    """
    return [
        # PII (and secrets) leaving the system in a final answer or a memory write.
        GuardrailRule(
            rule_id=f"gr-default:{tenant_id}:pii-final-output",
            tenant_id=tenant_id,
            name="Baseline: block PII/secrets in outputs",
            rule_type="pii_detection",
            layers=[GuardrailLayer.FINAL_OUTPUT, GuardrailLayer.MEMORY_WRITE],
            action=GuardrailAction.BLOCK,
            categories=[ViolationCategory.PII, ViolationCategory.SECRETS],
            severity="high",
        ),
        # Prompt injection in tool arguments and step descriptions.
        GuardrailRule(
            rule_id=f"gr-default:{tenant_id}:injection-tool-args",
            tenant_id=tenant_id,
            name="Baseline: block prompt injection",
            rule_type="prompt_injection",
            layers=[GuardrailLayer.TOOL_ARGS, GuardrailLayer.STEP, GuardrailLayer.GOAL],
            action=GuardrailAction.BLOCK,
            categories=[ViolationCategory.PROMPT_INJECTION],
            severity="high",
        ),
        # Credential/secret leakage via a dedicated regex, covering tool traffic.
        GuardrailRule(
            rule_id=f"gr-default:{tenant_id}:secret-regex",
            tenant_id=tenant_id,
            name="Baseline: block secret credentials",
            rule_type="regex_match",
            layers=[
                GuardrailLayer.FINAL_OUTPUT,
                GuardrailLayer.TOOL_ARGS,
                GuardrailLayer.TOOL_OUTPUT,
            ],
            action=GuardrailAction.BLOCK,
            categories=[ViolationCategory.SECRETS],
            severity="critical",
            config={"pattern": _SECRET_REGEX},
        ),
    ]


def _rule_from_spec(tenant_id: str, rule_id: str, spec: dict[str, Any]) -> GuardrailRule:
    """Build a GuardrailRule from a COMPLIANCE_BUNDLES spec dict (string enums)."""
    layers = [GuardrailLayer(x) for x in spec.get("layers", ["step"])]
    action = GuardrailAction(spec.get("action", "block"))
    categories = [ViolationCategory(c) for c in spec.get("categories", [])]
    return GuardrailRule(
        rule_id=rule_id,
        tenant_id=tenant_id,
        name=spec["name"],
        rule_type=spec["rule_type"],
        layers=layers,
        action=action,
        categories=categories,
        severity=spec.get("severity", "high"),
        config=dict(spec.get("config", {})),
    )


def _injection_deobfuscation_hit(content: str) -> str | None:
    """Return an obfuscation label if a de-obfuscated variant reveals an injection.

    Covers base64, rot13, leetspeak and Unicode-homoglyph obfuscation of the
    phrases in ``_INJECTION_PHRASES``. Deterministic and LLM-free so it is safe
    on the hot path and in the red-team corpus. Returns ``None`` when nothing
    matches (clean content must never be flagged here).
    """
    import base64
    import codecs
    import unicodedata

    def _has_phrase(text: str) -> bool:
        low = text.lower()
        return any(phrase in low for phrase in _INJECTION_PHRASES)

    # Homoglyph: NFKC + explicit confusable fold.
    folded = unicodedata.normalize("NFKC", content).translate(_HOMOGLYPH_MAP)
    if folded.lower() != content.lower() and _has_phrase(folded):
        return "homoglyph"

    # Leetspeak.
    leet = content.translate(_LEET_MAP)
    if leet.lower() != content.lower() and _has_phrase(leet):
        return "leetspeak"

    # ROT13.
    try:
        rot = codecs.encode(content, "rot_13")
        if _has_phrase(rot):
            return "rot13"
    except Exception:  # pragma: no cover - rot13 never raises on str
        pass

    # Base64: decode plausible tokens and re-scan.
    for token in re.findall(r"[A-Za-z0-9+/=]{16,}", content):
        try:
            decoded = base64.b64decode(token + "===", validate=False).decode(
                "utf-8", errors="ignore"
            )
        except Exception:
            continue
        if _has_phrase(decoded):
            return "base64"

    return None


class GuardrailsEngine:
    """Evaluates content against guardrail rules."""

    def __init__(self) -> None:
        self._rules: dict[str, list[GuardrailRule]] = {}  # tenant_id → rules
        self._violations: dict[str, list[GuardrailViolation]] = {}  # tenant_id → violations
        self._provider: Any = None
        # P1-4 persistence: an optional repository durably stores rules so they
        # survive a restart. Bound in the app lifespan (DB-backed) and left None
        # in the in-memory create_app path / unit tests.
        self._repo: Any = None
        self._auto_persist: bool = False
        self._unsaved: list[GuardrailRule] = []
        self._save_tasks: set[Any] = set()

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def bind_repository(self, repo: Any, *, auto_persist: bool = False) -> None:
        """Attach a persistence repository. When ``auto_persist`` is set, rules
        added at runtime are flushed to the repo on a best-effort background task.
        Tests bind with ``auto_persist=False`` and call ``flush`` explicitly for
        deterministic behaviour."""
        self._repo = repo
        self._auto_persist = auto_persist

    def add_rule(self, rule: GuardrailRule) -> None:
        self._rules.setdefault(rule.tenant_id, []).append(rule)
        if self._repo is not None:
            self._unsaved.append(rule)
            if self._auto_persist:
                self._schedule_flush()

    def _schedule_flush(self) -> None:
        """Best-effort background persistence (only when an event loop runs)."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.flush())
        self._save_tasks.add(task)
        task.add_done_callback(self._save_tasks.discard)

    async def flush(self) -> int:
        """Persist any rules added since the last flush. Returns the count saved."""
        if self._repo is None or not self._unsaved:
            return 0
        pending = self._unsaved
        self._unsaved = []
        saved = 0
        for rule in pending:
            try:
                await self._repo.upsert(rule)
                saved += 1
            except Exception:
                _log.exception("guardrail rule persist failed rule_id=%s", rule.rule_id)
                self._unsaved.append(rule)  # retry on next flush
        return saved

    async def load_from_repo(self, tenant_id: str | None = None) -> int:
        """Rehydrate rules from the bound repository into memory. Returns count."""
        if self._repo is None:
            return 0
        rules = await self._repo.load(tenant_id)
        loaded = 0
        for rule in rules:
            existing = self._rules.setdefault(rule.tenant_id, [])
            if any(r.rule_id == rule.rule_id for r in existing):
                continue
            existing.append(rule)
            loaded += 1
        return loaded

    def ensure_default_rules(
        self, tenant_id: str, bundles: list[str] | None = None
    ) -> int:
        """Seed baseline BLOCK rules (+ compliance-bundle rules) for a tenant.

        Fixes defect 5: without this every unconfigured tenant had ``get_rules()
        == []`` and therefore ``blocked=False`` for all inputs. Idempotent — rules
        carry deterministic ids, so re-seeding adds nothing. Returns the number of
        rules newly added.
        """
        existing_ids = {r.rule_id for r in self._rules.get(tenant_id, [])}
        added = 0
        for rule in _baseline_rules(tenant_id):
            if rule.rule_id in existing_ids:
                continue
            self.add_rule(rule)
            existing_ids.add(rule.rule_id)
            added += 1
        for tag in bundles or []:
            try:
                bundle = ComplianceBundle(str(tag).lower())
            except ValueError:
                continue
            for idx, spec in enumerate(COMPLIANCE_BUNDLES.get(bundle, [])):
                rule_id = f"gr-bundle:{tenant_id}:{bundle.value}:{idx}"
                rule = _rule_from_spec(tenant_id, rule_id, spec)
                if rule.rule_id in existing_ids:
                    continue
                self.add_rule(rule)
                existing_ids.add(rule.rule_id)
                added += 1
        return added

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
        # Encoded / obfuscated injections (base64 / rot13 / leetspeak / homoglyph).
        obfuscation = _injection_deobfuscation_hit(content)
        if obfuscation is not None:
            return {
                "triggered": True,
                "matches": [f"{obfuscation}-encoded injection"],
                "category": "prompt_injection",
            }
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
