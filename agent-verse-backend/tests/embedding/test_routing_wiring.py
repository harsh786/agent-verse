"""Is-it-actually-wired tests for multi-model embedding routing (D-10).

create_app must expose an embedding provider resolver on app.state, and the
resolver must map every configured provider by name so EmbeddingOrchestrator can
route the selected model to its own provider.
"""
from __future__ import annotations

from unittest.mock import patch


def test_create_app_sets_embed_provider_resolver_attribute() -> None:
    from app.main import create_app

    app = create_app()
    # Attribute is always present (None when ≤1 embedding provider is configured,
    # which makes the ingestion path fall back to the single embedder).
    assert hasattr(app.state, "embed_provider_resolver")


def test_create_app_builds_multi_provider_resolver_when_keys_present() -> None:
    from app.main import create_app

    def _fake_env(name: str) -> str:
        return {
            "VOYAGE_API_KEY": "vk",
            "OPENAI_API_KEY": "ok",
        }.get(name, "")

    # Patch key lookup so create_app constructs BOTH a Voyage and an OpenAI
    # embedding provider, and assert the resolver routes each name to a provider.
    with patch("app.core.config.get_provider_env", side_effect=_fake_env):
        app = create_app()

    resolver = app.state.embed_provider_resolver
    assert resolver is not None, "expected a resolver when 2 providers are configured"
    assert resolver("voyage") is not None
    assert resolver("openai") is not None
    # An unconfigured provider resolves to None → embed_for_content falls back.
    assert resolver("gemini") is None
