"""Pydantic v2 models mirroring the AgentVerse REST API schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GoalStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Goal(BaseModel):
    goal_id: str
    goal: str
    status: GoalStatus
    created_at: datetime
    updated_at: datetime | None = None
    result: str | None = None
    error: str | None = None
    steps_total: int = 0
    steps_completed: int = 0
    cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalEvent(BaseModel):
    type: str
    goal_id: str
    ts: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class Agent(BaseModel):
    agent_id: str
    name: str
    autonomy_mode: str
    model: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class Connector(BaseModel):
    server_id: str
    name: str
    url: str
    status: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalSubmitRequest(BaseModel):
    goal: str
    priority: str = "normal"
    dry_run: bool = False
    agent_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class AgentCreateRequest(BaseModel):
    name: str
    goal_template: str = ""
    autonomy_mode: str = "bounded-autonomous"
    connector_ids: list[str] = Field(default_factory=list)
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    allowed_collection_ids: list[str] = Field(default_factory=list)
    eval_suite_id: str | None = None
    policy_ids: list[str] = Field(default_factory=list)
    system_prompt: str = ""        # matches backend CreateAgentRequest
    model_override: str = ""       # renamed from 'model' to match backend
    max_iterations: int = 15       # new field
    timeout_seconds: int = 300     # new field


class ConnectorRegisterRequest(BaseModel):
    name: str
    url: str
    auth_token: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Memory(BaseModel):
    memory_id: str
    content: str
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""


class Schedule(BaseModel):
    schedule_id: str
    name: str
    goal_template: str
    enabled: bool
    cron: str | None = None
    next_run_at: str | None = None
    created_at: str = ""


class GoalMetrics(BaseModel):
    active_goals: int
    total_goals: int
    success_rate: float
    avg_latency_ms: float
    cost_today_usd: float


class CostMetrics(BaseModel):
    total_cost_usd: float
    cost_by_day: list[dict[str, Any]] = Field(default_factory=list)
    cost_by_model: dict[str, float] = Field(default_factory=dict)
    daily_budget_usd: float
    budget_utilization: float


class SimulationResult(BaseModel):
    run_id: str
    goal: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    completed: bool
    error: str | None = None
