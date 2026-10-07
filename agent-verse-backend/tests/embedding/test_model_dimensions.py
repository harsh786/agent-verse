"""Deepened coverage for model_registry.py and dimension_policy.py.

The prior test suite only exercised one happy path (dimension lookup for a
known model). This file adds:
  * unknown model-id fallback behaviour for DimensionPolicy
  * dimension lookup for every registered model in the default catalogue
  * EmbeddingModelRegistry.get()/filter()/list_by_modality() edge cases
  * a regression test for a real bug: EmbeddingOrchestrator.select() used to
    report the WRONG dimension for a custom, env-configured embedding model
    because it round-tripped through DimensionPolicy's static map instead of
    trusting the registry spec's own ``dimension`` field.
"""
from __future__ import annotations

import pytest

from app.embedding.dimension_policy import DimensionPolicy
from app.embedding.model_registry import EmbeddingModelRegistry, EmbeddingModelSpec
from app.embedding.orchestrator import EmbeddingOrchestrator
from app.ingestion.content_classifier import ContentType
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ── DimensionPolicy ───────────────────────────────────────────────────────────


class TestDimensionPolicyUnknownModel:
    def test_unknown_model_id_falls_back_to_1536(self) -> None:
        policy = DimensionPolicy()
        assert policy.select("some-model-nobody-registered") == 1536

    def test_empty_model_id_falls_back_to_1536(self) -> None:
        policy = DimensionPolicy()
        assert policy.select("") == 1536

    def test_none_like_garbage_model_id_falls_back_to_1536(self) -> None:
        policy = DimensionPolicy()
        assert policy.select("🤖-not-a-real-model") == 1536


class TestDimensionPolicyEveryRegisteredModel:
    """Every model in the default catalogue must have a correct, known dimension."""

    @pytest.mark.parametrize(
        ("model_id", "expected_dim"),
        [
            ("text-embedding-3-small", 1536),
            ("text-embedding-3-large", 3072),
            ("voyage-3-lite", 512),
            ("voyage-code-3", 1024),
            ("voyage-multimodal-3", 1024),
            ("fake-embedding", 10),
        ],
    )
    def test_dimension_lookup(self, model_id: str, expected_dim: int) -> None:
        policy = DimensionPolicy()
        assert policy.select(model_id) == expected_dim

    def test_regression_voyage_3_lite_is_512_not_1024(self) -> None:
        """Regression: DimensionPolicy's static map had 'voyage-3-lite' hard-coded
        to 1024, while the model registry (and app/embedding/router.py, and
        tests/api/test_phase5_api.py::test_embedding_model_registry_voyage_dimension_512)
        all agree the real dimension is 512. Any caller trusting DimensionPolicy
        for this model — as EmbeddingOrchestrator.select() used to — would have
        silently produced a 1024-d vector for a model that actually emits 512-d
        vectors, corrupting a pgvector column sized for 512."""
        assert DimensionPolicy().select("voyage-3-lite") == 512


# ── EmbeddingModelRegistry ────────────────────────────────────────────────────


def _endpoint(model_id: str, provider: str = "onprem", **kw: object):
    from app.ai_router.models import ModelCapability, ModelEndpoint

    extra = dict(kw.pop("extra", {}) or {})  # type: ignore[call-overload]
    extra.setdefault("source", "env")
    return ModelEndpoint(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        capabilities=[ModelCapability.EMBEDDING],
        extra=extra,
        **kw,  # type: ignore[arg-type]
    )


def _from(*endpoints: object) -> EmbeddingModelRegistry:
    from app.ai_router.registry import ModelRegistry

    reg = ModelRegistry()  # isolated: never auto-seeded from env
    for ep in endpoints:
        reg.register_configured(ep)  # type: ignore[arg-type]
    return EmbeddingModelRegistry.from_model_registry(registry=reg)


