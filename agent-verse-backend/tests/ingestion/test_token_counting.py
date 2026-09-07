"""ING-9: chunk sizing must use the real tokenizer, not char//4 heuristics."""

from __future__ import annotations

from app.agent.tokenizer import count_tokens
from app.ingestion.chunkers.semantic import SemanticChunker


def test_semantic_chunker_respects_real_token_budget():
    """FAILS TODAY: char-budget (tokens*4) under-counts token-dense text >2x."""
    para = "x = (a+b)*c - d/e; y = {k: v**2 for k in range(10)}  # dense code line"
    content = "\n\n".join([para] * 6)
    max_tokens = 40

    chunks = SemanticChunker(max_chunk_tokens=max_tokens).chunk(content)

    # A single paragraph is under budget, so packing must never exceed it.
    assert count_tokens(para) <= max_tokens, "test precondition: one paragraph fits"
    for c in chunks:
        assert count_tokens(c.content) <= max_tokens, (
            f"chunk has {count_tokens(c.content)} real tokens > budget {max_tokens} "
            "(char-budget packing under-counted tokens)"
        )
