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


def test_blend_uses_the_retrieval_rank_on_the_same_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1c-4: ``0.6 * ce + 0.4 * retrieval`` mixed a 0..1 cross-encoder score with a raw
    RRF score (~0.016 .. 0.05), so the retrieval evidence never mattered: live, the one
    chunk matching "Hosur" in the vector, full-text AND BM25 legs (RRF 0.049, every
    other chunk ~0.015) ranked 9th of 10. Both scores are now normalised over the
    candidate set before blending."""

    # The live numbers (top 10 of the widened pool + its lowest member): normalised
    # cross-encoder score and RRF score per chunk.
    live = [(1.0, 0.0156), (0.995, 0.0159), (0.927, 0.0143), (0.747, 0.0135),
            (0.519, 0.0149), (0.475, 0.0133), (0.429, 0.0139), (0.421, 0.0137),
            (0.316, 0.0147), (0.0, 0.0130)]
    chunks = [{"chunk_id": f"c{i}", "content": f"order {i} standard shipping", "score": rrf}
              for i, (_ce, rrf) in enumerate(live)]
    chunks.insert(8, {"chunk_id": "target", "content": "order reroute to Hosur",
                      "score": 0.0489})
    ce = {f"order {i} standard shipping": ce for i, (ce, _rrf) in enumerate(live)}
    ce["order reroute to Hosur"] = 0.317

    def _fake_cross_encode(query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        return [ce[d] for d in documents]

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _fake_cross_encode)
    out = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER)._cross_encoder_rerank(
        chunks, "Hosur"
    )

    rank = [c["chunk_id"] for c in out].index("target") + 1
    assert rank <= 3, [c["chunk_id"] for c in out]
    # Still mostly a cross-encoder ranking: the CE's top choices stay on top.
    assert {c["chunk_id"] for c in out[:2]} == {"c0", "c1"} or rank == 1
