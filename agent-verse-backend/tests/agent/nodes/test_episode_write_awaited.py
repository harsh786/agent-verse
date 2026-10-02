"""MEM-41: the episode write is awaited, not a cancellable fire-and-forget task.

The worker's fresh event loop is torn down (pending tasks cancelled) right after
the graph returns, so a background episode write with a slow embedder was lost
silently. The verify node now awaits it and flags a lost write.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.memory.episodic import EpisodicMemoryStore
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb

T = TenantContext(tenant_id="t-ep", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _SlowEmbedder:
    async def embed(self, _req: Any) -> Any:
        await asyncio.sleep(0.2)
        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]])


def _graph(store: EpisodicMemoryStore) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["step 1"]),
        executor=FakeProvider(responses=["out"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "done"}']),
        episodic_memory=store,
    )


def _state() -> dict[str, Any]:
    agent_state = AgentState(goal="list open tickets", tenant_ctx=T, goal_id="g-ep")
    agent_state.steps.append(
        StepResult(description="search", output="5 tickets", status=StepStatus.COMPLETE)
    )
    return {"agent_state": agent_state, "tenant_ctx": T}


async def test_episode_insert_happened_when_verify_returns() -> None:
    db = RlsRecordingDb()
    store = EpisodicMemoryStore(db_factory=db, embedder=_SlowEmbedder())
    await _graph(store)._node_verify(_state())
    # No draining of background tasks: the INSERT already ran.
    assert len(db.touching("INSERT INTO episodic_memories")) == 1


async def test_lost_episode_write_marks_memory_degraded() -> None:
    class _BrokenDb:
        def __call__(self) -> Any:
            raise ConnectionError("db down")

    store = EpisodicMemoryStore(db_factory=_BrokenDb())
    state = _state()
    await _graph(store)._node_verify(state)
    assert "episodic_record" in state["agent_state"].context.get("memory_degraded", [])
