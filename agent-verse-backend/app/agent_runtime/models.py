"""Agent Runtime 2.0 - formalized roles and execution models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentRole(StrEnum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    CRITIC = "critic"
    JUDGE = "judge"
    REFLECTOR = "reflector"
    SYNTHESIZER = "synthesizer"
    SUBAGENT = "subagent"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_HUMAN = "waiting_human"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PlanStep:
    """A typed step in an execution plan."""

    step_id: str
    description: str
    role: AgentRole = AgentRole.EXECUTOR
    dependencies: list[str] = field(default_factory=list)  # step_ids
    risk_level: RiskLevel = RiskLevel.LOW
    expected_evidence: list[str] = field(default_factory=list)
    output_contract: dict[str, Any] = field(default_factory=dict)
    tools_required: list[str] = field(default_factory=list)
    timeout_seconds: int = 60
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    error: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    model_used: str | None = None
    cost_usd: float = 0.0


@dataclass
class AgentExecutionPlan:
    """A typed execution plan for an agent run."""

    plan_id: str
    goal_id: str
    tenant_id: str
    goal_text: str
    strategy: str = "single_agent"
    steps: list[PlanStep] = field(default_factory=list)
    created_at: str | None = None
    agent_id: str | None = None
    model_assignments: dict[str, str] = field(default_factory=dict)  # role → model


@dataclass
class AgentRunTrace:
    """Complete execution trace for an agent run."""

    trace_id: str
    goal_id: str
    tenant_id: str
    plan: AgentExecutionPlan | None = None
    role_calls: list[dict[str, Any]] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    duration_ms: float = 0.0
    success: bool = False
    error: str | None = None
    model_selections: list[dict[str, Any]] = field(default_factory=list)
    runtime_profile_id: str | None = None  # GoalRuntimeProfile.profile_id
    patterns_used: list[str] = field(default_factory=list)  # active agent patterns
    rag_strategy_used: str = ""  # RAG strategy selected


@dataclass
class SubagentTask:
    """A task assigned to a subagent."""

    task_id: str
    parent_goal_id: str
    child_goal_id: str | None = None
    tenant_id: str = ""
    description: str = ""
    status: str = "pending"  # pending | running | complete | failed | cancelled
    agent_id: str | None = None
    cost_attributed: float = 0.0
    spawned_at: str | None = None
    completed_at: str | None = None
