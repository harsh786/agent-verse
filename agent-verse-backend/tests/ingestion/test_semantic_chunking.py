"""ING-10: embedding-boundary semantic chunking (opt-in), greedy fallback default."""

from __future__ import annotations

from app.ingestion.chunkers.semantic import SemanticChunker


def _topic_embed(sentences: list[str]) -> list[list[float]]:
    """Deterministic embedder: encode topic by a keyword so a topic shift is a
    similarity drop. 'alpha' -> [1,0], 'beta' -> [0,1]."""
    out: list[list[float]] = []
    for s in sentences:
        low = s.lower()
        out.append([1.0, 0.0] if "alpha" in low else [0.0, 1.0])
    return out


def test_boundary_at_topic_shift():
    """FAILS TODAY: SemanticChunker packs by size only; no topic-aware boundary."""
    content = (
        "Alpha one is here. Alpha two follows. Alpha three as well. "
        "Beta one begins now. Beta two continues. Beta three ends it."
    )
    chunker = SemanticChunker(max_chunk_tokens=1000, embed_fn=_topic_embed)
    chunks = chunker.chunk(content)

    # The topic shift (alpha -> beta) must create exactly one boundary,
    # even though the whole text is well under the token budget.
    assert len(chunks) == 2, f"expected a boundary at the topic shift, got {len(chunks)}"
    assert "alpha" in chunks[0].content.lower() and "beta" not in chunks[0].content.lower()
    assert "beta" in chunks[1].content.lower() and "alpha" not in chunks[1].content.lower()


def test_default_no_embedder_is_greedy_single_chunk():
    """Without an embedder the small text stays one greedy chunk (unchanged default)."""
    content = "Alpha one. Beta one."
    chunks = SemanticChunker(max_chunk_tokens=1000).chunk(content)
    assert len(chunks) == 1


def test_embed_failure_falls_back_to_greedy():
    """An embed error must degrade to greedy packing, never crash."""

    def _boom(_sentences: list[str]) -> list[list[float]]:
        raise RuntimeError("embed down")

    content = "Alpha one. Beta one."
    chunks = SemanticChunker(max_chunk_tokens=1000, embed_fn=_boom).chunk(content)
    assert len(chunks) >= 1  # fell back, produced chunks
