"""Planner adapter over ``goal_refinement`` — Task 7 of the Autonomous Org Brain.

``OrgBrain`` (Task 6, ``app/org/brain.py``) is constructor-injected with a
``Planner`` callable of shape::

    planner(goal: str, org_context: dict) -> tuple[str, float, str] | None

and calls it *synchronously* inside ``brain_decide.decide``'s
``propose_goal_mission`` lambda. ``make_planner`` here wraps the existing,
already-synchronous ``GoalRefinementPipeline.refine`` (see
``app/org/goal_refinement.py``) — which proposes a refined mission spec from a
raw goal + org context and already estimates ``estimated_budget_usd`` — into
that exact shape.

On *any* exception raised by the refiner (including the pipeline's own
injection-pattern ``ValueError``), the returned callable swallows it and
returns ``None`` so the brain simply degrades to "no proactive proposal" for
that tick rather than failing the whole DECIDE phase.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.observability.logging import get_logger

_log = get_logger(__name__)


class _Refiner(Protocol):
    """Structural type for anything shaped like ``GoalRefinementPipeline``."""

    def refine(self, raw_goal: str, org_context: dict[str, Any] | None = None) -> Any: ...


def make_planner(
    refiner: _Refiner,
) -> Callable[[str, dict[str, Any]], tuple[str, float, str] | None]:
    """Build a sync ``Planner`` (see ``app/org/brain.py``) backed by ``refiner``.

    ``refiner`` is expected to expose a synchronous ``refine(raw_goal,
    org_context) -> RefinedMissionSpec``-shaped object (duck-typed: only
    ``.refined_goal``, ``.estimated_budget_usd`` and ``.risk_level`` are read,
    each defaulted when absent so a minimal fake still works).
    """

    def planner(goal: str, org_context: dict[str, Any]) -> tuple[str, float, str] | None:
        try:
            refined = refiner.refine(goal, org_context)
        except Exception as exc:  # any failure -> None (see module docstring)
            _log.warning(
                "org_brain_planner_refine_failed",
                goal_length=len(goal),
                error=str(exc)[:200],
            )
            return None

        rationale = str(getattr(refined, "refined_goal", None) or goal)
        est_cost_usd = float(getattr(refined, "estimated_budget_usd", 0.0) or 0.0)
        risk_level = str(getattr(refined, "risk_level", None) or "low")
        return rationale, est_cost_usd, risk_level

    return planner
