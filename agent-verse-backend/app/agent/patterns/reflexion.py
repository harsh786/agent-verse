"""Reflexion pattern — episodic memory for self-improvement from failure.

Extracts lessons from goal failures and stores them for future recall.
The _node_reflect graph node handles failure diagnosis.
This adapter provides lesson storage + recall as a standalone component.
"""

from __future__ import annotations

from typing import Any

from app.agent.patterns.base import AgentPattern, PatternState


class ReflexionPattern(AgentPattern):
    """Reflexion: store failure lessons, recall for future goals."""

    def __init__(self, reflexion_store: Any = None) -> None:
        self._store = reflexion_store

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        from app.state_runtime.reflexion_store import ReflexionStore

        self._store = ReflexionStore()
        return self._store

    @property
    def pattern_id(self) -> str:
        return "reflexion"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Reflexion: extract and persist failure lessons after each goal. "
            "Recall lessons at planning time to avoid repeating mistakes. "
            "Backed by ReflexionStore (in-memory + Postgres)."
        )

    @property
    def node_name(self) -> str:
        return "_node_reflect"

    def is_compatible(self, goal_properties: Any) -> bool:
        return True

    async def store_lesson(
        self,
        *,
        tenant_id: str,
        goal: str,
        feedback: str,
        source_goal_id: str,
        failure_class: str = "unknown",
        db_factory: Any = None,
    ) -> bool:
        """Store a failure lesson. Returns True if stored."""
        if not feedback or not feedback.strip():
            return False
        store = self._get_store()
        lesson = f"For goal '{goal[:60]}': {feedback[:200]}"
        if db_factory is not None and hasattr(store, "record_async"):
            await store.record_async(
                tenant_id=tenant_id,
                lesson=lesson,
                source_goal_id=source_goal_id,
                failure_class=failure_class,
                db_factory=db_factory,
            )
        else:
            store.record(
                tenant_id=tenant_id,
                lesson=lesson,
                source_goal_id=source_goal_id,
                failure_class=failure_class,
            )
        return True

    def recall_lessons(self, *, tenant_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Recall recent failure lessons for a tenant."""
        result: list[dict[str, Any]] = self._get_store().recall(tenant_id=tenant_id, limit=limit)
        return result

    def format_for_context(self, lessons: list[dict[str, Any]]) -> str:
        """Format lessons as a context block for the planner prompt."""
        if not lessons:
            return ""
        lines = ["[Reflexion lessons from past failures — avoid these mistakes:]"]
        for i, item in enumerate(lessons[:5], 1):
            lines.append(f"  {i}. {item['lesson']} (class: {item.get('failure_class', 'unknown')})")
        return "\n".join(lines)