class TestFromModelRegistry:
    """The routing registry is the Model Registry's configured embedding models —
    no literal catalogue, no env-only EMBEDDING_MODEL / EMBEDDING_PROVIDER."""

    def test_every_configured_embedding_model_is_listed_with_its_dimension(self) -> None:
        registry = _from(
            _endpoint("my-onprem-model", extra={"dimensions": 768}),
            _endpoint("Qwen/Qwen3-Embedding-0.6B"),  # catalog-known: 1024
        )
        spec = registry.get("my-onprem-model")
        assert spec is not None
        assert spec.dimension == 768
        assert spec.provider == "onprem"
        assert spec.modality == "text"
        assert spec.key == "onprem/my-onprem-model"
        qwen = registry.get("Qwen/Qwen3-Embedding-0.6B")
        assert qwen is not None and qwen.dimension == 1024

    def test_requested_output_width_is_the_dimension_when_not_measured(self) -> None:
        registry = _from(_endpoint("shortened", extra={"output_dimensions": 1024}))
        spec = registry.get("shortened")
        assert spec is not None and spec.dimension == 1024

    def test_unknown_width_is_zero_and_never_routed(self) -> None:
        registry = _from(_endpoint("reg-code-mystery"))
        spec = registry.get("reg-code-mystery")
        assert spec is not None and spec.dimension == 0
        orch = EmbeddingOrchestrator(registry=registry)
        assert orch.select(ContentType.CODE, target_dim=1024).uses_default_embedder

    def test_modality_from_extra_else_model_id(self) -> None:
        registry = _from(
            _endpoint("acme-code-embed-2", extra={"dimensions": 1024}),
            _endpoint("acme-multimodal-1", extra={"dimensions": 1024}),
            _endpoint("plain-embed", extra={"dimensions": 1024, "embedding_modality": "code"}),
            _endpoint("decoder-embed", extra={"dimensions": 1024}),
        )
        assert registry.get("acme-code-embed-2").modality == "code"  # type: ignore[union-attr]
        assert registry.get("acme-multimodal-1").modality == "multimodal"  # type: ignore[union-attr]
        assert registry.get("plain-embed").modality == "code"  # type: ignore[union-attr]
        # "decoder" contains "code" but not as a word: a text model.
        assert registry.get("decoder-embed").modality == "text"  # type: ignore[union-attr]

    def test_cost_class_from_registry_price(self) -> None:
        registry = _from(
            _endpoint("free-embed", extra={"dimensions": 1024}),
            _endpoint("low-embed", extra={"dimensions": 1024}, cost_per_1k_input=0.00002),
            _endpoint("high-embed", extra={"dimensions": 1024}, cost_per_1k_input=0.01),
        )
        assert registry.get("free-embed").cost_class == "free"  # type: ignore[union-attr]
        assert registry.get("low-embed").cost_class == "low"  # type: ignore[union-attr]
        assert registry.get("high-embed").cost_class == "high"  # type: ignore[union-attr]

    def test_env_embedding_model_alone_adds_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EMBEDDING_MODEL / EMBEDDING_PROVIDER are not read here (the seeder puts
        the env model INTO the Model Registry; this reads only the registry)."""
        monkeypatch.setenv("EMBEDDING_MODEL", "env-only-model")
        monkeypatch.setenv("EMBEDDING_PROVIDER", "openai_compatible")
        registry = _from()
        assert registry.list_all() == []

    def test_orchestrator_routes_a_configured_code_model_of_the_collection_width(
        self,
    ) -> None:
        registry = _from(_endpoint("acme-code-embed-2", "voyage", extra={"dimensions": 384}))
        orch = EmbeddingOrchestrator(registry=registry)
        result = orch.select(
            ContentType.CODE,
            TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1"),
            target_dim=384,
        )
        assert result.model_id == "acme-code-embed-2"
        assert result.dimension == 384
        assert result.key == "voyage/acme-code-embed-2"


class TestEmbeddingModelRegistry:
    @staticmethod
    def _registry() -> EmbeddingModelRegistry:
        return EmbeddingModelRegistry(
            [
                EmbeddingModelSpec("reg-text", "text", 1536, "low", "openai"),
                EmbeddingModelSpec("reg-code", "code", 1024, "low", "voyage"),
                EmbeddingModelSpec("reg-big", "text", 3072, "medium", "openai"),
            ]
        )

    def test_get_known_model_returns_spec(self) -> None:
        spec = self._registry().get("reg-text")
        assert spec is not None
        assert spec.dimension == 1536

    def test_get_unknown_model_returns_none(self) -> None:
        assert self._registry().get("does-not-exist") is None

    def test_list_by_modality_unknown_modality_returns_empty(self) -> None:
        assert self._registry().list_by_modality("smell-o-vision") == []

    def test_list_all_returns_every_model(self) -> None:
        assert len(self._registry().list_all()) == 3

    def test_filter_by_cost_class(self) -> None:
        low_cost = self._registry().filter(cost_class="low")
        assert low_cost
        assert all(m.cost_class == "low" for m in low_cost)

    def test_filter_unknown_cost_class_returns_empty(self) -> None:
        assert self._registry().filter(cost_class="platinum") == []

    def test_filter_no_args_returns_everything(self) -> None:
        registry = self._registry()
        assert registry.filter() == registry.list_all()

    def test_duplicate_model_id_last_one_wins(self) -> None:
        """The registry indexes by model_id in a dict; a duplicate id overwrites
        rather than crashing or silently keeping the first entry."""
        specs = [
            EmbeddingModelSpec("dupe", "text", 111, "low", "p1"),
            EmbeddingModelSpec("dupe", "text", 222, "low", "p2"),
        ]
        registry = EmbeddingModelRegistry(specs)
        spec = registry.get("dupe")
        assert spec is not None
        assert spec.dimension == 222
        assert spec.provider == "p2"


# ── Regression: orchestrator must trust the registry's own dimension ─────────


class TestOrchestratorDimensionRegression:
    def test_routed_custom_model_reports_its_own_dimension(self, tenant_ctx) -> None:
        """A custom code embedder (never in DimensionPolicy's hardcoded map) is
        reported with the registry's own width, not DimensionPolicy's 1536."""
        custom_spec = EmbeddingModelSpec(
            model_id="my-onprem-code-embedder",
            modality="code",
            dimension=768,
            cost_class="low",
            provider="onprem",
        )
        orch = EmbeddingOrchestrator(registry=EmbeddingModelRegistry([custom_spec]))
        result = orch.select(ContentType.CODE, tenant_ctx, target_dim=768)
        assert result.model_id == "my-onprem-code-embedder"
        assert result.dimension == 768

    def test_default_embedder_selection_reports_the_target_width(self, tenant_ctx) -> None:
        orch = EmbeddingOrchestrator(registry=EmbeddingModelRegistry([]))
        result = orch.select(
            ContentType.CODE, tenant_ctx, target_dim=512, default_model="default-embed"
        )
        assert result.model_id == "default-embed"
        assert result.dimension == 512
