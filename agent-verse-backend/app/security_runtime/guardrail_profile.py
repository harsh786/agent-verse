"""GuardrailProfileSelector — selects guardrail bundle per risk level and compliance tags."""
from __future__ import annotations
import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


class GuardrailBundle(str, enum.Enum):
    DEFAULT = "default"
    STRICT = "strict"
    REGULATED = "regulated"
    DEVELOPER = "developer"
    RPA = "rpa"


@dataclass
class GuardrailConfig:
    name: GuardrailBundle
    scan_prompt_injection: bool = True
    scan_output_pii: bool = False
    scan_toxicity: bool = True
    exfiltration_guard_enabled: bool = False
    pii_redaction_enabled: bool = False
    output_schema_validation: bool = False
    block_on_injection: bool = True
    block_on_pii: bool = False
    max_output_tokens: int = 8000
    enabled_scanners: list[str] = field(default_factory=lambda: ["injection", "toxicity"])


class GuardrailProfileSelector:
    def select(self, profile: "GoalRuntimeProfile", *, tenant_ctx: "TenantContext") -> GuardrailConfig:
        from app.orchestration.runtime_profile import RiskLevel
        risk = profile.properties.risk
        compliance = profile.security.compliance_tags
        regulated_tags = {"gdpr", "hipaa", "pci", "soc2", "dpdp", "sox"}
        if compliance and set(compliance) & regulated_tags:
            return GuardrailConfig(
                name=GuardrailBundle.REGULATED,
                scan_prompt_injection=True, scan_output_pii=True, scan_toxicity=True,
                exfiltration_guard_enabled=True, pii_redaction_enabled=True,
                output_schema_validation=True, block_on_injection=True, block_on_pii=True,
                enabled_scanners=["injection", "toxicity", "pii", "exfiltration", "schema"],
            )
        if risk == RiskLevel.CRITICAL:
            return GuardrailConfig(
                name=GuardrailBundle.STRICT,
                scan_prompt_injection=True, scan_output_pii=True, scan_toxicity=True,
                exfiltration_guard_enabled=True, output_schema_validation=True,
                block_on_injection=True,
                enabled_scanners=["injection", "toxicity", "exfiltration", "schema"],
            )
        if risk == RiskLevel.HIGH:
            return GuardrailConfig(
                name=GuardrailBundle.STRICT,
                scan_prompt_injection=True, scan_output_pii=True, scan_toxicity=True,
                block_on_injection=True,
                enabled_scanners=["injection", "toxicity", "pii"],
            )
        return GuardrailConfig(
            name=GuardrailBundle.DEFAULT,
            scan_prompt_injection=True, scan_output_pii=False, scan_toxicity=True,
            block_on_injection=True,
            enabled_scanners=["injection", "toxicity"],
        )
