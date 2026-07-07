from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RuntimeConstraints:
    allowed_capabilities: list[str]
    denied_capabilities: list[str]
    required_approvals: list[str]
    max_cost_usd: float
    audit_level: str
    data_classes_allowed: list[str] = field(default_factory=lambda: ["public", "internal"])
    compliance_constraints: list[str] = field(default_factory=list)
    max_latency_ms: int = 120_000
