"""Hallucination T2/T3 — per-claim scoring + GroundingPolicy abstention."""
from __future__ import annotations

from app.agent.grounding_policy import GroundingPolicy


async def test_exact_tier_grounds_and_scores_per_claim() -> None:
    policy = GroundingPolicy(min_grounded_ratio=1.0)
    res = await policy.evaluate(
        "Closed JIRA-101 and 7 tickets",
        ["We closed JIRA-101; 7 tickets done"],
    )
    assert res.grounded is True
    assert res.grounded_ratio == 1.0
    assert all(v.tier == "exact" for v in res.verdicts)
    assert res.abstain == []


async def test_numbers_require_exact_even_with_embedder() -> None:
    # A wrong number must abstain regardless of embeddings.
    async def _embed(texts):
        return [[1.0, 0.0] for _ in texts]  # everything "similar"

    policy = GroundingPolicy(
        min_grounded_ratio=1.0, embed_fn=_embed, numeric_claims_require_exact=True
    )
    res = await policy.evaluate("Found 50 issues", ["There are 5 issues"])
    assert res.grounded is False
    assert "50" in res.abstain


async def test_embedding_tier_grounds_paraphrase_name() -> None:
    # Claim "Acme Corporation" not literally present but paraphrased; embedder
    # returns identical vectors for the claim and the matching span.
    async def _embed(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]  # all identical → cosine 1.0

    policy = GroundingPolicy(min_grounded_ratio=1.0, embed_fn=_embed, embed_threshold=0.8)
    res = await policy.evaluate(
        'The vendor is "Acme Corporation".',
        ["The supplier we selected is Acme."],
    )
    # quoted claim grounds via embedding tier
    quoted = [v for v in res.verdicts if v.value == "Acme Corporation"]
    assert quoted and quoted[0].grounded is True
    assert quoted[0].tier == "embedding"


async def test_ratio_threshold_and_abstain_list() -> None:
    policy = GroundingPolicy(min_grounded_ratio=0.75)
    # 1 of 2 grounded → ratio 0.5 < 0.75 → not grounded
    res = await policy.evaluate(
        "JIRA-101 and JIRA-999 are open",
        ["Only JIRA-101 is open"],
    )
    assert res.grounded_ratio == 0.5
    assert res.grounded is False
    assert "JIRA-999" in res.abstain


async def test_no_claims_is_grounded() -> None:
    res = await GroundingPolicy().evaluate("All done, looks good.", ["irrelevant"])
    assert res.grounded is True
    assert res.grounded_ratio == 1.0
