"""Tests for the hallucination-handling pipeline made live (D-4 / D-5).

Covers:
- NLIChecker deterministic (no-LLM) heuristic verdicts with real discrimination.
- ClaimDecomposer no-provider verification path.
- AttributionVerifier flagging citations that do not support their claim.
- verify_grounding() composing all three into a single safe_to_emit verdict.

Every test proves *discrimination* (supported vs unsupported vs contradicted),
not just that a code path runs.
"""

from __future__ import annotations

import pytest

from app.evals.attribution_verifier import AttributionVerifier
from app.intelligence.claim_decomposer import ClaimDecomposer
from app.intelligence.grounding_verification import GroundingVerdict, verify_grounding
from app.intelligence.nli_checker import NLIChecker

# ---------------------------------------------------------------------------
# NLIChecker deterministic heuristic (no LLM)
# ---------------------------------------------------------------------------


class TestNLIHeuristic:
    def test_entails_when_claim_covered_by_evidence(self) -> None:
        checker = NLIChecker()
        result = checker.check_consistency_sync(
            "Paris is the capital of France",
            "Paris is the capital city of France and its most populous city.",
        )
        assert result.verdict == "ENTAILS"

    def test_neutral_when_claim_unrelated(self) -> None:
        checker = NLIChecker()
        result = checker.check_consistency_sync(
            "The Eiffel Tower is ten thousand meters tall",
            "Paris is the capital of France.",
        )
        assert result.verdict == "NEUTRAL"

    def test_contradicts_on_negation_mismatch(self) -> None:
        checker = NLIChecker()
        result = checker.check_consistency_sync(
            "Paris is not located in France",
            "Paris is located in France.",
        )
        assert result.verdict == "CONTRADICTS"

    def test_contradicts_on_number_mismatch(self) -> None:
        checker = NLIChecker()
        result = checker.check_consistency_sync(
            "The tower is 324 meters tall",
            "The tower is 300 meters tall.",
        )
        assert result.verdict == "CONTRADICTS"

    @pytest.mark.asyncio
    async def test_check_consistency_falls_back_to_heuristic_without_provider(self) -> None:
        checker = NLIChecker()
        result = await checker.check_consistency(
            "Paris is the capital of France",
            "Paris is the capital of France.",
            provider=None,
        )
        assert result.verdict == "ENTAILS"


# ---------------------------------------------------------------------------
# ClaimDecomposer no-provider verification path
# ---------------------------------------------------------------------------


class TestClaimDecomposerNoProvider:
    @pytest.mark.asyncio
    async def test_verify_claims_no_provider_all_supported(self) -> None:
        decomposer = ClaimDecomposer()
        report = await decomposer.verify_claims(
            claims=["Paris is the capital of France"],
            evidence_chunks=["Paris is the capital of France."],
        )
        assert report.verdicts == ["ENTAILS"]
        assert report.overall_score == pytest.approx(1.0)
        assert report.unsupported_claims == []
        assert report.contradicted_claims == []

    @pytest.mark.asyncio
    async def test_verify_claims_no_provider_flags_contradiction(self) -> None:
        decomposer = ClaimDecomposer()
        report = await decomposer.verify_claims(
            claims=["Paris is not located in France"],
            evidence_chunks=["Paris is located in France."],
        )
        assert report.contradicted_claims == ["Paris is not located in France"]
        assert report.overall_score == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_check_answer_no_provider(self) -> None:
        decomposer = ClaimDecomposer()
        report = await decomposer.check_answer(
            answer="Paris is the capital of France. The Seine flows through Paris.",
            evidence_chunks=[
                "Paris is the capital of France.",
                "The Seine river flows through Paris.",
            ],
        )
        assert report.overall_score == pytest.approx(1.0)
        assert not report.unsupported_claims
        assert not report.contradicted_claims


# ---------------------------------------------------------------------------
# AttributionVerifier discrimination
# ---------------------------------------------------------------------------


