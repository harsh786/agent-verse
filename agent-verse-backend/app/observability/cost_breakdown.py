"""Per-goal, per-role cost breakdown tracking.

Tracks input/output tokens and estimated cost per LLM role (planner/executor/verifier)
for a single goal execution. Results are stored in goal_events for the UI to display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoleCostEntry:
    role: str  # "planner" | "executor" | "verifier"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0


@dataclass
class GoalCostBreakdown:
    goal_id: str
    entries: list[RoleCostEntry] = field(default_factory=list)

    def record(
        self,
        role: str,
        model: str,
        input_tok: int,
        output_tok: int,
        cost: float,
    ) -> None:
        for e in self.entries:
            if e.role == role and e.model == model:
                e.input_tokens += input_tok
                e.output_tokens += output_tok
                e.cost_usd += cost
                e.calls += 1
                return
        self.entries.append(
            RoleCostEntry(
                role=role,
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cost_usd=cost,
                calls=1,
            )
        )

    def total_cost(self) -> float:
        return sum(e.cost_usd for e in self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "total_cost_usd": self.total_cost(),
            "roles": [
                {
                    "role": e.role,
                    "model": e.model,
                    "input_tokens": e.input_tokens,
                    "output_tokens": e.output_tokens,
                    "cost_usd": round(e.cost_usd, 6),
                    "calls": e.calls,
                }
                for e in self.entries
            ],
        }


# Per-goal registry (cleared when goal completes)
_goal_breakdowns: dict[str, GoalCostBreakdown] = {}


def get_breakdown(goal_id: str) -> GoalCostBreakdown:
    if goal_id not in _goal_breakdowns:
        _goal_breakdowns[goal_id] = GoalCostBreakdown(goal_id=goal_id)
    return _goal_breakdowns[goal_id]


def record_role_cost(
    goal_id: str,
    role: str,
    model: str,
    input_tok: int,
    output_tok: int,
    cost: float,
) -> None:
    get_breakdown(goal_id).record(role, model, input_tok, output_tok, cost)


def finalize_breakdown(goal_id: str) -> dict[str, Any]:
    """Get the final breakdown and remove from registry."""
    bd = _goal_breakdowns.pop(goal_id, GoalCostBreakdown(goal_id=goal_id))
    return bd.to_dict()
