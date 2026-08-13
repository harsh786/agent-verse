"""Tests for alert router, SLO tracker, attribution verifier, complexity scorer, shadow router."""
from __future__ import annotations

import pytest
from app.observability.alert_router import AlertRouter, AlertRule
from app.observability.slo_tracker import SLOTracker, SLODefinition
from app.evals.attribution_verifier import AttributionVerifier
from app.ai_router.complexity_scorer import QueryComplexityScorer
from app.ai_router.shadow_router import ShadowRouter, ShadowRoutingConfig


# ---------------------------------------------------------------------------
# AlertRouter
# ---------------------------------------------------------------------------

class TestAlertRouter:
    def setup_method(self) -> None:
        self.router = AlertRouter()

    @pytest.mark.asyncio
    async def test_fires_alert_when_threshold_exceeded(self) -> None:
        rule = AlertRule(
            metric="error_rate", threshold=0.05, window_seconds=60,
            severity="warning", webhook_url="", name="err-rate-alert"
        )
        self.router.register_rule(rule)
        fired = await self.router.evaluate("error_rate", 0.10, tenant_id="t1")
        assert len(fired) == 1
        assert fired[0].rule_name == "err-rate-alert"

    @pytest.mark.asyncio
    async def test_does_not_fire_below_threshold(self) -> None:
        rule = AlertRule(
            metric="error_rate", threshold=0.05, window_seconds=60,
            severity="warning", webhook_url="", name="err-rate-alert2"
        )
        self.router.register_rule(rule)
        fired = await self.router.evaluate("error_rate", 0.01)
        assert len(fired) == 0

    def test_list_and_remove_rules(self) -> None:
        rule = AlertRule(
            metric="latency", threshold=2.0, window_seconds=60,
            severity="info", webhook_url="", name="lat-rule"
        )
        self.router.register_rule(rule)
        assert any(r.name == "lat-rule" for r in self.router.list_rules())
        removed = self.router.remove_rule("lat-rule")
        assert removed
        assert not any(r.name == "lat-rule" for r in self.router.list_rules())

    @pytest.mark.asyncio
    async def test_cooldown_prevents_duplicate_alerts(self) -> None:
        rule = AlertRule(
            metric="cpu", threshold=0.9, window_seconds=60,
            severity="critical", webhook_url="", name="cpu-alert"
        )
        self.router.register_rule(rule)
        f1 = await self.router.evaluate("cpu", 0.95)
        f2 = await self.router.evaluate("cpu", 0.95)
        assert len(f1) == 1
        assert len(f2) == 0  # cooldown active


# ---------------------------------------------------------------------------
# SLOTracker
# ---------------------------------------------------------------------------

class TestSLOTracker:
    def setup_method(self) -> None:
        self.tracker = SLOTracker()
        self.slo = SLODefinition(name="api-slo", target=0.99, window_hours=1, tenant_id="t1")

    def test_perfect_success_rate(self) -> None:
        for _ in range(100):
            self.tracker.record_event(True, self.slo)
        status = self.tracker.burn_rate(self.slo)
        assert status.current_success_rate == pytest.approx(1.0)
        assert status.burn_rate_multiple <= 1.0

    def test_all_failures_high_burn_rate(self) -> None:
        for _ in range(10):
            self.tracker.record_event(False, self.slo)
        status = self.tracker.burn_rate(self.slo)
        assert status.burn_rate_multiple > 1.0
        assert status.is_breaching is True

    def test_no_events_returns_status(self) -> None:
        status = self.tracker.burn_rate(self.slo)
        assert status.total_events == 0
        assert status.current_success_rate == pytest.approx(1.0)

    def test_summary(self) -> None:
        self.tracker.record_event(True, self.slo)
        summary = self.tracker.summary("t1")
        assert len(summary) == 1
        assert summary[0]["slo_name"] == "api-slo"


# ---------------------------------------------------------------------------
# AttributionVerifier
# ---------------------------------------------------------------------------

