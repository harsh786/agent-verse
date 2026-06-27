"""Civilization domain models — pure data types, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CivilizationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class MemberStatus(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    RETIRED = "retired"
    SPAWNING = "spawning"
    DEBATING = "debating"
    FAILED = "failed"


class SpawnDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


class LearningStatus(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    REJECTED = "rejected"


@dataclass
class Constitution:
    """Immutable per-civilization policy. Pure data — no I/O."""
    max_depth: int = 4
    max_total_agents: int = 50
    max_concurrent_agents: int = 10
    total_budget_usd: float = 100.0
    per_agent_budget_usd: float = 10.0
    budget_decay: float = 0.6
    spawn_rate_limit_per_min: int = 20
    high_risk_requires_hitl: bool = True
    inherited_policy_ids: list[str] = field(default_factory=list)
    autonomy_ceiling: str = "bounded-autonomous"
    reputation_floor: float = 0.2
    idle_ttl_seconds: int = 3600
    min_viable_roster: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> Constitution:
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_fields})

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    def compute_child_budget(self, parent_budget: float, depth: int) -> float:
        """Exponential budget decay per depth level."""
        return parent_budget * (self.budget_decay ** depth)


@dataclass
class SpawnContext:
    """Everything the Governor needs to evaluate a spawn request."""
    civilization_id: str
    tenant_id: str
    requester_agent_id: str
    requested_capability: str
    goal_text: str
    depth: int
    current_total_agents: int
    current_concurrent_agents: int
    civilization_budget_spent_usd: float
    spawn_rate_last_min: int
    parent_budget_usd: float
    parent_policy_ids: list[str] = field(default_factory=list)


@dataclass
class SpawnVerdict:
    decision: SpawnDecision
    reason: str
    allowed_budget_usd: float = 0.0
    clamped_autonomy: str = "bounded-autonomous"
    inherited_policy_ids: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class BreachContext:
    civilization_id: str
    tenant_id: str
    budget_spent_usd: float
    budget_total_usd: float
    spawn_rate_last_min: int
    total_agents: int
    concurrent_agents: int


@dataclass
class BreachVerdict:
    breached: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class MetaAgentConfigValidated:
    """Result of Governor validating a MetaAgentPlanner output."""
    name: str
    goal_template: str
    autonomy_mode: str
    connector_ids: list[str]
    trigger_config: dict
    system_prompt: str
    max_iterations: int
    allowed_collection_ids: list[str]
    policy_ids: list[str]
    eval_suite_id: str | None = None
