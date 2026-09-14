"""Salience-ranked working memory (opt-in): eviction and recall by importance.

Default WorkingMemory stays FIFO (see test_working_memory.py). When a
SalienceScorer is supplied, the buffer keeps the most *salient* items under
capacity pressure and can recall them ranked, so a bounded prompt block holds
the items most relevant to the run's focus rather than merely the newest.
"""
from __future__ import annotations

from app.memory.salience import SalienceScorer
from app.memory.working_memory import WorkingMemory


def _wm() -> WorkingMemory:
    return WorkingMemory(
        capacity=3,
        scorer=SalienceScorer(),
        focus="python machine learning",
    )


def test_salience_eviction_keeps_the_relevant_item() -> None:
    wm = _wm()
    # The relevant item is pushed FIRST (oldest) — pure FIFO would evict it.
    wm.push("python machine learning tutorial with scikit-learn", source="rag")
    wm.push("weather forecast shows rain tomorrow", source="tool")
    wm.push("local sports scores from last night", source="tool")
    wm.push("a cooking recipe for pasta carbonara", source="tool")  # overflow → evict

    contents = [i.content for i in wm.snapshot()]
    assert len(wm) == 3
    # Salience eviction must drop a low-relevance item, NOT the relevant one.
    assert any("python machine learning" in c for c in contents), (
        f"relevant item was wrongly evicted; kept: {contents}"
    )


def test_most_salient_ranks_by_relevance_to_query() -> None:
    wm = _wm()
    wm.push("python machine learning tutorial with scikit-learn", source="rag")
    wm.push("weather forecast shows rain tomorrow", source="tool")
    top = wm.most_salient(1, query="python machine learning")
    assert len(top) == 1
    assert "python machine learning" in top[0].content


def test_default_working_memory_is_still_fifo() -> None:
    # No scorer → unchanged FIFO contract (regression guard).
    wm = WorkingMemory(capacity=2)
    wm.push("first")
    wm.push("second")
    wm.push("third")  # evicts "first" (FIFO)
    contents = [i.content for i in wm.snapshot()]
    assert contents == ["second", "third"]
    # most_salient without a scorer falls back to recency order (newest first).
    assert wm.most_salient(1)[0].content == "third"
