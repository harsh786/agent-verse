"""MEM-12: episodic/procedural recall and embed failures are surfaced, never silent.

* A DB recall error raises (warning log with tenant + degraded metric) instead
  of answering from the per-process cache.
* An embed failure (episode write or recall query) is logged, counted and
  reported through ``degraded`` — recall drops to keyword-only visibly.
* The planner records failed memory blocks in ``context["memory_degraded"]``
  and emits a ``memory_degraded`` event.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.memory.episodic import EpisodicMemoryStore, EpisodicMemoryUnavailableError
from app.observability.metrics import MEMORY_DEGRADED_TOTAL
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="deg-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _metric(store: str, op: str) -> float:
    return MEMORY_DEGRADED_TOTAL.labels(store=store, op=op)._value.get()


def _state() -> AgentState:
    st = AgentState(goal="list open Jira tickets", tenant_ctx=T, goal_id="g1")
    st.status = GoalStatus.COMPLETE
    st.steps = [StepResult(description="s", status=StepStatus.COMPLETE)]
    return st


class _BadEmbedder:
    async def embed(self, _req: Any) -> Any:
        raise ConnectionError("embedding provider down")


async def test_episodic_recall_db_failure_raises_and_counts() -> None:
    def _boom() -> None:
        raise RuntimeError("db connection refused")

    store = EpisodicMemoryStore(db_factory=MagicMock(side_effect=_boom))
    before = _metric("episodic", "recall")
    with pytest.raises(EpisodicMemoryUnavailableError):
        await store.recall(goal="jira", tenant_id=T.tenant_id)
    assert _metric("episodic", "recall") == before + 1


async def test_episode_embed_failure_is_logged_and_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = EpisodicMemoryStore(embedder=_BadEmbedder())
    before = _metric("episodic", "embed")
    await store.record(state=_state(), tenant_ctx=T)
    assert _metric("episodic", "embed") == before + 1
    assert store._cache[T.tenant_id][0].embedding is None


async def test_recall_query_embed_failure_reports_degraded() -> None:
    store = EpisodicMemoryStore(embedder=_BadEmbedder())
    await store.record(state=_state(), tenant_ctx=T)
    degraded: list[str] = []
    eps = await store.recall(goal="jira tickets", tenant_id=T.tenant_id, degraded=degraded)
    assert eps  # keyword-only recall still answers
    assert degraded == ["episodic_query_embed_failed"]


async def test_planner_records_memory_degraded_and_emits_event() -> None:
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    class _DownEpisodic:
        async def recall(self, **_kw: object) -> list[object]:
            raise EpisodicMemoryUnavailableError("db down")

    class _DownProcedural:
        async def recall(self, **_kw: object) -> list[object]:
            from app.memory.procedural import ProceduralMemoryUnavailableError

            raise ProceduralMemoryUnavailableError("db down")

    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    graph = AgentGraph(
        planner=FakeProvider(responses=['["Step 1: do it"]']),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        episodic_memory=_DownEpisodic(),
        procedural_memory=_DownProcedural(),
    )
    graph._event_callback = _cb  # type: ignore[assignment]
    st = AgentState(goal="goal", tenant_ctx=T)
    await graph._node_plan({"agent_state": st, "tenant_ctx": T})

    assert set(st.context["memory_degraded"]) >= {"episodic_recall", "procedural_recall"}
    sources = {e.get("source") for e in events if e.get("type") == "memory_degraded"}
    assert {"episodic_recall", "procedural_recall"} <= sources
