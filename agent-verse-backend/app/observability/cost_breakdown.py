"""Per-goal, per-role cost breakdown tracking.

Tracks input/output tokens and estimated cost per LLM role (planner/executor/verifier)
for a single goal execution. Results are stored in goal_events for the UI to display.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict, dataclass, field
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

    def to_state(self) -> dict[str, Any]:
        """Lossless serialization for persistence (raw, unrounded entries)."""
        return {"goal_id": self.goal_id, "entries": [asdict(e) for e in self.entries]}

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> GoalCostBreakdown:
        return cls(
            goal_id=data["goal_id"],
            entries=[RoleCostEntry(**e) for e in data.get("entries", [])],
        )

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


# Per-goal registry (also serves as the local cache / fallback when a backend is set).
_goal_breakdowns: dict[str, GoalCostBreakdown] = {}

# Optional Redis-like backend (synchronous get/set/delete). When configured, breakdowns are
# persisted so the cost-metrics endpoint can still serve them after a process restart
# (production reads via get_breakdown and never calls finalize_breakdown, so the data is
# meant to survive for later retrieval). Left unset -> pure in-memory, as before.
#
# Wired (D-21): the FastAPI lifespan calls ``configure_persistence`` with a sync
# Redis client, so per-goal cost breakdowns survive a restart on the running server.
_backend: Any | None = None


def configure_persistence(redis_client: Any | None) -> None:
    """Enable Redis-backed persistence (client must expose sync get/set/delete)."""
    global _backend
    _backend = redis_client


def reset_persistence() -> None:
    """Disable persistence and revert to the in-memory registry (used by tests)."""
    global _backend
    _backend = None


def _redis_key(goal_id: str) -> str:
    return f"cost_breakdown:{goal_id}"


def _backend_load(goal_id: str) -> GoalCostBreakdown | None:
    """Load a breakdown from the backend, or None on no-backend / miss / error."""
    if _backend is None:
        return None
    try:
        raw = _backend.get(_redis_key(goal_id))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return GoalCostBreakdown.from_state(json.loads(raw))
    except (ValueError, TypeError, KeyError):
        return None


def _persist(goal_id: str, bd: GoalCostBreakdown) -> None:
    # Keep the local cache current too — it is the fallback when the backend is unreachable.
    _goal_breakdowns[goal_id] = bd
    if _backend is None:
        return
    # Best-effort persistence; never break cost recording on a backend hiccup.
    with contextlib.suppress(Exception):
        _backend.set(_redis_key(goal_id), json.dumps(bd.to_state()))


def get_breakdown(goal_id: str) -> GoalCostBreakdown:
    """Return the current breakdown for *goal_id* (read path used by the metrics API)."""
    if _backend is not None:
        bd = _backend_load(goal_id)
        if bd is not None:
            return bd
        # Backend miss or error -> fall back to the local cache (may hold in-flight data).
        return _goal_breakdowns.get(goal_id, GoalCostBreakdown(goal_id=goal_id))
    return _goal_breakdowns.setdefault(goal_id, GoalCostBreakdown(goal_id=goal_id))


def record_role_cost(
    goal_id: str,
    role: str,
    model: str,
    input_tok: int,
    output_tok: int,
    cost: float,
) -> None:
    if _backend is not None:
        bd = _backend_load(goal_id) or _goal_breakdowns.setdefault(
            goal_id, GoalCostBreakdown(goal_id=goal_id)
        )
        bd.record(role, model, input_tok, output_tok, cost)
        _persist(goal_id, bd)
    else:
        get_breakdown(goal_id).record(role, model, input_tok, output_tok, cost)


def finalize_breakdown(goal_id: str) -> dict[str, Any]:
    """Get the final breakdown and remove it from the registry (and backend)."""
    if _backend is not None:
        bd = (
            _backend_load(goal_id)
            or _goal_breakdowns.get(goal_id)
            or GoalCostBreakdown(goal_id=goal_id)
        )
        _goal_breakdowns.pop(goal_id, None)
        with contextlib.suppress(Exception):
            _backend.delete(_redis_key(goal_id))
        return bd.to_dict()
    bd = _goal_breakdowns.pop(goal_id, GoalCostBreakdown(goal_id=goal_id))
    return bd.to_dict()
