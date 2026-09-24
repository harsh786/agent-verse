"""Guardrails 2.0 data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class GuardrailLayer(StrEnum):
    GOAL = "goal"
    PLAN = "plan"
    STEP = "step"
    TOOL_ARGS = "tool_args"
    TOOL_OUTPUT = "tool_output"
    FINAL_OUTPUT = "final_output"
    MEMORY_WRITE = "memory_write"
    RAG_INGEST = "rag_ingest"
    GRAPH_EXTRACT = "graph_extract"


class GuardrailAction(StrEnum):
    LOG = "log"
    WARN = "warn"
    REDACT = "redact"
    BLOCK = "block"
    REQUIRE_HITL = "require_hitl"
    QUARANTINE = "quarantine"


class ViolationCategory(StrEnum):
    PII = "pii"
    PHI = "phi"
    PCI = "pci"
    SECRETS = "secrets"
    PROMPT_INJECTION = "prompt_injection"
    TOOL_INJECTION = "tool_injection"
    DATA_EXFILTRATION = "data_exfiltration"
    TOXICITY = "toxicity"
    COPYRIGHT = "copyright"
    JAILBREAK = "jailbreak"
    UNSAFE_CODE = "unsafe_code"
    UNSAFE_ADVICE = "unsafe_advice"


class ComplianceBundle(StrEnum):
    GDPR = "gdpr"
    SOC2 = "soc2"
    HIPAA = "hipaa"
    PCI = "pci"
    DPDP = "dpdp"
    SOX = "sox"


@dataclass
class GuardrailRule:
    """A single guardrail rule."""

    rule_id: str
    tenant_id: str
    name: str
    rule_type: str
    layers: list[GuardrailLayer] = field(default_factory=lambda: [GuardrailLayer.STEP])
    action: GuardrailAction = GuardrailAction.BLOCK
    categories: list[ViolationCategory] = field(default_factory=list)
    severity: str = "high"
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    created_at: str | None = None


@dataclass
class GuardrailViolation:
    """A guardrail violation record."""

    violation_id: str
    tenant_id: str
    rule_id: str
    rule_name: str
    layer: str
    action_taken: str
    category: str
    content_preview: str  # Redacted/truncated preview
    severity: str
    goal_id: str | None = None
    step_description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


# Compliance bundle → default rules mapping
COMPLIANCE_BUNDLES: dict[ComplianceBundle, list[dict[str, Any]]] = {
    ComplianceBundle.GDPR: [
        {
            "name": "Block PII in outputs",
            "rule_type": "pii_detection",
            "layers": ["final_output", "memory_write"],
            "action": "redact",
            "categories": ["pii"],
            "severity": "critical",
        },
        {
            "name": "Block PII in RAG ingest",
            "rule_type": "pii_detection",
            "layers": ["rag_ingest"],
            "action": "block",
            "categories": ["pii"],
            "severity": "high",
        },
    ],
    ComplianceBundle.HIPAA: [
        {
            "name": "Block PHI anywhere",
            "rule_type": "pii_detection",
            "layers": ["goal", "step", "tool_output", "final_output", "memory_write"],
            "action": "block",
            "categories": ["phi"],
            "severity": "critical",
        },
    ],
    ComplianceBundle.SOC2: [
        {
            # rule_type was "keyword_block", which GuardrailsEngine._check_keywords
            # matches against ``rule.config["keywords"]`` — a list this bundle spec
            # never supplies, and ``enable_compliance_bundle`` (app/api/
            # guardrails_v2.py) never copies a bundle spec's ``config`` onto the
            # GuardrailRule it constructs anyway. The rule could therefore never
            # trigger for ANY content: enabling the SOC2 bundle silently gave zero
            # secret redaction while claiming to "block secrets in outputs".
            # "pii_detection" is the rule_type the baseline default rules and the
            # PCI/GDPR bundles already use for this exact SECRETS category — it
            # dispatches to _check_pii, which regex-matches real secret formats
            # (AWS/OpenAI/Anthropic/GitHub/Google keys) via _SECRET_PATTERNS.
            "name": "Block secrets in outputs",
            "rule_type": "pii_detection",
            "layers": ["final_output", "tool_output"],
            "action": "redact",
            "categories": ["secrets"],
            "severity": "critical",
        },
        {
            "name": "Block prompt injection",
            "rule_type": "prompt_injection",
            "layers": ["goal", "step"],
            "action": "block",
            "categories": ["prompt_injection"],
            "severity": "high",
        },
    ],
    ComplianceBundle.PCI: [
        {
            "name": "Block PCI data",
            "rule_type": "pii_detection",
            "layers": ["final_output", "memory_write", "rag_ingest"],
            "action": "block",
            "categories": ["pci"],
            "severity": "critical",
        },
    ],
}
