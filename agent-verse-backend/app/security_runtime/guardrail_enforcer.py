"""GuardrailEnforcer — applies GuardrailConfig to actual tool calls and outputs.

Wiring layer between GuardrailProfileSelector and guardrails_v2/engine.
Translates a GuardrailConfig (bundle selection) into concrete scanning decisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile

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
except (ImportError, Exception):
    _GUARDRAILS_V2_AVAILABLE = False


class GuardrailEnforcer:
    """Applies guardrail scanning based on the selected GuardrailConfig."""

    def check_tool_args(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        profile: GoalRuntimeProfile,
    ) -> EnforcementResult:
        """Check tool arguments for injection, PII, and policy violations."""
        from app.security_runtime.guardrail_profile import GuardrailProfileSelector
        from app.tenancy.context import PlanTier, TenantContext

        selector = GuardrailProfileSelector()
        # C5 fix: use actual tenant plan from profile, not hardcoded PROFESSIONAL
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
        config = selector.select(profile, tenant_ctx=tenant_ctx)

        if _GUARDRAILS_V2_AVAILABLE:
            return self._check_with_engine(str(tool_args), config, "tool_args")

        content = str(tool_args)
        injection = self._check_injection(content) if config.scan_prompt_injection else False
        blocked = injection and config.block_on_injection

        return EnforcementResult(
            checked=True,
            blocked=blocked,
            injection_detected=injection,
            reason="injection_detected" if injection else "",
        )

    def check_final_output(
        self,
        output: str,
        profile: GoalRuntimeProfile,
    ) -> EnforcementResult:
        """Check final output for PII, toxicity, and policy violations."""
        from app.security_runtime.guardrail_profile import GuardrailProfileSelector
        from app.tenancy.context import PlanTier, TenantContext

        selector = GuardrailProfileSelector()
        # C5 fix: use actual tenant plan from profile, not hardcoded PROFESSIONAL
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
        config = selector.select(profile, tenant_ctx=tenant_ctx)

        if _GUARDRAILS_V2_AVAILABLE:
            return self._check_with_engine(output, config, "final_output")

        pii = self._check_pii(output) if config.scan_output_pii else False
        blocked = pii and config.block_on_pii

        return EnforcementResult(
            checked=True,
            blocked=blocked,
            pii_detected=pii,
            reason="pii_detected" if pii else "",
        )

    def _check_with_engine(self, content: str, config: Any, layer: str) -> EnforcementResult:
        try:
            from app.guardrails_v2.engine import guardrails_engine
            from app.guardrails_v2.models import GuardrailLayer

            layer_map = {
                "tool_args": GuardrailLayer.TOOL_ARGS,
                "final_output": GuardrailLayer.FINAL_OUTPUT,
            }
            result = guardrails_engine.evaluate(
                content=content,
                layer=layer_map.get(layer, GuardrailLayer.TOOL_ARGS),
            )
            return EnforcementResult(
                checked=True,
                blocked=getattr(result, "blocked", False),
                injection_detected=getattr(result, "injection_detected", False),
                pii_detected=getattr(result, "pii_detected", False),
                reason=getattr(result, "reason", ""),
            )
        except Exception:
            return self._fallback_check(content)

    def _fallback_check(self, content: str) -> EnforcementResult:
        injection = self._check_injection(content)
        pii = self._check_pii(content)
        return EnforcementResult(
            checked=True, blocked=False, injection_detected=injection, pii_detected=pii
        )

    def _check_injection(self, content: str) -> bool:
        return any(p.search(content) for p in _INJECTION_PATTERNS)

    def _check_pii(self, content: str) -> bool:
        return any(p.search(content) for p in _PII_PATTERNS)
