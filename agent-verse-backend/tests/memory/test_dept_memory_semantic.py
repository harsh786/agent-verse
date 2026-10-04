"""MEM-42: department memory recall is semantic (blended with the lexical score).

Retrieval was keyword-substring scoring only, so a paraphrased SOP or decision
("money back" for "refund") was never found.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.memory.dept_memory import DepartmentMemory

T = "dm-sem-tenant"

# Concept axes: paraphrases land on the same axis.
_AXES = {
    "refund": 1, "money back": 1, "reimburse": 1,
    "deploy": 2, "release": 2, "ship": 2,
    "hiring": 3, "recruit": 3,
}


class FakeEmbedder:
    model_id = "fake-concepts-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        vec = [0.0] * 2048
        lowered = text.lower()
        for phrase, axis in _AXES.items():
            if phrase in lowered:
                vec[axis] = 1.0
        vec[0] = 0.05  # never all-zero
        return tuple(vec)


async def _allow(*, content: str, **_kw: Any) -> dict[str, Any]:
    return {"blocked": False}


@pytest.fixture(autouse=True)
def _guardrail() -> Any:
    with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_allow):
        yield


async def _seed(mem: DepartmentMemory) -> None:
    await mem.add("support", "o1", T, "Refunds over $500 need finance approval", "ops")
    await mem.add("support", "o1", T, "Release notes go out every Friday", "ops")
    await mem.add("support", "o1", T, "Hiring freeze until Q3", "ops")


async def test_paraphrased_query_recalls_the_matching_sop() -> None:
    mem = DepartmentMemory()
    embedder = FakeEmbedder()
    mem.set_embedder(embedder)
    await _seed(mem)
    hits = await mem.retrieve(
        "support", "how do customers get their money back", top_k=1, tenant_id=T
    )
    assert [h.content for h in hits] == ["Refunds over $500 need finance approval"]
    # Entries were embedded once, on add (not per query).
    assert len(embedder.calls) == 3 + 1


async def test_lexical_ranking_still_works_without_an_embedder() -> None:
    mem = DepartmentMemory()
    await _seed(mem)
    hits = await mem.retrieve("support", "hiring freeze", top_k=1, tenant_id=T)
    assert [h.content for h in hits] == ["Hiring freeze until Q3"]


async def test_a_per_call_embedder_is_used_when_the_store_has_none() -> None:
    mem = DepartmentMemory()
    embedder = FakeEmbedder()
    mem.set_embedder(embedder)
    await _seed(mem)
    mem.set_embedder(None)
    hits = await mem.retrieve(
        "support", "when do we ship", top_k=1, tenant_id=T, embedder=embedder
    )
    assert [h.content for h in hits] == ["Release notes go out every Friday"]


async def test_embedding_failure_stores_the_entry_lexical_only() -> None:
    class Broken(FakeEmbedder):
        async def __call__(self, text: str) -> tuple[float, ...]:
            raise RuntimeError("embedding provider down")

    mem = DepartmentMemory()
    mem.set_embedder(Broken())
    entry = await mem.add("support", "o1", T, "Refunds over $500 need approval", "ops")
    assert entry.embedding is None
    hits = await mem.retrieve("support", "refunds", top_k=1, tenant_id=T)
    assert hits and hits[0].entry_id == entry.entry_id


def test_sql_ranking_blends_cosine_with_the_lexical_score() -> None:
    from app.memory.dept_memory import _ranked_select_sql

    sql = " ".join(_ranked_select_sql(scope="dept_id = :did", clause="", n_keywords=2,
                                      semantic=True, exact=False).split())
    assert "embedding::halfvec(2048) <=> CAST(:qvec AS halfvec(2048))" in sql
    assert "embedding_model = :qmodel" in sql
    assert "tenant_id = :tid" in sql
    lexical = " ".join(_ranked_select_sql(scope="dept_id = :did", clause="", n_keywords=2,
                                          semantic=False, exact=False).split())
    assert "halfvec" not in lexical
