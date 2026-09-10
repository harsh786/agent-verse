"""Semantic near-duplicate chunk dedup (pipeline Stage 11).

Exact-hash dedup only catches byte-identical chunks. When a source sets
``near_dup_threshold > 0``, chunks whose embeddings are cosine-similar to an
already-kept chunk are dropped too — removing boilerplate/near-identical
passages before they reach the index. Default (0.0) preserves exact-hash-only
behaviour.
"""
from __future__ import annotations

from app.ingestion.pipeline import IngestionPipeline, _cosine_similarity


def _chunk(text: str, embedding: list[float], content_hash: str = "") -> dict:
    return {"text": text, "embedding": embedding, "content_hash": content_hash or text}


def test_cosine_similarity_basic() -> None:
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert _cosine_similarity([], [1.0]) == 0.0  # degenerate
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vector


def test_disabled_by_default_keeps_near_dupes() -> None:
    pipe = IngestionPipeline()
    chunks = [
        _chunk("The quarterly report is ready.", [1.0, 0.0, 0.0], "h1"),
        _chunk("The quarterly report is prepared.", [0.999, 0.001, 0.0], "h2"),
    ]
    # Default threshold 0.0 → only exact-hash dedup, both distinct hashes kept.
    kept = pipe._dedup_chunks(chunks)
    assert len(kept) == 2


def test_near_dup_dropped_above_threshold() -> None:
    pipe = IngestionPipeline()
    chunks = [
        _chunk("The quarterly report is ready.", [1.0, 0.0, 0.0], "h1"),
        _chunk("The quarterly report is prepared.", [0.999, 0.01, 0.0], "h2"),  # ~1.0 cosine
        _chunk("Unrelated: the cat sat on the mat.", [0.0, 0.0, 1.0], "h3"),  # orthogonal
    ]
    kept = pipe._dedup_chunks(chunks, near_dup_threshold=0.98)
    texts = [c["text"] for c in kept]
    assert "The quarterly report is ready." in texts
    assert "Unrelated: the cat sat on the mat." in texts
    assert "The quarterly report is prepared." not in texts  # dropped as near-dup
    assert len(kept) == 2


def test_exact_hash_dedup_still_applies_with_near_dup_on() -> None:
    pipe = IngestionPipeline()
    chunks = [
        _chunk("same", [1.0, 0.0], "dup"),
        _chunk("same", [1.0, 0.0], "dup"),  # exact hash dup
        _chunk("different", [0.0, 1.0], "other"),
    ]
    kept = pipe._dedup_chunks(chunks, near_dup_threshold=0.99)
    assert len(kept) == 2


def test_chunks_without_embedding_survive_near_dup_pass() -> None:
    pipe = IngestionPipeline()
    chunks = [
        _chunk("a", [], "h1"),  # no embedding
        _chunk("b", [], "h2"),  # no embedding
    ]
    kept = pipe._dedup_chunks(chunks, near_dup_threshold=0.98)
    assert len(kept) == 2  # near-dup pass can't compare them → both kept
