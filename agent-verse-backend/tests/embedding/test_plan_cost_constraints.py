"""Tenant plan/cost constraints in EmbeddingOrchestrator.select().

Every embedding comes from the Model Registry-configured embedder. Text content
is always the default embedder (no plan-dependent model swap — that would put
two models' vectors in one index). The plan's cost-class allowlist
(``_COST_BY_PLAN``) applies to the only thing that is routed: a configured
CODE / multimodal registry specialist of the collection's width. When the plan
cannot afford one, the default embedder serves the content — never an
unaffordable model, never a fake one.
"""
from __future__ import annotations

from app.embedding.model_registry import EmbeddingModelRegistry, EmbeddingModelSpec
from app.embedding.orchestrator import EmbeddingOrchestrator
from app.ingestion.content_classifier import ContentType
from app.tenancy.context import PlanTier, TenantContext

_DIM = 1024


def _ctx(plan: PlanTier, tenant_id: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=plan, api_key_id="k1")


def _orch(*specs: EmbeddingModelSpec) -> EmbeddingOrchestrator:
    return EmbeddingOrchestrator(registry=EmbeddingModelRegistry(list(specs)))


_CODE_LOW = EmbeddingModelSpec("code-low", "code", _DIM, "low", "p")
_CODE_MEDIUM = EmbeddingModelSpec("code-medium", "code", _DIM, "medium", "p")
_CODE_HIGH = EmbeddingModelSpec("code-high", "code", _DIM, "high", "p")


class TestPaidTierSelection:
    def test_free_plan_never_gets_medium_or_high_cost(self) -> None:
        orch = _orch(_CODE_HIGH, _CODE_MEDIUM, _CODE_LOW)
        result = orch.select(ContentType.CODE, _ctx(PlanTier.FREE), target_dim=_DIM)
        assert result.model_id == "code-low"

    def test_starter_plan_capped_same_as_free(self) -> None:
        orch = _orch(_CODE_HIGH, _CODE_MEDIUM, _CODE_LOW)
        result = orch.select(ContentType.CODE, _ctx(PlanTier.STARTER), target_dim=_DIM)
        assert result.model_id == "code-low"

    def test_professional_plan_can_get_medium_cost_model(self) -> None:
        orch = _orch(_CODE_HIGH, _CODE_MEDIUM)
        result = orch.select(ContentType.CODE, _ctx(PlanTier.PROFESSIONAL), target_dim=_DIM)
        assert result.model_id == "code-medium"
        assert result.cost_class == "medium"

    def test_enterprise_plan_can_get_high_cost_model(self) -> None:
        orch = _orch(_CODE_HIGH, _CODE_MEDIUM)
        enterprise = orch.select(ContentType.CODE, _ctx(PlanTier.ENTERPRISE), target_dim=_DIM)
        assert enterprise.model_id == "code-high"
        professional = orch.select(
            ContentType.CODE, _ctx(PlanTier.PROFESSIONAL), target_dim=_DIM
        )
        assert professional.model_id == "code-medium"

    def test_no_tenant_ctx_defaults_to_free_tier_allowance(self) -> None:
        orch = _orch(_CODE_HIGH, _CODE_LOW)
        result = orch.select(ContentType.CODE, None, target_dim=_DIM)
        assert result.model_id == "code-low"

    def test_unknown_plan_value_falls_back_to_low_only(self) -> None:
        orch = _orch(_CODE_HIGH, _CODE_LOW)

        class _FakePlan:
            value = "super-secret-plan"

        class _FakeCtx:
            plan = _FakePlan()

        result = orch.select(ContentType.CODE, _FakeCtx(), target_dim=_DIM)  # type: ignore[arg-type]
        assert result.model_id == "code-low"

    def test_text_is_never_plan_routed(self) -> None:
        """Text keeps the default embedder whatever the plan affords."""
        orch = _orch(EmbeddingModelSpec("text-high", "text", _DIM, "high", "p"))
        for plan in PlanTier:
            result = orch.select(
                ContentType.TEXT, _ctx(plan), target_dim=_DIM, default_model="default-embed"
            )
            assert result.uses_default_embedder
            assert result.model_id == "default-embed"


class TestPlanChangeMidSession:
    def test_upgrade_from_free_to_professional_unlocks_better_model(self) -> None:
        orch = _orch(_CODE_MEDIUM, _CODE_LOW)
        before = orch.select(ContentType.CODE, _ctx(PlanTier.FREE, "t9"), target_dim=_DIM)
        after = orch.select(ContentType.CODE, _ctx(PlanTier.PROFESSIONAL, "t9"), target_dim=_DIM)
        assert before.model_id == "code-low"
        assert after.model_id == "code-medium"

    def test_downgrade_from_enterprise_to_free_loses_access_to_expensive_model(self) -> None:
        orch = _orch(_CODE_HIGH, _CODE_LOW)
        before = orch.select(ContentType.CODE, _ctx(PlanTier.ENTERPRISE, "t9"), target_dim=_DIM)
        after = orch.select(ContentType.CODE, _ctx(PlanTier.FREE, "t9"), target_dim=_DIM)
        assert before.model_id == "code-high"
        assert after.model_id == "code-low"


class TestBudgetExhaustionDegradesGracefully:
    def test_free_tenant_with_only_high_cost_code_model_gets_the_default(self) -> None:
        orch = _orch(_CODE_HIGH)
        result = orch.select(
            ContentType.CODE, _ctx(PlanTier.FREE), target_dim=_DIM, default_model="default-embed"
        )
        assert result.uses_default_embedder
        assert result.model_id == "default-embed"

    def test_empty_registry_is_the_default_embedder_for_any_content_type(self) -> None:
        orch = _orch()
        for content_type in ContentType:
            result = orch.select(content_type, _ctx(PlanTier.ENTERPRISE), target_dim=_DIM)
            assert result.uses_default_embedder
            assert result.model_id == ""