class TestAttributionVerifier:
    def setup_method(self) -> None:
        self.verifier = AttributionVerifier(jaccard_threshold=0.1)

    def test_verify_relevant_citation(self) -> None:
        answer = "Python is widely used in data science [1]."
        chunks = ["Python is popular for data science and machine learning."]
        report = self.verifier.verify(answer, chunks)
        assert report.verified_count >= 0
        assert 0.0 <= report.precision_score <= 1.0

    def test_no_citations_returns_full_precision(self) -> None:
        answer = "No citations here."
        chunks = ["some chunk"]
        report = self.verifier.verify(answer, chunks, citation_indices=[])
        assert report.precision_score == 1.0
        assert report.verified_count == 0

    def test_out_of_range_citation(self) -> None:
        answer = "claim [99]."
        chunks = ["only one chunk"]
        report = self.verifier.verify(answer, chunks)
        assert report.failed_count >= 1

    def test_extract_citation_indices(self) -> None:
        text = "fact [1] and also [3] plus [2]."
        indices = self.verifier._extract_citation_indices(text)
        # 1-based → 0-based
        assert 0 in indices
        assert 1 in indices
        assert 2 in indices


# ---------------------------------------------------------------------------
# QueryComplexityScorer
# ---------------------------------------------------------------------------

class TestQueryComplexityScorer:
    def setup_method(self) -> None:
        self.scorer = QueryComplexityScorer()

    def test_simple_query(self) -> None:
        result = self.scorer.score("What time is it?")
        assert result.level == "simple"
        assert result.score < 0.35

    def test_complex_technical_query(self) -> None:
        result = self.scorer.score(
            "How does the transformer architecture optimize gradient descent "
            "during distributed inference, and also how does quantization "
            "affect vector embedding precision?"
        )
        assert result.level in ("moderate", "complex")
        assert result.score > 0.2

    def test_score_in_range(self) -> None:
        for query in ["hi", "a" * 500, "what is python and also how does it work with algorithms?"]:
            r = self.scorer.score(query)
            assert 0.0 <= r.score <= 1.0

    def test_recommended_tier(self) -> None:
        simple = self.scorer.score("hi")
        assert simple.recommended_model_tier() == "small"


# ---------------------------------------------------------------------------
# ShadowRouter
# ---------------------------------------------------------------------------

class TestShadowRouter:
    @pytest.mark.asyncio
    async def test_returns_primary_response(self) -> None:
        class Provider:
            def __init__(self, name: str) -> None:
                self.name = name

            async def complete(self, req: object) -> object:
                class R:
                    content = None
                r = R()
                r.content = self.name
                return r

        router = ShadowRouter(ShadowRoutingConfig(enabled=True, sample_rate=1.0))
        from app.providers.base import CompletionRequest, Message
        req = CompletionRequest(messages=[Message(role="user", content="hi")], model="")
        resp = await router.shadow_call(req, Provider("primary"), Provider("shadow"))
        assert resp.content == "primary"

    @pytest.mark.asyncio
    async def test_zero_sample_rate_no_shadow(self) -> None:
        calls = {"shadow": 0}

        class Primary:
            async def complete(self, req: object) -> object:
                class R:
                    content = "primary"
                return R()

        class Shadow:
            async def complete(self, req: object) -> object:
                calls["shadow"] += 1
                class R:
                    content = "shadow"
                return R()

        router = ShadowRouter(ShadowRoutingConfig(enabled=True, sample_rate=0.0))
        from app.providers.base import CompletionRequest, Message
        req = CompletionRequest(messages=[Message(role="user", content="hi")], model="")
        for _ in range(10):
            await router.shadow_call(req, Primary(), Shadow())
        assert calls["shadow"] == 0

    def test_get_log_returns_list(self) -> None:
        router = ShadowRouter()
        log = router.get_log()
        assert isinstance(log, list)
