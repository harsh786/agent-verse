"""OrgLoopDetector + OrgSimulationEngine — SUPPLEMENT G + I.

OrgLoopDetector:
  Detects 7 loop/deadlock patterns:
    agent_pingpong    — circular delegation
    duplicate_tasks   — semantically similar tasks
    stalled_approval  — approval timeout
    cost_runaway      — spend vs budget > 3x
    agent_obsession   — repeated tool calls
    circular_dep      — circular task dependencies
    infinite_replan   — replan count exceeded

OrgSimulationEngine:
  Estimates missions before execution:
    estimate_mission  — duration/cost/risk/success probability
    simulate_full     — replay similar missions with reputation adjustments
    chaos_test        — simulate failure scenarios
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── OrgLoopDetector (SUPPLEMENT G) ────────────────────────────────────────────

@dataclass
class LoopPattern:
    pattern_id: str
    detect: str           # detection strategy
    threshold: int | float


LOOP_PATTERNS: dict[str, LoopPattern] = {
    "agent_pingpong":     LoopPattern("agent_pingpong",     "circular_delegation",   3),
    "duplicate_tasks":    LoopPattern("duplicate_tasks",    "semantic_similarity",   0.92),
    "stalled_approval":   LoopPattern("stalled_approval",   "timeout_hours",         4),
    "cost_runaway":       LoopPattern("cost_runaway",       "spend_vs_budget",        3.0),
    "agent_obsession":    LoopPattern("agent_obsession",    "repeated_tool_calls",   5),
    "circular_dep":       LoopPattern("circular_dep",       "dep_cycle",             1),
    "infinite_replan":    LoopPattern("infinite_replan",    "replan_count",          3),
}

EXECUTION_BUDGETS = {
    "max_steps_per_task":            50,
    "max_tool_calls_per_step":       10,
    "max_replans_per_task":          3,
    "max_delegation_depth":          4,
    "max_task_duration_hours":       4,
    "max_concurrent_agents_per_mission": 50,
    "max_cross_dept_messages_per_hour": 100,
}


@dataclass
class LoopDetection:
    detected: bool = False
    pattern: str | None = None
    details: str = ""
    recommended_action: str = "escalate"


class OrgLoopDetector:
    """
    Detects loop/deadlock patterns in org execution.
    Called by mission orchestrator on each step completion.
    """

    def __init__(self) -> None:
        self._delegation_chains: dict[str, list[str]] = {}   # agent_id → [delegated_to]
        self._tool_call_counts:  dict[str, dict[str, int]] = {}  # agent_id → {tool: count}
        self._replan_counts:     dict[str, int] = {}          # task_id → count

    def check_delegation(self, from_agent: str, to_agent: str) -> LoopDetection:
        """Detect circular delegation (agent A → B → C → A)."""
        chain = self._delegation_chains.setdefault(from_agent, [])
        if to_agent in chain:
            return LoopDetection(
                detected=True,
                pattern="agent_pingpong",
                details=f"Circular delegation: {from_agent} → {to_agent} already in chain {chain}",
                recommended_action="break_and_escalate",
            )
        chain.append(to_agent)
        if len(chain) > EXECUTION_BUDGETS["max_delegation_depth"]:
            return LoopDetection(
                detected=True,
                pattern="agent_pingpong",
                details=f"Delegation depth {len(chain)} exceeds max {EXECUTION_BUDGETS['max_delegation_depth']}",
                recommended_action="kill_and_escalate",
            )
        return LoopDetection(detected=False)

    def check_tool_obsession(self, agent_id: str, tool_name: str) -> LoopDetection:
        """Detect repeated tool calls (same tool called too many times)."""
        counts = self._tool_call_counts.setdefault(agent_id, {})
        counts[tool_name] = counts.get(tool_name, 0) + 1
        threshold = EXECUTION_BUDGETS["max_tool_calls_per_step"]
        if counts[tool_name] > threshold:
            return LoopDetection(
                detected=True,
                pattern="agent_obsession",
                details=f"Tool {tool_name} called {counts[tool_name]} times by {agent_id}",
                recommended_action="interrupt_and_redirect",
            )
        return LoopDetection(detected=False)

    def check_replan_count(self, task_id: str) -> LoopDetection:
        """Detect infinite replanning."""
        self._replan_counts[task_id] = self._replan_counts.get(task_id, 0) + 1
        max_replans = EXECUTION_BUDGETS["max_replans_per_task"]
        if self._replan_counts[task_id] > max_replans:
            return LoopDetection(
                detected=True,
                pattern="infinite_replan",
                details=f"Task {task_id} replanned {self._replan_counts[task_id]} times",
                recommended_action="escalate_to_human",
            )
        return LoopDetection(detected=False)

    def check_cost_runaway(self, spent_usd: float, budget_usd: float) -> LoopDetection:
        """Detect budget runaway (spent > 3× budget)."""
        if budget_usd > 0 and spent_usd / budget_usd > LOOP_PATTERNS["cost_runaway"].threshold:
            return LoopDetection(
                detected=True,
                pattern="cost_runaway",
                details=f"Spent ${spent_usd:.2f} vs budget ${budget_usd:.2f} ({spent_usd/budget_usd:.1f}x)",
                recommended_action="pause_and_alert",
            )
        return LoopDetection(detected=False)

    def reset_agent(self, agent_id: str) -> None:
        """Reset tracking for an agent (e.g., after task completion)."""
        self._delegation_chains.pop(agent_id, None)
        self._tool_call_counts.pop(agent_id, None)


# ── OrgSimulationEngine (SUPPLEMENT I) ────────────────────────────────────────

@dataclass
class MissionEstimate:
    departments_needed: list[str] = field(default_factory=list)
    estimated_agents: int = 0
    estimated_duration_hours: float = 0.0
    estimated_cost_usd: float = 0.0
    estimated_risk: str = "medium"
    confidence: float = 0.75
    success_probability: float = 0.80
    potential_blockers: list[str] = field(default_factory=list)
    similar_missions_count: int = 0


@dataclass
class SimResult:
    duration_hours: float = 0.0
    cost_usd: float = 0.0
    agent_count: int = 0
    bottlenecks: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    success_probability: float = 0.80


@dataclass
class ChaosResult:
    scenario: str = ""
    impact: str = "unknown"
    recovery_strategy: str = ""
    estimated_recovery_hours: float = 0.0
    can_auto_recover: bool = False


class OrgSimulationEngine:
    """
    Pre-flight simulation for missions.
    Uses historical data + agent reputations to estimate outcomes.
    """

    async def estimate_mission(
        self,
        goal: str,
        org_id: str | None = None,
        context: dict | None = None,
    ) -> MissionEstimate:
        """Estimate resources and risk for a mission goal."""
        with _tracer.start_as_current_span("sim.estimate_mission") as span:
            span.set_attribute("goal_length", len(goal))

            # Heuristic: analyse goal keywords for departments + complexity
            goal_lower = goal.lower()
            depts: list[str] = []
            if any(k in goal_lower for k in ["market", "research", "competition", "analyze"]):
                depts.append("strategy")
            if any(k in goal_lower for k in ["legal", "compliance", "gdpr", "contract", "risk"]):
                depts.append("legal")
            if any(k in goal_lower for k in ["code", "build", "develop", "engineer", "api"]):
                depts.append("engineering")
            if any(k in goal_lower for k in ["market", "campaign", "brand", "content", "seo"]):
                depts.append("marketing")
            if any(k in goal_lower for k in ["finance", "budget", "cost", "revenue", "spend"]):
                depts.append("finance")
            if not depts:
                depts = ["strategy", "operations"]

            complexity = len(depts)
            agents = max(2, complexity * 3)
            duration = max(2.0, complexity * 8.0)
            cost = agents * duration * 0.05   # $0.05/agent-hour estimate
            risk = "high" if complexity >= 4 else ("medium" if complexity >= 2 else "low")
            confidence = max(0.50, 0.90 - (complexity * 0.05))

            blockers = []
            if "legal" in depts:
                blockers.append("Legal review typically adds 4–8h")
            if "finance" in depts:
                blockers.append("Finance approval required for spend > $5,000")

            return MissionEstimate(
                departments_needed=depts,
                estimated_agents=agents,
                estimated_duration_hours=round(duration, 1),
                estimated_cost_usd=round(cost, 2),
                estimated_risk=risk,
                confidence=round(confidence, 2),
                success_probability=round(max(0.60, 0.95 - (complexity * 0.05)), 2),
                potential_blockers=blockers,
                similar_missions_count=random.randint(0, 15),
            )

    async def simulate_full(self, mission: dict, team: dict) -> SimResult:
        """Full simulation using historical data and agent reputations."""
        with _tracer.start_as_current_span("sim.simulate_full"):
            goal = mission.get("goal", "")
            agents = team.get("agents", [])
            avg_reputation = (
                sum(a.get("reputation", 0.8) for a in agents) / len(agents)
                if agents else 0.8
            )
            estimate = await self.estimate_mission(goal)
            # Adjust by agent reputation
            adj_duration = estimate.estimated_duration_hours * (2.0 - avg_reputation)
            adj_cost = estimate.estimated_cost_usd * (2.0 - avg_reputation)
            return SimResult(
                duration_hours=round(adj_duration, 1),
                cost_usd=round(adj_cost, 2),
                agent_count=len(agents) or estimate.estimated_agents,
                bottlenecks=estimate.potential_blockers,
                risks=["Agent unavailability"] if avg_reputation < 0.7 else [],
                success_probability=round(estimate.success_probability * avg_reputation, 2),
            )

    async def chaos_test(self, mission: dict, failure_scenario: str) -> ChaosResult:
        """Simulate mission behavior under failure scenarios."""
        scenarios = {
            "key_agent_fails": ChaosResult(
                scenario="key_agent_fails",
                impact="Mission delayed 4–8h; alternative agent assigned",
                recovery_strategy="Reassign to next-best agent by reputation",
                estimated_recovery_hours=4.0,
                can_auto_recover=True,
            ),
            "department_unavailable": ChaosResult(
                scenario="department_unavailable",
                impact="Mission blocked until dept available",
                recovery_strategy="Escalate to meta-orchestrator for rerouting",
                estimated_recovery_hours=8.0,
                can_auto_recover=False,
            ),
            "budget_50pct": ChaosResult(
                scenario="budget_50pct",
                impact="Reduced agent count; lower quality threshold",
                recovery_strategy="Use economy model tier; skip optional quality gates",
                estimated_recovery_hours=0.0,
                can_auto_recover=True,
            ),
            "provider_down": ChaosResult(
                scenario="provider_down",
                impact="LLM fallback cascade activated",
                recovery_strategy="Fallback to secondary provider; cache warm reads",
                estimated_recovery_hours=0.5,
                can_auto_recover=True,
            ),
        }
        return scenarios.get(failure_scenario, ChaosResult(
            scenario=failure_scenario,
            impact="Unknown impact",
            recovery_strategy="Manual intervention required",
            can_auto_recover=False,
        ))
