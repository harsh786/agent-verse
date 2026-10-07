"""The seeder registers embedding models configured only in Settings.

``configured_embed_model()`` reads the process env only, so an embedding model set
only in Settings / ``.env`` (the on-prem ``ONPREM_EMBEDDING_MODEL`` behind
``ONPREM_EMBEDDING_BASE_URL``, ``NVIDIA_EMBED_MODEL``) never reached the Model
Registry. Capabilities are MERGED with an entry the same model already has.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_router.models import ModelCapability
from app.ai_router.registry import ModelRegistry
from app.ai_router.seeder import seed_registry_from_config
from app.core.config import Settings

_ISOLATE_PROVIDER_ENV = True


def _use_settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    import app.core.config as config

    settings = Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    for name in ("EMBEDDING_MODEL", "NVIDIA_EMBED_MODEL", "ONPREM_QWEN_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _caps(reg: ModelRegistry, provider: str, model_id: str) -> list[ModelCapability]:
    entry = reg.get_configured(provider, model_id)
    assert entry is not None, f"{provider}/{model_id} was not seeded"
    return list(entry.capabilities)


def test_settings_only_onprem_embedding_model_is_seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(
        monkeypatch,
        onprem_enabled=True,
        onprem_qwen_base_url="",
        onprem_embedding_base_url="http://10.0.0.5:30082/v1",
        onprem_embedding_model="acme/embed-x",
    )
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    assert ModelCapability.EMBEDDING in _caps(reg, "onprem", "acme/embed-x")


def test_settings_only_nvidia_embedding_model_is_seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(
        monkeypatch, nvidia_api_key="nvapi-test", nvidia_embed_model="nvidia/nemotron-3-embed-1b"
    )
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    assert ModelCapability.EMBEDDING in _caps(reg, "nvidia", "nvidia/nemotron-3-embed-1b")


def test_onprem_embedding_off_seeds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(
        monkeypatch,
        onprem_enabled=False,
        onprem_embedding_base_url="http://10.0.0.5:30082/v1",
        onprem_embedding_model="acme/embed-x",
    )
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    assert reg.get_configured("onprem", "acme/embed-x") is None


def test_capabilities_are_merged_never_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same on-prem model serving chat AND embeddings keeps both."""
    _use_settings(
        monkeypatch,
        onprem_enabled=True,
        onprem_qwen_base_url="http://10.0.0.5:30080/v1",
        onprem_qwen_model="acme/dual",
        onprem_embedding_base_url="http://10.0.0.5:30082/v1",
        onprem_embedding_model="acme/dual",
    )
    monkeypatch.setenv("ONPREM_QWEN_MODEL", "acme/dual")
    import app.ai_router.seeder as seeder

    monkeypatch.setattr(seeder, "_reasoning_model_ids", lambda: ["acme/dual"])
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    caps = _caps(reg, "onprem", "acme/dual")
    assert ModelCapability.TEXT_GENERATION in caps
    assert ModelCapability.EMBEDDING in caps
