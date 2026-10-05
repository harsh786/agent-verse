"""P1c-3: the cross-encoder reranker sees the whole chunk, not its first 512 characters.

``RerankPolicy._cross_encoder_rerank`` cut every chunk to ``content[:512]`` before
scoring. The model's own limit is 512 *tokens* (~2,000 characters), so the cut threw
away most of a normal 512-token chunk. Live (SRC-MONGO-SYNC): a MongoDB order is
rendered as ``key: value`` lines with its ``notes`` field last; the cross-encoder
only saw the ``_id`` / dates / line items every order shares, and the one chunk that
matched "Hosur" in both lexical legs was reranked out of the top 10 of 20 orders.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.context.rerank_policy import RerankPolicy, RerankStrategy

_FILLER = "\n".join(f"lines[{i}].sku: SKU-{i:04d}\nlines[{i}].qty: {i % 4 + 1}" for i in range(12))


def _chunk(cid: str, notes: str) -> dict[str, Any]:
    content = f"_id: {cid}\norder_no: ORD-{cid}\nstatus: delivered\n{_FILLER}\nnotes: {notes}"
    assert len(content) > 512  # the fact sits past the old cut
    return {"chunk_id": cid, "content": content, "score": 0.016}


def test_cross_encoder_scores_text_beyond_512_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def _fake_cross_encode(query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        seen.append(documents)
        return [10.0 if "Hosur" in d else 0.0 for d in documents]

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _fake_cross_encode)
    chunks = [_chunk(f"{i:02d}", "standard surface shipping via the hub") for i in range(5)]
    chunks.insert(3, _chunk("target", "Customer asked to reroute to the Hosur centre"))

    out = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER)._cross_encoder_rerank(
        chunks, "reroute Hosur"
    )

    assert seen and all("notes:" in d for d in seen[0])
    assert out[0]["chunk_id"] == "target"


def test_cross_encoder_input_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pathological chunk is still capped (the model truncates at 512 tokens)."""
    seen: list[list[str]] = []

    def _fake_cross_encode(query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        seen.append(documents)
        return [0.0 for _ in documents]

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _fake_cross_encode)
    RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER)._cross_encoder_rerank(
        [{"chunk_id": "x", "content": "a" * 100_000, "score": 0.1}], "q"
    )
    assert 2000 <= len(seen[0][0]) <= 8192

