"""Deepened coverage for tenant plan/cost constraints in EmbeddingOrchestrator.select().

The prior suite only tested the FREE-plan case. This adds paid-tier selection
(starter/professional/enterprise), plan upgrade/downgrade mid-session, and a
"budget exceeded" analogue: this codebase enforces embedding cost not through
a separate USD budget check but through the ``_COST_BY_PLAN`` cost-class
allowlist — when a tenant's plan cannot afford any candidate for a modality,
selection gracefully degrades to a cheaper modality / the ultimate fallback
rather than erroring.
"""
from __future__ import annotations

import pytest

from app.embedding.model_registry import EmbeddingModelRegistry, EmbeddingModelSpec
from app.embedding.orchestrator import EmbeddingOrchestrator
from app.ingestion.content_classifier import ContentType
from app.tenancy.context import PlanTier, TenantContext


def _ctx(plan: PlanTier, tenant_id: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=plan, api_key_id="k1")


@pytest.fixture
def orch() -> EmbeddingOrchestrator:
    return EmbeddingOrchestrator(registry=EmbeddingModelRegistry.build_default())


# ── Paid-tier plan selection ──────────────────────────────────────────────────


class TestPaidTierSelection:
    def test_free_plan_never_gets_medium_or_high_cost(self, orch: EmbeddingOrchestrator) -> None:
        result = orch.select(content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.FREE))
        assert result.cost_class in ("free", "low")

    def test_starter_plan_capped_same_as_free(self, orch: EmbeddingOrchestrator) -> None:
        result = orch.select(content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.STARTER))
        assert result.cost_class in ("free", "low")

    def test_professional_plan_can_get_medium_cost_model(
        self, orch: EmbeddingOrchestrator
    ) -> None:
        """Professional unlocks 'medium' cost class; with the default catalogue
        the affordable, highest-dimension text model is text-embedding-3-large
        (medium, 3072-d) over text-embedding-3-small (low, 1536-d)."""
        result = orch.select(content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.PROFESSIONAL))
        assert result.model_id == "text-embedding-3-large"
        assert result.cost_class == "medium"
        assert result.dimension == 3072

    def test_enterprise_plan_can_get_high_cost_model(self) -> None:
        """Build a registry with an explicit 'high' cost class model and verify
        only enterprise reaches it; professional stays capped at medium."""
        registry = EmbeddingModelRegistry(
            [
                EmbeddingModelSpec("text-low", "text", 512, "low", "p"),
                EmbeddingModelSpec("text-medium", "text", 1536, "medium", "p"),
                EmbeddingModelSpec("text-high", "text", 4096, "high", "p"),
            ]
        )
        orch = EmbeddingOrchestrator(registry=registry)

        enterprise_result = orch.select(
            content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.ENTERPRISE)
        )
        assert enterprise_result.model_id == "text-high"
        assert enterprise_result.cost_class == "high"

        professional_result = orch.select(
            content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.PROFESSIONAL)
        )
        assert professional_result.model_id == "text-medium"
        assert professional_result.cost_class == "medium"

    def test_no_tenant_ctx_defaults_to_free_tier_allowance(
        self, orch: EmbeddingOrchestrator
    ) -> None:
        result = orch.select(content_type=ContentType.TEXT, tenant_ctx=None)
        assert result.cost_class in ("free", "low")

    def test_unknown_plan_value_falls_back_to_low_only(self) -> None:
        """_COST_BY_PLAN.get(plan, ["low"]) — an unrecognised plan string (should
        never happen with the PlanTier enum, but the lookup itself must degrade
        safely) only allows 'low', never 'medium'/'high'."""
        registry = EmbeddingModelRegistry(
            [
                EmbeddingModelSpec("text-low", "text", 512, "low", "p"),
                EmbeddingModelSpec("text-high", "text", 4096, "high", "p"),
            ]
        )
        orch = EmbeddingOrchestrator(registry=registry)

        class _FakePlan:
            value = "super-secret-plan"

        class _FakeCtx:
            plan = _FakePlan()

        result = orch.select(content_type=ContentType.TEXT, tenant_ctx=_FakeCtx())  # type: ignore[arg-type]
        assert result.model_id == "text-low"
        assert result.cost_class == "low"


# ── Plan upgrade / downgrade mid-session ──────────────────────────────────────


