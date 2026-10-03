"""Episodic recall must use the stored embedding, not only keyword overlap.

Regression: ``record()`` embedded every goal and persisted the vector
(``episodic_memories.embedding`` JSONB), but ``recall()`` ranked purely by
keyword overlap — so "ship the billing microservice" never recalled "deploy
the payment service" (no shared words), however similar the embeddings.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.state import AgentState, GoalStatus
from app.memory.episodic import EpisodicMemoryStore
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-sem", plan=PlanTier.PROFESSIONAL, api_key_id="k")

# Toy semantic space: deploy/ship-ish texts point one way, baking another.
_VECTORS = {
    "deploy the payment service": [1.0, 0.0, 0.1],
    "ship the billing microservice": [0.95, 0.05, 0.1],
    "bake a chocolate cake for the billing party": [0.0, 1.0, 0.0],
}


class _Embedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, req: Any) -> Any:
        self.calls += 1
        return MagicMock(embeddings=[_VECTORS[t] for t in req.texts])


def _state(goal: str) -> AgentState:
    st = AgentState(goal=goal, tenant_ctx=_CTX, goal_id="g")
    st.status = GoalStatus.COMPLETE
    return st


@pytest.mark.asyncio
async def test_in_memory_recall_ranks_by_embedding_similarity() -> None:
    store = EpisodicMemoryStore(db_factory=None, embedder=_Embedder())
    await store.record(state=_state("bake a chocolate cake for the billing party"), tenant_ctx=_CTX)
    await store.record(state=_state("deploy the payment service"), tenant_ctx=_CTX)

    # Keyword overlap favours the cake ("billing"); semantics favour deploy.
    eps = await store.recall(goal="ship the billing microservice", tenant_id=_CTX.tenant_id)
    assert eps[0].goal_text == "deploy the payment service"


def _db_with_rows(rows: list[tuple[Any, ...]]) -> Any:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    session.execute = AsyncMock(return_value=result)
    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=begin)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)
    return MagicMock(return_value=session), session


@pytest.mark.asyncio
async def test_db_recall_ranks_by_stored_embedding() -> None:
    # Column 9 is the cosine similarity Postgres computed (MEM-40: HNSW query).
    rows = [
        # keyword-favoured, semantically far; high quality
        (
            "e-cake", "g1", "bake a chocolate cake for the billing party", "s", "success", "",
            0.9, 1, "[]", 0.12,
        ),
        # no shared keywords, semantically close; low quality
        (
            "e-deploy", "g2", "deploy the payment service", "s", "success", "",
            0.3, 1, "[]", 0.97,
        ),
    ]
    factory, session = _db_with_rows(rows)
    store = EpisodicMemoryStore(db_factory=factory, embedder=_Embedder())
    eps = await store.recall(goal="ship the billing microservice", tenant_id=_CTX.tenant_id)
    assert eps[0].episode_id == "e-deploy"
    sqls = [str(c.args[0]) for c in session.execute.call_args_list]
    assert any("FROM episodic_memories" in s and "embedding_vec" in s for s in sqls)


@pytest.mark.asyncio
async def test_db_recall_without_embedder_keeps_keyword_ranking() -> None:
    rows = [
        ("e1", "g1", "unrelated email", "s", "success", "", 0.9, 1, "[]", None),
        ("e2", "g2", "jira ticket search", "s", "success", "", 0.4, 1, "[]", None),
    ]
    factory, _ = _db_with_rows(rows)
    eps = await EpisodicMemoryStore(db_factory=factory).recall(
        goal="jira ticket search", tenant_id=_CTX.tenant_id
    )
    assert eps[0].episode_id == "e2"
