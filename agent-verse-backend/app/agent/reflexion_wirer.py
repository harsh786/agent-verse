"""ReflexionWirer — automatically stores failure lessons in ReflexionStore.

Doc-1 §3.3 Level 3 / Doc-3 §4: Cross-goal learning via Reflexion.
After every goal failure, extract the lesson and store for future runs.
Now persists to Postgres via record_async() for cross-process durability.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.state_runtime.reflexion_store import ReflexionStore


class ReflexionWirer:
    """Extracts and stores failure lessons after goal execution."""

    def __init__(
        self,
        store: "ReflexionStore | None" = None,
        db_factory: Any = None,
    ) -> None:
        if store is None:
            from app.state_runtime.reflexion_store import ReflexionStore
            store = ReflexionStore()
        self._store = store
        self._db_factory = db_factory

    def extract_lesson(self, state: "AgentState") -> str | None:
        feedback = (state.verification_feedback or "").strip()
        if not feedback:
            return None
        goal = state.goal[:100]
        lesson = (f"For goal '{goal[:60]}': {feedback[:200]}"
                  if len(feedback) <= 200
                  else f"For goal '{goal[:60]}': {feedback[:200]}...")
        return lesson

    async def maybe_store_async(self, state: "AgentState") -> bool:
        """Async version: stores lesson in-memory AND persists to DB."""
        from app.agent.state import GoalStatus
        if state.status not in (GoalStatus.FAILED,):
            return False
        lesson = self.extract_lesson(state)
        if not lesson:
            return False

        failure_class = _classify_failure(state.verification_feedback or "")

        await self._store.record_async(
            tenant_id=state.tenant_ctx.tenant_id,
            lesson=lesson,
            source_goal_id=state.goal_id,
            failure_class=failure_class,
            db_factory=self._db_factory,
        )
        return True

    def maybe_store(self, state: "AgentState") -> bool:
        """Sync version: stores lesson in-memory only (backward compat).
        Prefer maybe_store_async() for cross-process durability.
        """
        from app.agent.state import GoalStatus
        if state.status not in (GoalStatus.FAILED,):
            return False
        lesson = self.extract_lesson(state)
        if not lesson:
            return False

        failure_class = _classify_failure(state.verification_feedback or "")

        self._store.record(
            tenant_id=state.tenant_ctx.tenant_id,
            lesson=lesson,
            source_goal_id=state.goal_id,
            failure_class=failure_class,
        )
        # Also fire-and-forget async persist if DB available
        if self._db_factory is not None:
            try:
                import asyncio
                asyncio.ensure_future(
                    self._store.record_async(
                        tenant_id=state.tenant_ctx.tenant_id,
                        lesson=lesson,
                        source_goal_id=state.goal_id,
                        failure_class=failure_class,
                        db_factory=self._db_factory,
                    )
                )
            except Exception:
                pass
        return True


def _classify_failure(feedback: str) -> str:
    f = feedback.lower()
    if "permission" in f or "unauthorized" in f:
        return "auth_failure"
    if "not found" in f or "404" in f:
        return "context_gap"
    if "timeout" in f:
        return "timeout"
    if "rate limit" in f or "429" in f:
        return "rate_limit"
    return "unknown"


_default_reflexion_wirer: ReflexionWirer | None = None


def get_reflexion_wirer(db_factory: Any = None) -> ReflexionWirer:
    global _default_reflexion_wirer
    if _default_reflexion_wirer is None or db_factory is not None:
        _default_reflexion_wirer = ReflexionWirer(db_factory=db_factory)
    return _default_reflexion_wirer
