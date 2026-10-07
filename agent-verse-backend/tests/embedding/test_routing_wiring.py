"""Is-it-actually-wired tests for multi-model embedding routing (D-10).

create_app must expose an embedding provider resolver on app.state, and the
resolver must map a configured Model Registry embedding model (by its
``provider/model_id`` key) to an embedder built from its registry entry, so
EmbeddingOrchestrator can route a content type to a configured specialist.
"""
from __future__ import annotations


def test_create_app_sets_embed_provider_resolver_attribute() -> None:
    from app.main import create_app

    app = create_app()
    # Attribute is always present (None when ≤1 embedding provider is configured,
    # which makes the ingestion path fall back to the single embedder).
    assert hasattr(app.state, "embed_provider_resolver")


def test_registry_resolver_builds_only_configured_registry_models() -> None:
    """The resolver maps a Model Registry KEY (provider/model_id) to an embedder
    built from that registry entry — a provider NAME or an unconfigured model
    resolves to None (the default embedder then serves the content)."""
    from app.ai_router.models import ModelCapability, ModelEndpoint
    from app.ai_router.registry import ModelRegistry
    from app.core.config import Settings
    from app.embedding.orchestrator import build_registry_embedder_resolver
    from app.observability.traced_provider import unwrap_provider
    from app.providers.embedder_factory import embedder_model_name

    reg = ModelRegistry()
    reg.register_configured(
        ModelEndpoint(
            provider="openai",
            model_id="text-embedding-3-large",
            display_name="code embedder",
            capabilities=[ModelCapability.EMBEDDING],
            extra={"source": "env", "embedding_modality": "code"},
        )
    )
    resolver = build_registry_embedder_resolver(
        Settings(openai_api_key="sk-test"), registry=reg
    )

    built = resolver("openai/text-embedding-3-large")
    assert built is not None
    assert embedder_model_name(unwrap_provider(built)) == "text-embedding-3-large"
    assert resolver("openai") is None  # a provider name is not a model
    assert resolver("voyage/voyage-code-3") is None  # not configured here
    assert resolver("") is None
