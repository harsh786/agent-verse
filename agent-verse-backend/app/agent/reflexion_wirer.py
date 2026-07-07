"""ReflexionWirer — automatically stores failure lessons in ReflexionStore.

Doc-1 §3.3 Level 3 / Doc-3 §4: Cross-goal learning via Reflexion.
After every goal failure, extract the lesson and store for future runs.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.state_runtime.reflexion_store import ReflexionStore


class ReflexionWirer:
    """Extracts and stores failure lessons after goal execution."""

    def __init__(self, store: "ReflexionStore | None" = None) -> None:
        if store is None:
            from app.state_runtime.reflexion_store import ReflexionStore
            store = ReflexionStore()
        self._store = store

    def extract_lesson(self, state: "AgentState") -> str | None:
        feedback = (state.verification_feedback or "").strip()
        if not feedback:
            return None
        goal = state.goal[:100]
        lesson = (f"For goal '{goal[:60]}': {feedback[:200]}"
                  if len(feedback) <= 200
                  else f"For goal '{goal[:60]}': {feedback[:200]}...")
        return lesson

    def maybe_store(self, state: "AgentState") -> bool:
        """Store a lesson if the goal failed and feedback is useful. Returns True if stored."""
        from app.agent.state import GoalStatus
        if state.status not in (GoalStatus.FAILED,):
            return False
        lesson = self.extract_lesson(state)
        if not lesson:
            return False

        failure_class = "unknown"
        feedback_lower = (state.verification_feedback or "").lower()
        if "permission" in feedback_lower or "unauthorized" in feedback_lower:
            failure_class = "auth_failure"
        elif "not found" in feedback_lower or "404" in feedback_lower:
            failure_class = "context_gap"
        elif "timeout" in feedback_lower:
            failure_class = "timeout"
        elif "rate limit" in feedback_lower or "429" in feedback_lower:
            failure_class = "rate_limit"

        self._store.record(
            tenant_id=state.tenant_ctx.tenant_id,
            lesson=lesson,
            source_goal_id=state.goal_id,
            failure_class=failure_class,
        )
        return True


_default_reflexion_wirer: ReflexionWirer | None = None


def get_reflexion_wirer() -> ReflexionWirer:
    global _default_reflexion_wirer
    if _default_reflexion_wirer is None:
        _default_reflexion_wirer = ReflexionWirer()
    return _default_reflexion_wirer
