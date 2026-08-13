"""Bounded deterministic swarm convergence evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ConvergenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    work_item_id: str
    mandatory: bool
    state: Literal["pending", "claimed", "completed", "failed"]
    result_digest: str | None = None


class ConvergenceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    converged: bool
    terminal: bool
    reason: str


def evaluate_convergence(
    items: tuple[ConvergenceItem, ...],
    *,
    criteria_met: bool,
    spent: Decimal,
    budget: Decimal,
    deadline: datetime,
    repeated_results: int,
) -> ConvergenceDecision:
    if spent > budget:
        return ConvergenceDecision(converged=False, terminal=True, reason="budget_exceeded")
    if datetime.now(UTC) >= deadline:
        return ConvergenceDecision(converged=False, terminal=True, reason="deadline_exceeded")
    if repeated_results >= 3:
        return ConvergenceDecision(converged=False, terminal=True, reason="no_progress")
    mandatory = tuple(item for item in items if item.mandatory)
    if any(item.state == "failed" for item in mandatory):
        return ConvergenceDecision(converged=False, terminal=True, reason="mandatory_failed")
    converged = criteria_met and all(item.state == "completed" for item in mandatory)
    return ConvergenceDecision(
        converged=converged,
        terminal=converged,
        reason="objective_satisfied" if converged else "work_remaining",
    )


__all__ = ["ConvergenceDecision", "ConvergenceItem", "evaluate_convergence"]
