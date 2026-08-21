from __future__ import annotations

import enum
from dataclasses import dataclass, field


class CapabilityKind(str, enum.Enum):
    TOOL = "tool"
    MODEL = "model"
    AGENT = "agent"
    SKILL = "skill"
    RETRIEVER = "retriever"
    EMBEDDER = "embedder"
    PARSER = "parser"
    CHUNKER = "chunker"
    GUARDRAIL = "guardrail"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CapabilityProfile:
    capability_id: str
    kind: CapabilityKind
    tenant_scope: str
    input_modalities: list[str]
    output_modalities: list[str]
    risk_level: RiskLevel
    cost_class: str
    latency_class: str
    reliability_score: float
    required_permissions: list[str]
    description: str = ""
    tags: list[str] = field(default_factory=list)
