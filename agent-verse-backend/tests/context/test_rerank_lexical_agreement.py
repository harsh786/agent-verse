"""OI-5: the cross-encoder blend no longer buries exact-identifier / record matches.

After P1c-3 / -4 a one-word proper-noun query still ranked the only lexical match
3rd, behind documents the cross-encoder preferred. Root cause: the cross-encoder's
raw logits were min-max normalised over the candidates, so when it could not tell
the candidates apart (every logit ~ -10, or every structured record ~ +6) its
noise was stretched to a full 0..1 span and, at weight 0.6, outvoted the
retrieval evidence (every lexical leg + P2-3's exact-match leg agreeing on one
chunk). Now the logits are read as probabilities (sigmoid): an indiscriminate
cross-encoder adds the same to every chunk and the retrieval order decides; a
confident one still reorders. An exact identifier of the query (``RTO-5531``) in
a chunk ranks it ahead of chunks without it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.context.rerank_policy import RerankPolicy, RerankStrategy


def _records(n: int) -> list[str]:
    return [
        f"_id: 66f1a{i}\norder_no: ORD-{i:04d}\nstatus: shipped\nitems: widget x2\n"
        f"notes: {'customer asked for RTO-5531 reference' if i == 5 else 'leave at door'}"
        for i in range(n)
    ]


def _rerank(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[dict[str, Any]],
    query: str,
    ce: dict[str, float],
) -> list[str]:
    def _fake(query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        return [ce[d] for d in documents]

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _fake)
    out = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER)._cross_encoder_rerank(chunks, query)
    return [c["chunk_id"] for c in out]


def test_indiscriminate_logits_on_records_do_not_bury_the_identifier_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records(8)
    # Retrieval: the exact match (phrase + fts + bm25) leads by far.
    chunks = [
        {"chunk_id": f"r{i}", "content": c, "score": 0.049 if i == 5 else 0.015 + i * 0.0002}
        for i, c in enumerate(records)
    ]
    # Real ms-marco logits on such records: all ~5.4-6.3, the target NOT highest.
    logits = [5.81, 5.91, 6.27, 5.65, 5.64, 5.36, 5.39, 5.42]
    order = _rerank(monkeypatch, chunks, "RTO-5531", dict(zip(records, logits, strict=True)))
    assert order[0] == "r5", order


def test_negative_noise_logits_leave_the_lexical_agreement_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live 'Hosur' shape: the cross-encoder scores every chunk as irrelevant
    (logits around -10) and its slight preferences are noise."""
    docs = [f"order {i}: standard surface shipping via the hub" for i in range(9)]
    docs.insert(3, "order 99: customer asked to reroute to the Hosur centre")
    chunks = [
        {"chunk_id": f"c{i}", "content": d, "score": 0.0489 if "Hosur" in d else 0.015}
        for i, d in enumerate(docs)
    ]
    logits = {d: -9.0 + 0.1 * i for i, d in enumerate(docs)}
    logits[docs[3]] = -10.4  # the CE likes the target least
    order = _rerank(monkeypatch, chunks, "Hosur", logits)
    assert order[0] == "c3", order


def test_a_confident_cross_encoder_still_reorders(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = [
        "the canteen opens at six",
        "Orchid-9 gate passes replace paper at Hosur",
        "gate passes are paper",
    ]
    # Retrieval mildly prefers a weak lexical match; the CE is sure about doc 1.
    chunks = [
        {"chunk_id": "a", "content": docs[0], "score": 0.016},
        {"chunk_id": "b", "content": docs[1], "score": 0.015},
        {"chunk_id": "c", "content": docs[2], "score": 0.017},
    ]
    order = _rerank(
        monkeypatch,
        chunks,
        "which yard replaces paper passes?",
        {docs[0]: -11.0, docs[1]: 9.3, docs[2]: -5.7},
    )
    assert order[0] == "b", order


def test_exact_identifier_outranks_a_cross_encoder_favourite_without_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = [
        "Receipt TJ-5534 towage, paid INR 7,86,000",
        "Receipt TJ-5531 towage, paid INR 1,86,000",
    ]
    chunks = [
        {"chunk_id": "wrong", "content": docs[0], "score": 0.03},
        {"chunk_id": "right", "content": docs[1], "score": 0.02},
    ]
    order = _rerank(monkeypatch, chunks, "TJ-5531", {docs[0]: 7.0, docs[1]: 2.0})
    assert order == ["right", "wrong"]


def test_identifier_must_match_whole_not_as_a_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = ["job TJ-55310 closed", "job notes without a code"]
    chunks = [
        {"chunk_id": "prefix", "content": docs[0], "score": 0.015},
        {"chunk_id": "other", "content": docs[1], "score": 0.03},
    ]
    order = _rerank(monkeypatch, chunks, "TJ-5531", {docs[0]: 0.0, docs[1]: 0.0})
    assert order[0] == "other", order


def test_probability_scale_fallback_scores_are_used_as_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TF-IDF fallback scores are already 0..1 and are not squashed again."""
    docs = ["alpha beta", "gamma delta"]
    chunks = [
        {"chunk_id": "x", "content": docs[0], "score": 0.015},
        {"chunk_id": "y", "content": docs[1], "score": 0.016},
    ]

    def _fake(query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        return [0.9 if d == docs[0] else 0.0 for d in documents]

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _fake)
    out = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER)._cross_encoder_rerank(chunks, "alpha")
    assert out[0]["chunk_id"] == "x"
    assert out[0]["ce_score"] == pytest.approx(0.9)
