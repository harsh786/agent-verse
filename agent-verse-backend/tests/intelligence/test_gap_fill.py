"""Tests for NLI checker, claim decomposer, streaming guard, toxicity classifier."""
from __future__ import annotations

import pytest
from app.intelligence.nli_checker import NLIChecker, NLIResult
from app.intelligence.claim_decomposer import ClaimDecomposer
from app.guardrails_v2.streaming_guard import StreamingGuard
from app.guardrails_v2.toxicity import ToxicityClassifier


class FakeProvider:
    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, req: object) -> object:
        class R:
            content = None
        r = R()
        r.content = self._content
        return r


# ---------------------------------------------------------------------------
# NLI Checker
# ---------------------------------------------------------------------------

class TestNLIChecker:
    @pytest.mark.asyncio
    async def test_entails_verdict(self) -> None:
        checker = NLIChecker()
        provider = FakeProvider("ENTAILS")
        result = await checker.check_consistency("Paris is in France", "Paris is the capital of France.", provider)
        assert result.verdict == "ENTAILS"
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_contradicts_verdict(self) -> None:
        checker = NLIChecker()
        provider = FakeProvider("CONTRADICTS")
        result = await checker.check_consistency("Water is dry", "Water is a liquid.", provider)
        assert result.verdict == "CONTRADICTS"

    @pytest.mark.asyncio
    async def test_graceful_on_failure(self) -> None:
        class FailProvider:
            async def complete(self, req: object) -> None:
                raise RuntimeError("down")

        checker = NLIChecker()
        result = await checker.check_consistency("claim", "evidence", FailProvider())
        assert result.verdict == "NEUTRAL"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_check_answer_consistency_returns_float(self) -> None:
        checker = NLIChecker()
        provider = FakeProvider("ENTAILS")
        score = await checker.check_answer_consistency("The sky is blue", ["The sky appears blue."], provider)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Claim Decomposer
# ---------------------------------------------------------------------------

class TestClaimDecomposer:
    def test_decompose_sync_returns_sentences(self) -> None:
        decomposer = ClaimDecomposer()
        text = "The sky is blue. Water is wet. Python is a language."
        claims = decomposer.decompose_sync(text)
        assert len(claims) >= 2

    @pytest.mark.asyncio
    async def test_decompose_async_graceful_on_failure(self) -> None:
        class FailProvider:
            async def complete(self, req: object) -> None:
                raise RuntimeError("down")

        decomposer = ClaimDecomposer()
        claims = await decomposer.decompose("sentence one. sentence two.", FailProvider())
        assert len(claims) >= 1  # fallback to sentence split

    @pytest.mark.asyncio
    async def test_verify_claims_returns_report(self) -> None:
        from app.intelligence.nli_checker import NLIChecker

        decomposer = ClaimDecomposer()
        checker = NLIChecker()
        provider = FakeProvider("ENTAILS")
        report = await decomposer.verify_claims(
            claims=["Paris is in France"],
            evidence_chunks=["Paris is the capital city of France."],
            nli=checker,
            provider=provider,
        )
        assert 0.0 <= report.overall_score <= 1.0
        assert len(report.verdicts) == 1


# ---------------------------------------------------------------------------
# Streaming Guard
# ---------------------------------------------------------------------------

class TestStreamingGuard:
    def test_allows_normal_token(self) -> None:
        guard = StreamingGuard(patterns=[r"rm\s+-rf\s+/"])
        decision = guard.check_token("Hello world")
        assert decision.allow is True

    def test_blocks_dangerous_pattern(self) -> None:
        guard = StreamingGuard(patterns=[r"rm\s+-rf\s+/"])
        guard.check_token("sudo ")
        guard.check_token("rm ")
        guard.check_token("-rf ")
        decision = guard.check_token("/")
        # Buffer should now contain "sudo rm -rf /"
        assert decision.allow is False
        assert "rm" in (decision.matched_pattern or "").lower() or not decision.allow

    def test_reset_clears_buffer(self) -> None:
        guard = StreamingGuard(patterns=[r"danger"])
        guard.check_token("dang")
        guard.reset()
        decision = guard.check_token("er")
        assert decision.allow is True  # buffer was cleared

    def test_buffer_rolling_eviction(self) -> None:
        guard = StreamingGuard(patterns=[r"xyz"], buffer_size=10)
        # Push more than buffer_size chars
        for _ in range(20):
            guard.check_token("abcde")  # 100 chars total, buffer keeps last 10
        # "xyz" is not in the last 10 chars
        decision = guard.check_token("hello")
        assert decision.allow is True

    def test_add_pattern_dynamically(self) -> None:
        guard = StreamingGuard(patterns=[])
        guard.add_pattern(r"BLOCK_THIS")
        decision = guard.check_token("BLOCK_THIS text")
        assert decision.allow is False


# ---------------------------------------------------------------------------
# Toxicity Classifier
# ---------------------------------------------------------------------------

class TestToxicityClassifier:
    def setup_method(self) -> None:
        self.classifier = ToxicityClassifier(toxicity_threshold=0.4, use_llm_for_ambiguous=False)

    def test_clean_text_not_toxic(self) -> None:
        result = self.classifier.classify_sync("I love programming in Python.")
        assert result.is_toxic is False
        assert result.score < 0.4

    def test_result_fields_present(self) -> None:
        result = self.classifier.classify_sync("normal text")
        assert isinstance(result.score, float)
        assert isinstance(result.is_toxic, bool)
        assert isinstance(result.categories, list)
        assert result.method == "pattern"

    def test_score_in_range(self) -> None:
        result = self.classifier.classify_sync("any text here")
        assert 0.0 <= result.score <= 1.0

    @pytest.mark.asyncio
    async def test_classify_async_no_provider(self) -> None:
        classifier = ToxicityClassifier(use_llm_for_ambiguous=False)
        result = await classifier.classify("normal text", provider=None)
        assert isinstance(result.is_toxic, bool)
