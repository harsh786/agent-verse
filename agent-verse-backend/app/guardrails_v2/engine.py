"""Guardrails 2.0 evaluation engine."""

from __future__ import annotations

import datetime
import logging
import re
import time
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

# Tenant regex rules: per-evaluation time budget and input cap (ReDoS guard).
_REGEX_TIMEOUT_S = 0.25
_REGEX_MAX_INPUT = 200_000
_REGEX_FALLBACK_MAX_INPUT = 10_000

# Lazy per-tenant rule loading (replaces the cross-tenant startup warm scan).
# A tenant's persisted rules are re-read from Postgres at most this often, so a
# rule written through another replica becomes enforced here within the window.
_DEFAULT_RULE_REFRESH_S = 60.0
# After a FIRST load fails, further evaluations for that tenant fail fast for this
# long instead of each waiting on a struggling database.
_LOAD_RETRY_BACKOFF_S = 5.0
# Per-tenant cap of the in-process violation cache (Postgres is the record).
_VIOLATION_CACHE_PER_TENANT = 1000


class GuardrailRulesUnavailableError(RuntimeError):
    """A tenant's persisted guardrail rules could not be loaded.

    Raised instead of silently evaluating against only the in-memory defaults:
    that would quietly drop the tenant's own BLOCK rules. Callers already treat
    an erroring guardrail check as "unknown" and fail closed on high-risk work
    (SAFE-4). Deliberately not a ``TypeError``/``AttributeError``, which callers
    treat as a contract bug.
    """

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
    # International numbers with a country code (e.g. "+91 98450 12345", "+44 20 7946
    # 0958"); the North-American pattern above misses every non-3-3-4 grouping.
    (r"(?<![\w+])\+\d{1,3}(?:[\s.-]?\d){6,14}\b", "International phone"),
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

    def __init__(self, *, rule_refresh_s: float = _DEFAULT_RULE_REFRESH_S) -> None:
        self._rules: dict[str, list[GuardrailRule]] = {}  # tenant_id → rules
        self._violations: dict[str, list[GuardrailViolation]] = {}  # tenant_id → violations
        self._provider: Any = None
        # P1-4 persistence: an optional repository durably stores rules so they
        # survive a restart. Bound in the app lifespan (DB-backed) and left None
        # in the in-memory create_app path / unit tests.
        self._repo: Any = None
        self._auto_persist: bool = False
        # Rules written by an operator/API call → upserted on flush.
        self._unsaved: list[GuardrailRule] = []
        # Seeded baseline/bundle defaults → inserted only if absent on flush, so a
        # re-seed on a fresh process never overwrites a persisted (edited) row.
        self._unsaved_seeds: list[GuardrailRule] = []
        self._save_tasks: set[Any] = set()
        # Lazy per-tenant load bookkeeping (monotonic timestamps).
        self._rule_refresh_s = rule_refresh_s
        self._tenant_loaded_at: dict[str, float] = {}
        self._tenant_load_failed_at: dict[str, float] = {}

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def bind_repository(self, repo: Any, *, auto_persist: bool = False) -> None:
        """Attach (or with ``None`` detach) a persistence repository.

        When ``auto_persist`` is set, rules added at runtime are flushed to the
        repo on a best-effort background task. Tests bind with
        ``auto_persist=False`` and call ``flush`` explicitly for deterministic
        behaviour. Nothing is read here: each tenant's persisted rules are loaded
        on that tenant's first evaluation (:meth:`ensure_tenant_loaded`).
        """
        self._repo = repo
        self._auto_persist = auto_persist
        self._tenant_loaded_at.clear()
        self._tenant_load_failed_at.clear()

    @property
    def has_repository(self) -> bool:
        """True when a persistence repository is bound (tenant rules are loadable)."""
        return self._repo is not None

    def add_rule(self, rule: GuardrailRule) -> None:
        self._add(rule, seed=False)

    def _put_in_memory(self, rule: GuardrailRule) -> None:
        rules = self._rules.setdefault(rule.tenant_id, [])
        for i, existing in enumerate(rules):
            if existing.rule_id == rule.rule_id:
                rules[i] = rule
                return
        rules.append(rule)

    async def add_rule_durable(self, rule: GuardrailRule) -> bool:
        """Persist *rule*, THEN make it live here; returns whether it is durable.

        ``POST /rules`` used to answer "created" while persistence was a
        best-effort background flush (GRD-02). With a repository bound a failed
        write raises and nothing changes; without one (in-memory create_app /
        unit tests) the rule lives in this process only and ``False`` says so.
        """
        if self._repo is None:
            self._put_in_memory(rule)
            return False
        await self._repo.upsert(rule)
        self._put_in_memory(rule)
        return True

    def all_rules(self, tenant_id: str) -> list[GuardrailRule]:
        """Every in-memory rule of the tenant, disabled ones included."""
        return list(self._rules.get(tenant_id, []))

    def find_rule(self, tenant_id: str, rule_id: str) -> GuardrailRule | None:
        return next((r for r in self._rules.get(tenant_id, []) if r.rule_id == rule_id), None)

    async def update_rule_durable(
        self, tenant_id: str, rule_id: str, **changes: Any
    ) -> GuardrailRule | None:
        """Apply *changes* to a rule (incl. ``enabled``), persist, bump its version.

        Returns ``None`` when the tenant has no such rule. Raises when the write
        fails (nothing changes then).
        """
        import dataclasses

        await self.ensure_tenant_loaded(tenant_id)
        current = self.find_rule(tenant_id, rule_id)
        if current is None:
            return None
        updated = dataclasses.replace(current, **changes, version=current.version + 1)
        if self._repo is not None:
            await self._repo.upsert(updated)
        self._put_in_memory(updated)
        return updated

    async def delete_rule_durable(self, tenant_id: str, rule_id: str) -> bool:
        """Delete a tenant's rule (durably first). Returns False if it did not exist."""
        await self.ensure_tenant_loaded(tenant_id)
        if self.find_rule(tenant_id, rule_id) is None:
            return False
        if self._repo is not None:
            await self._repo.delete(tenant_id, rule_id)
        self._rules[tenant_id] = [r for r in self._rules.get(tenant_id, []) if r.rule_id != rule_id]
        self._unsaved = [r for r in self._unsaved if r.rule_id != rule_id]
        return True

    def _add(self, rule: GuardrailRule, *, seed: bool) -> None:
        self._rules.setdefault(rule.tenant_id, []).append(rule)
        if self._repo is not None:
            (self._unsaved_seeds if seed else self._unsaved).append(rule)
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
        if self._repo is None or not (self._unsaved or self._unsaved_seeds):
            return 0
        seeds, self._unsaved_seeds = self._unsaved_seeds, []
        pending, self._unsaved = self._unsaved, []
        insert_if_absent = getattr(self._repo, "insert_if_absent", None)
        saved = 0
        for rule in seeds:
            try:
                if insert_if_absent is not None:
                    await insert_if_absent(rule)
                else:  # minimal repos (tests) without the conditional insert
                    await self._repo.upsert(rule)
                saved += 1
            except Exception:
                _log.exception("guardrail rule persist failed rule_id=%s", rule.rule_id)
                self._unsaved_seeds.append(rule)  # retry on next flush
        for rule in pending:
            try:
                await self._repo.upsert(rule)
                saved += 1
            except Exception:
                _log.exception("guardrail rule persist failed rule_id=%s", rule.rule_id)
                self._unsaved.append(rule)  # retry on next flush
        return saved

    async def load_from_repo(self, tenant_id: str) -> int:
        """Merge ONE tenant's persisted rules into memory. Returns the count added.

        Postgres is the source of truth: a persisted row replaces an in-memory
        rule with the same id (e.g. a baseline default seeded moments earlier by
        ``ensure_default_rules``), unless that in-memory rule is an operator write
        still waiting to be flushed — that one is newer than the row.
        """
        if self._repo is None:
            return 0
        rules = await self._repo.load(tenant_id)
        pending_writes = {r.rule_id for r in self._unsaved if r.tenant_id == tenant_id}
        existing = self._rules.setdefault(tenant_id, [])
        position = {r.rule_id: i for i, r in enumerate(existing)}
        persisted: set[str] = set()
        loaded = 0
        for rule in rules:
            if rule.tenant_id != tenant_id:
                # Defense in depth: RLS + the repo's predicate already exclude
                # these; never let another tenant's rule into this tenant's list.
                _log.error(
                    "guardrail rule tenant mismatch rule_id=%s expected=%s got=%s",
                    rule.rule_id,
                    tenant_id,
                    rule.tenant_id,
                )
                continue
            persisted.add(rule.rule_id)
            idx = position.get(rule.rule_id)
            if idx is None:
                position[rule.rule_id] = len(existing)
                existing.append(rule)
                loaded += 1
            elif rule.rule_id not in pending_writes:
                existing[idx] = rule
        # A seed whose row already exists needs no insert.
        self._unsaved_seeds = [r for r in self._unsaved_seeds if r.rule_id not in persisted]
        # A rule deleted on another replica disappears here too (Postgres is the
        # source of truth) — except writes/seeds this process has not flushed yet.
        not_yet_saved = pending_writes | {
            r.rule_id for r in self._unsaved_seeds if r.tenant_id == tenant_id
        }
        self._rules[tenant_id] = [
            r for r in existing if r.rule_id in persisted or r.rule_id in not_yet_saved
        ]
        return loaded

    async def ensure_tenant_loaded(self, tenant_id: str) -> None:
        """Make sure ``tenant_id``'s persisted rules are in memory (and fresh).

        This is the lazy, per-tenant replacement for the old cross-tenant startup
        scan: the first evaluation for a tenant on this process reads that
        tenant's rules under its own RLS context; later evaluations re-read them
        at most every ``rule_refresh_s`` so rules written on another replica are
        picked up. No-op without a bound repository.

        Raises :class:`GuardrailRulesUnavailableError` when the tenant has never
        been loaded here and the load fails. Once loaded, a failed refresh keeps
        serving the last-known rules (logged) rather than failing the request.
        """
        if self._repo is None or not tenant_id:
            return
        now = time.monotonic()
        loaded_at = self._tenant_loaded_at.get(tenant_id)
        if loaded_at is not None and now - loaded_at < self._rule_refresh_s:
            return
        if loaded_at is None:
            failed_at = self._tenant_load_failed_at.get(tenant_id)
            if failed_at is not None and now - failed_at < _LOAD_RETRY_BACKOFF_S:
                raise GuardrailRulesUnavailableError(
                    f"guardrail rules for tenant {tenant_id!r} are unavailable"
                )
        try:
            await self.load_from_repo(tenant_id)
        except Exception as exc:
            if loaded_at is None:
                self._tenant_load_failed_at[tenant_id] = time.monotonic()
                _log.warning("guardrail rules load failed tenant=%s: %s", tenant_id, exc)
                raise GuardrailRulesUnavailableError(
                    f"guardrail rules for tenant {tenant_id!r} could not be loaded"
                ) from exc
            _log.warning(
                "guardrail rules refresh failed tenant=%s; serving last-known rules: %s",
                tenant_id,
                exc,
            )
            self._tenant_loaded_at[tenant_id] = time.monotonic()  # retry next window
            return
        self._tenant_loaded_at[tenant_id] = time.monotonic()
        self._tenant_load_failed_at.pop(tenant_id, None)

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
            self._add(rule, seed=True)
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
                self._add(rule, seed=True)
                existing_ids.add(rule.rule_id)
                added += 1
        return added

    def get_rules(self, tenant_id: str, layer: GuardrailLayer | None = None) -> list[GuardrailRule]:
        """In-memory view only. Request paths should use :meth:`aget_rules`, which
        first loads the tenant's persisted rules on a fresh process."""
        rules = [r for r in self._rules.get(tenant_id, []) if r.enabled]
        if layer:
            rules = [r for r in rules if layer in r.layers]
        return rules

    async def aget_rules(
        self, tenant_id: str, layer: GuardrailLayer | None = None
    ) -> list[GuardrailRule]:
        """Like :meth:`get_rules`, after loading the tenant's persisted rules."""
        await self.ensure_tenant_loaded(tenant_id)
        return self.get_rules(tenant_id, layer)

    def get_violations(self, tenant_id: str, limit: int = 100) -> list[GuardrailViolation]:
        """This process's recent violations only (bounded cache)."""
        violations = list(reversed(self._violations.get(tenant_id, [])))
        return violations[:limit]

    async def aget_violations(
        self, tenant_id: str, limit: int = 100, severity: str | None = None
    ) -> list[GuardrailViolation]:
        """The tenant's violations from Postgres (every replica's) when a repository
        is bound; raises if it cannot be read — never a one-replica partial view."""
        lister = getattr(self._repo, "list_violations", None)
        if lister is None:
            found = self.get_violations(tenant_id, limit if not severity else 10_000)
            if severity:
                found = [v for v in found if v.severity == severity][:limit]
            return found
        result: list[GuardrailViolation] = await lister(
            tenant_id, limit=limit, severity=severity
        )
        return result

    def _remember_violation(self, tenant_id: str, violation: GuardrailViolation) -> None:
        cache = self._violations.setdefault(tenant_id, [])
        cache.append(violation)
        if len(cache) > _VIOLATION_CACHE_PER_TENANT:
            del cache[: len(cache) - _VIOLATION_CACHE_PER_TENANT]

    async def _persist_violations(
        self, tenant_id: str, violations: list[GuardrailViolation]
    ) -> None:
        recorder = getattr(self._repo, "record_violations", None)
        if recorder is None or not violations:
            return
        try:
            await recorder(tenant_id, violations)
        except Exception as exc:
            # The evaluation verdict stands; the lost record is loud, not silent.
            _log.error(
                "guardrail_violations_persist_failed tenant=%s count=%s: %s",
                tenant_id,
                len(violations),
                exc,
            )

    async def evaluate(
        self,
        content: str,
        layer: GuardrailLayer,
        tenant_id: str,
        goal_id: str | None = None,
        step_description: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate content against all active rules for the given layer."""
        rules = await self.aget_rules(tenant_id, layer)
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
                self._remember_violation(tenant_id, violation)

                if rule.action == GuardrailAction.BLOCK:
                    blocked = True
                elif rule.action == GuardrailAction.REQUIRE_HITL:
                    hitl_required = True
                elif rule.action == GuardrailAction.REDACT:
                    redacted_content = self._redact(redacted_content, result.get("matches", []))

        await self._persist_violations(tenant_id, violations)
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

        rules = await self.aget_rules(tenant_id, layer_enum)
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
            return await self._check_toxicity_llm(content, tenant_id=rule.tenant_id)
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
        """Run a TENANT-supplied pattern with a time budget (ReDoS-safe).

        ``re.findall`` ran the pattern unbounded: one catastrophic-backtracking
        rule (``(a+)+$``) pinned an API worker's CPU on every evaluated message.
        The third-party ``regex`` engine (already locked in uv.lock) supports a
        timeout; a timed-out or invalid pattern TRIGGERS the rule (fail closed)
        instead of silently passing the content. Without ``regex`` installed the
        input is capped so the worst case stays bounded.
        """
        pattern = config.get("pattern", "")
        if not pattern:
            return {"triggered": False, "matches": [], "category": "regex"}
        try:
            import regex as _regex_engine
        except ImportError:  # pragma: no cover - regex is a locked dependency
            try:
                found = re.findall(pattern, content[:_REGEX_FALLBACK_MAX_INPUT])
            except re.error:
                return {"triggered": True, "matches": ["invalid_pattern"], "category": "regex"}
            return {"triggered": bool(found), "matches": found[:50], "category": "regex"}
        try:
            found = _regex_engine.findall(
                pattern, content[:_REGEX_MAX_INPUT], timeout=_REGEX_TIMEOUT_S
            )
        except TimeoutError:
            _log.warning("guardrail_regex_timeout pattern=%s", str(pattern)[:80])
            return {"triggered": True, "matches": ["regex_timeout"], "category": "regex"}
        except _regex_engine.error:
            return {"triggered": True, "matches": ["invalid_pattern"], "category": "regex"}
        return {"triggered": bool(found), "matches": found[:50], "category": "regex"}

    async def _check_toxicity_llm(
        self, content: str, *, tenant_id: str | None = None
    ) -> dict[str, Any]:
        """LLM toxicity judge, with the pattern classifier as the floor.

        Without a provider, or on a provider error, the rule used to answer
        "not toxic" for everything (it could never trigger). The built-in
        pattern classifier now decides in those cases.
        """
        if self._provider is None:
            return self._check_toxicity_patterns(content)
        try:
            from app.providers.base import CompletionRequest, Message
            from app.providers.guarded_completion import complete_decision

            prompt = f"Is this text toxic? Answer only yes or no.\n\nText: {content[:200]}"
            # Charged and circuit-broken; any failure (open circuit, timeout,
            # budget refusal) falls back to the pattern classifier below.
            resp = await complete_decision(
                self._provider,
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=5,
                ),
                role="guardrail_toxicity",
                tenant_id=tenant_id,
            )
            is_toxic = "yes" in resp.content.lower()
            if not is_toxic:
                # The LLM saw only 200 chars; the patterns still apply to all.
                return self._check_toxicity_patterns(content)
            return {"triggered": True, "matches": [], "category": "toxicity"}
        except Exception as exc:
            _log.warning("guardrail_toxicity_llm_failed_pattern_fallback: %s", str(exc)[:120])
            return self._check_toxicity_patterns(content)

    def _check_toxicity_patterns(self, content: str) -> dict[str, Any]:
        from app.guardrails_v2.toxicity import ToxicityClassifier

        result = ToxicityClassifier(use_llm_for_ambiguous=False).classify_sync(content)
        return {
            "triggered": result.is_toxic,
            "matches": list(result.categories),
            "category": "toxicity",
        }

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
