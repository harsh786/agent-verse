"""GuardrailEnforcer — applies GuardrailConfig to actual tool calls and outputs.

Wiring layer between GuardrailProfileSelector and guardrails_v2/engine.
Translates a GuardrailConfig (bundle selection) into concrete scanning decisions.

P0-1 fix: the two public methods are ``async`` because the engine's ``evaluate``
is ``async`` and requires ``tenant_id``. Blocking is derived from the engine's
real return dict (``violations[].category``), honours the selected config, and
**fails closed** on a high-risk goal when the engine errors — a broken safety
check must never read as "allowed".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile

logger = logging.getLogger(__name__)

_INJECTION_PATTERNS = [
    re.compile(
        r"(?i)(ignore|forget|disregard)\s+(previous|prior|above|all)\s+(instructions?|prompts?|rules?|context)"
    ),
    re.compile(
        r"(?i)(you are now|act as|pretend to be|roleplay as)\s+.{0,50}(without|ignore|bypass)"
    ),
    re.compile(r"(?i)(system\s*prompt|hidden\s*instruction|jailbreak)"),
    re.compile(r"(?i)(DROP\s+TABLE|DELETE\s+FROM|TRUNCATE\s+TABLE|ALTER\s+TABLE)"),
]

_PII_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
]

# Violation categories emitted by guardrails_v2.engine._evaluate_rule.
# These MUST match the strings the engine actually produces:
#   _check_injection -> "prompt_injection"; _check_pii -> "pii".
_INJECTION_CATEGORIES = frozenset({"prompt_injection", "tool_injection", "jailbreak"})
_PII_CATEGORIES = frozenset({"pii", "phi", "pci", "secrets"})


@dataclass
class EnforcementResult:
    checked: bool
    blocked: bool = False
    injection_detected: bool = False
    pii_detected: bool = False
    toxicity_detected: bool = False
    reason: str = ""
    redacted_content: str = ""


# Check if guardrails_v2 is available
try:
    from app.guardrails_v2.engine import guardrails_engine as _ge

    _GUARDRAILS_V2_AVAILABLE = _ge is not None
except Exception:
    _GUARDRAILS_V2_AVAILABLE = False


def _profile_is_high_risk(profile: GoalRuntimeProfile) -> bool:
    """A high/critical-risk goal must fail closed when the safety check errors."""
    try:
        risk = getattr(getattr(profile, "properties", None), "risk", None)
        risk_val = str(getattr(risk, "value", risk) or "").lower()
        return risk_val in {"high", "critical"}
    except Exception:
        return True  # unknown risk -> treat as high, fail closed


class GuardrailEnforcer:
    """Applies guardrail scanning based on the selected GuardrailConfig."""

    def _resolve_config(self, profile: GoalRuntimeProfile) -> Any:
        from app.security_runtime.guardrail_profile import GuardrailProfileSelector
        from app.tenancy.context import PlanTier, TenantContext

        selector = GuardrailProfileSelector()
        try:
            plan_str = getattr(profile, "tenant_plan", None) or "professional"
            plan = (
                PlanTier(plan_str)
                if plan_str in [p.value for p in PlanTier]
                else PlanTier.PROFESSIONAL
            )
        except Exception:
            plan = PlanTier.PROFESSIONAL
        tenant_ctx = TenantContext(tenant_id=profile.tenant_id, plan=plan, api_key_id="k1")
        return selector.select(profile, tenant_ctx=tenant_ctx)

    async def check_tool_args(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        profile: GoalRuntimeProfile,
    ) -> EnforcementResult:
        """Check tool arguments for injection, PII, and policy violations."""
        config = self._resolve_config(profile)
        high_risk = _profile_is_high_risk(profile)

        if _GUARDRAILS_V2_AVAILABLE:
            return await self._check_with_engine(
                str(tool_args), config, "tool_args", profile.tenant_id, high_risk=high_risk
            )

        return self._fallback_check(str(tool_args), config, fail_closed=False)

    async def check_final_output(
        self,
        output: str,
        profile: GoalRuntimeProfile,
    ) -> EnforcementResult:
        """Check final output for PII, toxicity, and policy violations."""
        config = self._resolve_config(profile)
        high_risk = _profile_is_high_risk(profile)

        if _GUARDRAILS_V2_AVAILABLE:
            return await self._check_with_engine(
                output, config, "final_output", profile.tenant_id, high_risk=high_risk
            )

        return self._fallback_check(output, config, fail_closed=False)

    async def _check_with_engine(
        self,
        content: str,
        config: Any,
        layer: str,
        tenant_id: str,
        *,
        high_risk: bool = False,
    ) -> EnforcementResult:
        from app.guardrails_v2.engine import guardrails_engine
        from app.guardrails_v2.models import GuardrailLayer

        layer_map = {
            "tool_args": GuardrailLayer.TOOL_ARGS,
            "final_output": GuardrailLayer.FINAL_OUTPUT,
        }
        try:
            result = await guardrails_engine.evaluate(
                content=content,
                layer=layer_map.get(layer, GuardrailLayer.TOOL_ARGS),
                tenant_id=tenant_id,
            )
        except (TypeError, AttributeError):
            # A signature/contract error is a BUG, never "not blocked".
            logger.exception("guardrail engine contract error; failing closed")
            return self._fallback_check(content, config, fail_closed=True)
        except Exception:
            logger.exception("guardrail engine unavailable")
            return self._fallback_check(content, config, fail_closed=high_risk)

        categories = {
            str(v.get("category", "")).lower() for v in result.get("violations", [])
        }
        # Detection is a union of what configured rules matched AND cheap local
        # regex, so an unconfigured tenant still gets detection (blocking stays
        # rule-/config-driven below). Local scans honour the selected config.
        scan_inj = getattr(config, "scan_prompt_injection", True)
        scan_pii = getattr(config, "scan_output_pii", True)
        injection = bool(categories & _INJECTION_CATEGORIES) or (
            scan_inj and self._check_injection(content)
        )
        pii = bool(categories & _PII_CATEGORIES) or (scan_pii and self._check_pii(content))

        blocked = bool(result.get("blocked", False))
        # Honour the selected config even when no rule carried action=BLOCK.
        if injection and getattr(config, "block_on_injection", False):
            blocked = True
        if pii and getattr(config, "block_on_pii", False):
            blocked = True

        return EnforcementResult(
            checked=True,
            blocked=blocked,
            injection_detected=injection,
            pii_detected=pii,
            reason="; ".join(sorted(c for c in categories if c)),
            redacted_content=result.get("redacted_content") or "",
        )

    def _fallback_check(
        self, content: str, config: Any, *, fail_closed: bool = False
    ) -> EnforcementResult:
        injection = (
            self._check_injection(content)
            if getattr(config, "scan_prompt_injection", True)
            else False
        )
        pii = (
            self._check_pii(content) if getattr(config, "scan_output_pii", True) else False
        )
        blocked = (injection and getattr(config, "block_on_injection", False)) or (
            pii and getattr(config, "block_on_pii", False)
        )
        if fail_closed and (injection or pii):
            blocked = True
        reason_parts = []
        if injection:
            reason_parts.append("injection_detected")
        if pii:
            reason_parts.append("pii_detected")
        return EnforcementResult(
            checked=True,
            blocked=blocked,
            injection_detected=injection,
            pii_detected=pii,
            reason="; ".join(reason_parts),
        )

    def _check_injection(self, content: str) -> bool:
        return any(p.search(content) for p in _INJECTION_PATTERNS)

    def _check_pii(self, content: str) -> bool:
        return any(p.search(content) for p in _PII_PATTERNS)
