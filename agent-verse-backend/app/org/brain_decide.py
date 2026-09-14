from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.org.brain_settings import AutonomySettings
from app.org.brain_types import BrainDecision


@dataclass(frozen=True)
class OrgSnapshot:
    blocked: int
    failed: int
    active_missions: int
    open_goals: list[str]
    goals_in_flight: frozenset[str]


def decide(
    snapshot: OrgSnapshot,
    settings: AutonomySettings,
    *,
    propose_goal_mission: Callable[[str], tuple[str, float, str] | None],
) -> list[BrainDecision]:
    out: list[BrainDecision] = []
    # Reactive: step in on trouble.
    if snapshot.blocked > settings.blocked_threshold:
        out.append(BrainDecision("reactive", f"{snapshot.blocked} tasks blocked — remediate",
                                 "", 5.0, "low", "reactive:blocked"))
    if snapshot.failed > settings.failed_threshold:
        out.append(BrainDecision("reactive", f"{snapshot.failed} tasks failed — investigate",
                                 "", 5.0, "low", "reactive:failed"))
    # Proactive: pursue the charter when idle.
    if snapshot.active_missions <= settings.idle_threshold:
        for goal in snapshot.open_goals:
            if goal in snapshot.goals_in_flight:
                continue
            planned = propose_goal_mission(goal)
            if planned is None:
                continue
            rationale, est_cost, risk = planned
            out.append(BrainDecision("proactive", rationale, goal, float(est_cost), risk,
                                     f"proactive:{goal}"))
            break  # one proactive proposal per tick
    return out
