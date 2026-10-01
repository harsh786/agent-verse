"""PROV-17: a configured-model override POSTed to one replica reaches the others.

``_lazy_seeded`` seeded the registry once per process, so an override saved via
one API replica was invisible on every other replica / worker until restart.
The shared store now carries a version counter; selection re-seeds when it moves.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_router import registry_store, selection
from app.ai_router.models import ModelCapability
from app.ai_router.registry import ModelRegistry


class _SyncRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def incr(self, key: str) -> int:
        self.data[key] = str(int(self.data.get(key, "0")) + 1)
        return int(self.data[key])


@pytest.fixture
def shared(monkeypatch: pytest.MonkeyPatch) -> Any:
    redis = _SyncRedis()
    store = registry_store.ModelRegistryStore(redis)
    registry_store.set_model_registry_store(store)
    monkeypatch.setattr(selection, "_VERSION_CHECK_INTERVAL_S", 0.0)
    return store


def _replica(monkeypatch: pytest.MonkeyPatch) -> tuple[ModelRegistry, Any]:
    """A fresh process-global registry + selection state (another replica)."""
    reg = ModelRegistry()
    monkeypatch.setattr(selection, "model_registry", reg)
    import app.ai_router.seeder as seeder

    monkeypatch.setattr(seeder, "model_registry", reg, raising=False)
    monkeypatch.setattr(selection, "_lazy_seeded", False)
    monkeypatch.setattr(selection, "_seeded_version", None)
    return reg, selection


def test_store_bumps_a_version_on_every_change(shared: Any) -> None:
    v0 = shared.version()
    shared.upsert({"provider": "nvidia", "model_id": "m-new", "capabilities": ["text_generation"]})
    v1 = shared.version()
    shared.remove("nvidia", "m-new")
    assert v0 < v1 < shared.version()


def test_override_on_one_replica_is_seen_by_another(
    shared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg_b, sel = _replica(monkeypatch)
    sel._ensure_seeded(reg_b)  # replica B seeds at startup, before the override
    assert not any(m.model_id == "ui-model" for m in reg_b.list_configured())

    # Replica A (the API that received POST /models/configured) writes the override.
    shared.upsert(
        {
            "provider": "nvidia",
            "model_id": "ui-model",
            "display_name": "UI model",
            "capabilities": [ModelCapability.TEXT_GENERATION.value],
        }
    )

    sel._ensure_seeded(reg_b)  # B's next selection notices the version change
    assert any(m.model_id == "ui-model" for m in reg_b.list_configured())


def test_unchanged_version_does_not_reseed(shared: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    reg_b, sel = _replica(monkeypatch)
    calls: list[int] = []
    import app.ai_router.seeder as seeder

    real = seeder.seed_registry_from_config

    def _counting(registry: Any = None) -> int:
        calls.append(1)
        return real(registry)

    monkeypatch.setattr(seeder, "seed_registry_from_config", _counting)
    sel._ensure_seeded(reg_b)
    sel._ensure_seeded(reg_b)
    assert len(calls) == 1
