"""T4.3 — salience wired into context value/budgeting."""
from __future__ import annotations

from app.context.rerank_policy import predict_chunk_value
from app.context.salience_enrichment import enrich_with_salience


def test_predict_chunk_value_respects_salience_multiplier() -> None:
    base = {"score": 0.5}
    low = {"score": 0.5, "salience": 0.5}
    high = {"score": 0.5, "salience": 1.5}
    assert predict_chunk_value(high) > predict_chunk_value(base) > predict_chunk_value(low)


def test_predict_chunk_value_unchanged_without_salience() -> None:
    # Back-compat: no salience key → multiplier defaults to 1.0.
    chunk = {"score": 0.8}
    v_no_key = predict_chunk_value(chunk)
    v_one = predict_chunk_value({**chunk, "salience": 1.0})
    assert v_no_key == v_one


def test_enrich_sets_higher_salience_for_relevant_chunk() -> None:
    chunks = [
        {"content": "python machine learning tutorial with scikit-learn", "score": 0.5},
        {"content": "weather forecast shows rain tomorrow", "score": 0.5},
    ]
    enriched = enrich_with_salience(chunks, query="python machine learning")
    sal = {c["content"].split()[0]: c["salience"] for c in enriched}
    assert sal["python"] > sal["weather"]
    # originals not mutated
    assert "salience" not in chunks[0]


def test_enrich_then_value_reorders_toward_relevant() -> None:
    chunks = [
        {"content": "weather forecast shows rain tomorrow", "score": 0.6},
        {"content": "python machine learning tutorial", "score": 0.5},
    ]
    enriched = enrich_with_salience(chunks, query="python machine learning")
    ranked = sorted(enriched, key=predict_chunk_value, reverse=True)
    # Despite a lower raw score, the salient chunk should now lead.
    assert "python" in ranked[0]["content"]


def test_enrich_empty_is_empty() -> None:
    assert enrich_with_salience([], "q") == []