class TestPlanChangeMidSession:
    def test_upgrade_from_free_to_professional_unlocks_better_model(
        self, orch: EmbeddingOrchestrator
    ) -> None:
        """The orchestrator is stateless per-call: the same instance, called
        first for a free tenant and then for the *same tenant id* now on
        professional, must reflect the upgrade on the very next call."""
        before = orch.select(content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.FREE, "t9"))
        after = orch.select(
            content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.PROFESSIONAL, "t9")
        )
        assert before.cost_class in ("free", "low")
        assert after.cost_class == "medium"
        assert after.dimension > before.dimension

    def test_downgrade_from_enterprise_to_free_loses_access_to_expensive_model(self) -> None:
        registry = EmbeddingModelRegistry(
            [
                EmbeddingModelSpec("text-low", "text", 512, "low", "p"),
                EmbeddingModelSpec("text-high", "text", 4096, "high", "p"),
            ]
        )
        orch = EmbeddingOrchestrator(registry=registry)

        before = orch.select(
            content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.ENTERPRISE, "t9")
        )
        after = orch.select(content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.FREE, "t9"))

        assert before.model_id == "text-high"
        assert after.model_id == "text-low"
        assert after.cost_class == "low"


# ── "Budget exceeded" analogue: cost-class exhaustion per modality ───────────


class TestBudgetExhaustionDegradesGracefully:
    def test_free_tenant_with_only_high_cost_code_model_falls_back_to_text(self) -> None:
        """CODE content prefers modality ['code', 'text']. If the only 'code'
        candidate is unaffordable on the tenant's plan, selection must silently
        degrade to the next modality (text) instead of erroring or returning
        the unaffordable model."""
        registry = EmbeddingModelRegistry(
            [
                EmbeddingModelSpec("code-expensive", "code", 2048, "high", "p"),
                EmbeddingModelSpec("text-cheap", "text", 1536, "low", "p"),
            ]
        )
        orch = EmbeddingOrchestrator(registry=registry)

        result = orch.select(content_type=ContentType.CODE, tenant_ctx=_ctx(PlanTier.FREE))
        assert result.model_id == "text-cheap"
        assert result.cost_class == "low"

    def test_all_modalities_unaffordable_hits_ultimate_fallback(self) -> None:
        """When the registry has candidates but NONE are affordable on the
        tenant's plan for ANY modality the content type maps to, and there is
        also no plain 'text' model at all to fall back to, selection must
        still return a usable (though degraded) result — the hard-coded
        fake-embedding ultimate fallback — never raise."""
        registry = EmbeddingModelRegistry(
            [
                EmbeddingModelSpec("code-only-expensive", "code", 2048, "high", "p"),
            ]
        )
        orch = EmbeddingOrchestrator(registry=registry)

        result = orch.select(content_type=ContentType.CODE, tenant_ctx=_ctx(PlanTier.FREE))
        assert result.model_id == "fake-embedding"
        assert result.dimension == 10
        assert result.cost_class == "free"
        assert result.selection_reason == "no embedding model available"

    def test_last_resort_fallback_ignores_plan_cap_when_no_affordable_text_model(
        self,
    ) -> None:
        """Documents a real behavioural edge case (pinned, not asserted as
        'correct'): when the only TEXT model in the registry is unaffordable
        for the tenant's plan, ``select()``'s literal
        ``list_by_modality("text")`` last-resort branch returns it ANYWAY,
        bypassing the cost-class allowlist — rather than falling through to
        the cost-blind 'fake-embedding' ultimate fallback. This is the one
        place cost enforcement can be bypassed, so it is worth pinning
        explicitly: a free-tier tenant CAN receive a 'high' cost-class model
        if it is the only text model registered."""
        registry = EmbeddingModelRegistry(
            [
                EmbeddingModelSpec("text-only-expensive", "text", 3072, "high", "p"),
            ]
        )
        orch = EmbeddingOrchestrator(registry=registry)

        result = orch.select(content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.FREE))
        assert result.model_id == "text-only-expensive"
        assert result.cost_class == "high"
        assert result.selection_reason == "fallback to text embedding"

    def test_empty_registry_hits_ultimate_fallback_for_any_content_type(self) -> None:
        registry = EmbeddingModelRegistry([])
        orch = EmbeddingOrchestrator(registry=registry)

        result = orch.select(content_type=ContentType.TEXT, tenant_ctx=_ctx(PlanTier.ENTERPRISE))
        assert result.model_id == "fake-embedding"
        assert result.provider == "fake"
