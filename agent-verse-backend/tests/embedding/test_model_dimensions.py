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

    def test_every_default_registry_model_has_a_dimension_policy_entry(self) -> None:
        """Cross-check: DimensionPolicy's static map should cover every model
        the *default* (no env override) registry advertises, so orchestrator
        selections are never silently wrong for a built-in model."""
        registry = EmbeddingModelRegistry.build_default()
        policy = DimensionPolicy()
        for spec in registry.list_all():
            assert policy.select(spec.model_id) == spec.dimension, (
                f"DimensionPolicy disagrees with the registry for {spec.model_id!r}"
            )

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


class TestBuildDefaultWithEnvConfiguredModel:
    """EmbeddingModelRegistry.build_default() special-cases a dedicated,
    env-configured embedding model (EMBEDDING_MODEL / NVIDIA_EMBED_MODEL —
    an on-prem or NVIDIA OpenAI-compatible endpoint). This whole branch had
    zero test coverage."""

    def test_embedding_model_env_var_is_advertised_with_configured_dimension(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "my-onprem-model")
        monkeypatch.setenv("EMBEDDING_DIM", "768")
        monkeypatch.setenv("EMBEDDING_PROVIDER", "openai_compatible")
        monkeypatch.delenv("NVIDIA_EMBED_MODEL", raising=False)

        registry = EmbeddingModelRegistry.build_default()
        spec = registry.get("my-onprem-model")
        assert spec is not None
        assert spec.dimension == 768
        assert spec.provider == "openai_compatible"
        assert spec.cost_class == "low"
        assert spec.modality == "text"
        # The catalogue's own text-embedding-3-* models must NOT be advertised
        # (they'd 404 against a single-model provider).
        assert registry.get("text-embedding-3-small") is None
        assert registry.get("text-embedding-3-large") is None
        # fake-embedding remains as the ultimate no-provider fallback.
        assert registry.get("fake-embedding") is not None

    def test_nvidia_embed_model_env_var_used_when_embedding_model_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        monkeypatch.setenv("NVIDIA_EMBED_MODEL", "nv-embed-v2")
        monkeypatch.delenv("EMBEDDING_DIM", raising=False)

        registry = EmbeddingModelRegistry.build_default()
        assert registry.get("nv-embed-v2") is not None

    def test_embedding_model_takes_priority_over_nvidia_embed_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "explicit-model")
        monkeypatch.setenv("NVIDIA_EMBED_MODEL", "nvidia-model")

        registry = EmbeddingModelRegistry.build_default()
        assert registry.get("explicit-model") is not None
        assert registry.get("nvidia-model") is None

    def test_missing_embedding_dim_defaults_to_1024(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "no-dim-model")
        monkeypatch.delenv("EMBEDDING_DIM", raising=False)

        registry = EmbeddingModelRegistry.build_default()
        spec = registry.get("no-dim-model")
        assert spec is not None
        assert spec.dimension == 1024

    def test_non_numeric_embedding_dim_falls_back_to_1024(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed EMBEDDING_DIM (e.g. a typo'd env var) must not crash
        registry construction — it degrades to the 1024 default."""
        monkeypatch.setenv("EMBEDDING_MODEL", "bad-dim-model")
        monkeypatch.setenv("EMBEDDING_DIM", "not-a-number")

        registry = EmbeddingModelRegistry.build_default()
        spec = registry.get("bad-dim-model")
        assert spec is not None
        assert spec.dimension == 1024

    def test_missing_embedding_provider_defaults_to_openai(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "no-provider-model")
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

        registry = EmbeddingModelRegistry.build_default()
        spec = registry.get("no-provider-model")
        assert spec is not None
        assert spec.provider == "openai"

    def test_orchestrator_selects_configured_model_with_correct_dimension(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: the dimension bug fixed above, exercised through the
        real env-var-driven registry construction path (not a hand-built
        registry), for a tenant on any plan."""
        monkeypatch.setenv("EMBEDDING_MODEL", "onprem-e2e")
        monkeypatch.setenv("EMBEDDING_DIM", "384")

        registry = EmbeddingModelRegistry.build_default()
        orch = EmbeddingOrchestrator(registry=registry)
        result = orch.select(
            content_type=ContentType.TEXT,
            tenant_ctx=TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1"),
        )
        assert result.model_id == "onprem-e2e"
        assert result.dimension == 384


class TestEmbeddingModelRegistry:
    def test_get_known_model_returns_spec(self) -> None:
        registry = EmbeddingModelRegistry.build_default()
        spec = registry.get("text-embedding-3-small")
        assert spec is not None
        assert spec.dimension == 1536

    def test_get_unknown_model_returns_none(self) -> None:
        registry = EmbeddingModelRegistry.build_default()
        assert registry.get("does-not-exist") is None

    def test_list_by_modality_unknown_modality_returns_empty(self) -> None:
        registry = EmbeddingModelRegistry.build_default()
        assert registry.list_by_modality("smell-o-vision") == []

    def test_list_all_returns_every_model(self) -> None:
        registry = EmbeddingModelRegistry.build_default()
        assert len(registry.list_all()) == 6

    def test_filter_by_cost_class(self) -> None:
        registry = EmbeddingModelRegistry.build_default()
        low_cost = registry.filter(cost_class="low")
        assert low_cost
        assert all(m.cost_class == "low" for m in low_cost)

    def test_filter_unknown_cost_class_returns_empty(self) -> None:
        registry = EmbeddingModelRegistry.build_default()
        assert registry.filter(cost_class="platinum") == []

    def test_filter_no_args_returns_everything(self) -> None:
        registry = EmbeddingModelRegistry.build_default()
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
    def test_configured_custom_model_reports_its_own_dimension(self, tenant_ctx) -> None:
        """A custom embedding model (e.g. an on-prem/NVIDIA endpoint configured
        via EMBEDDING_MODEL/EMBEDDING_DIM) is NEVER present in DimensionPolicy's
        hardcoded map. Before the fix, EmbeddingOrchestrator.select() silently
        reported DimensionPolicy's 1536 default for such a model instead of the
        model's real, configured dimension — which would corrupt any dimension
        check downstream (e.g. guard_dimension_change / pgvector column width).
        """
        custom_spec = EmbeddingModelSpec(
            model_id="my-onprem-embedder",
            modality="text",
            dimension=768,
            cost_class="low",
            provider="openai_compatible",
            description="Custom on-prem model, not in DimensionPolicy's static map",
        )
        registry = EmbeddingModelRegistry([custom_spec])
        orch = EmbeddingOrchestrator(registry=registry)

        result = orch.select(content_type=ContentType.TEXT, tenant_ctx=tenant_ctx)

        assert result.model_id == "my-onprem-embedder"
        assert result.dimension == 768, (
            "orchestrator must report the registry's own dimension for a custom "
            "model, not DimensionPolicy's stale/absent static-map default"
        )

    def test_fallback_branch_also_reports_registry_dimension(self, tenant_ctx) -> None:
        """Same regression, but through the 'fallback to any text model' branch
        (reached when the requested modality has no affordable candidate)."""
        custom_spec = EmbeddingModelSpec(
            model_id="fallback-onprem-embedder",
            modality="text",
            dimension=512,
            cost_class="low",
            provider="openai_compatible",
        )
        registry = EmbeddingModelRegistry([custom_spec])
        orch = EmbeddingOrchestrator(registry=registry)

        # CODE has no candidates at all in this registry, so the "code" modality
        # step is skipped and it falls through to the "text" fallback modality
        # loop, and then to the explicit `fallback = list_by_modality("text")`
        # branch only if the modality loop itself found nothing — here the loop
        # DOES find the text model in modality iteration, exercising the primary
        # branch on the second modality ("text") of CODE's modality list.
        result = orch.select(content_type=ContentType.CODE, tenant_ctx=tenant_ctx)
        assert result.model_id == "fallback-onprem-embedder"
        assert result.dimension == 512