class TestAttributionDiscrimination:
    def test_flags_citation_that_does_not_support_claim(self) -> None:
        verifier = AttributionVerifier(jaccard_threshold=0.15)
        answer = "Bananas are a yellow tropical fruit rich in potassium [1]."
        chunks = ["The stock market declined sharply amid recession fears."]
        report = verifier.verify(answer, chunks)
        assert report.failed_count == 1
        assert report.verified_count == 0
        assert report.precision_score == pytest.approx(0.0)

    def test_verifies_citation_that_supports_claim(self) -> None:
        verifier = AttributionVerifier(jaccard_threshold=0.15)
        answer = "Bananas are a yellow tropical fruit rich in potassium [1]."
        chunks = ["Bananas are a yellow tropical fruit that is rich in potassium."]
        report = verifier.verify(answer, chunks)
        assert report.verified_count == 1
        assert report.failed_count == 0
        assert report.precision_score == pytest.approx(1.0)

    def test_duplicate_citation_counted_once(self) -> None:
        verifier = AttributionVerifier(jaccard_threshold=0.15)
        answer = "Bananas are yellow fruit [1]. Bananas contain potassium [1]."
        chunks = ["Bananas are a yellow fruit rich in potassium."]
        report = verifier.verify(answer, chunks)
        # [1] appears twice but must be verified once.
        assert report.verified_count + report.failed_count == 1


# ---------------------------------------------------------------------------
# verify_grounding — the composed, reusable entrypoint
# ---------------------------------------------------------------------------


class TestVerifyGrounding:
    @pytest.mark.asyncio
    async def test_all_supported_is_safe_to_emit(self) -> None:
        verdict = await verify_grounding(
            answer="Paris is the capital of France [1]. The Seine flows through Paris [2].",
            evidence_chunks=[
                "Paris is the capital of France.",
                "The Seine river flows through Paris.",
            ],
        )
        assert isinstance(verdict, GroundingVerdict)
        assert verdict.safe_to_emit is True
        assert verdict.contradicted_claims == []
        assert verdict.unsupported_claims == []
        assert verdict.claim_score == pytest.approx(1.0)
        assert verdict.attribution_score == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_fabricated_claim_is_not_safe(self) -> None:
        verdict = await verify_grounding(
            answer=(
                "Paris is the capital of France. "
                "The Eiffel Tower is ten thousand meters tall and made of solid gold."
            ),
            evidence_chunks=["Paris is the capital of France."],
        )
        assert verdict.safe_to_emit is False
        assert any("gold" in c.lower() or "meters" in c.lower() for c in verdict.unsupported_claims)

    @pytest.mark.asyncio
    async def test_contradicted_claim_is_not_safe(self) -> None:
        verdict = await verify_grounding(
            answer="Paris is not located in France.",
            evidence_chunks=["Paris is located in France."],
        )
        assert verdict.safe_to_emit is False
        assert verdict.contradicted_claims

    @pytest.mark.asyncio
    async def test_bad_citation_drags_down_attribution(self) -> None:
        # The claim is factually fine against evidence chunk 0, but it cites [2]
        # (chunk index 1), which is about something else entirely.
        verdict = await verify_grounding(
            answer="Bananas are a yellow tropical fruit rich in potassium [2].",
            evidence_chunks=[
                "Bananas are a yellow tropical fruit rich in potassium.",
                "The stock market declined sharply amid recession fears.",
            ],
        )
        assert verdict.attribution_score == pytest.approx(0.0)
        assert verdict.safe_to_emit is False

    @pytest.mark.asyncio
    async def test_deterministic_no_llm_path(self) -> None:
        # Runs entirely without a provider — proves the heuristic fallback works.
        good = await verify_grounding(
            answer="Water boils at 100 degrees celsius at sea level.",
            evidence_chunks=["At sea level, water boils at 100 degrees celsius."],
            provider=None,
        )
        bad = await verify_grounding(
            answer="Water boils at 500 degrees celsius at sea level.",
            evidence_chunks=["At sea level, water boils at 100 degrees celsius."],
            provider=None,
        )
        assert good.safe_to_emit is True
        assert bad.safe_to_emit is False
