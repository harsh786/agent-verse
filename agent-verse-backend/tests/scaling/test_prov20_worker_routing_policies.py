"""PROV-20: workers apply tenant routing policies and never drop a goal override.

The worker read tenant routing policies BEFORE wiring the Redis
ModelRegistryStore (first goal per process saw an empty local dict); the read
fell back to the local copy on store errors; and a failed goal ``model_override``
DB lookup logged and returned "" so the goal ran on another model.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_router import registry_store
from app.ai_router.models import TaskType


@pytest.fixture
def no_store() -> Any:
    saved = registry_store.get_model_registry_store()
    registry_store._store = None  # type: ignore[attr-defined]
    yield
    registry_store._store = saved  # type: ignore[attr-defined]


class _SyncRedis:
    def __init__(self, data: dict[str, str] | None = None, *, broken: bool = False) -> None:
        self.data, self.broken = dict(data or {}), broken

    def get(self, key: str) -> str | None:
        if self.broken:
            raise ConnectionError("redis down")
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value


def test_worker_init_wires_the_model_registry_store(
    no_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import redis as sync_redis

    from app.scaling import tasks

    monkeypatch.setattr(tasks, "REDIS_URL", "redis://example:6379/0", raising=False)
    monkeypatch.setattr(
        tasks, "_wire_worker_model_registry_store", tasks._connect_model_registry_store
    )
    monkeypatch.setattr(sync_redis, "from_url", lambda *a, **k: _SyncRedis())
    monkeypatch.setattr(
        "app.scaling.worker_cost.install_worker_cost_services", lambda: None
    )
    tasks._setup_worker_checkpointer()
    assert registry_store.get_model_registry_store() is not None


def test_first_goal_in_a_fresh_worker_sees_the_tenant_policy(no_store: Any) -> None:
    import json

    from app.ai_router.registry import tenant_policy_role_models

    redis = _SyncRedis(
        {
            "model_registry:route_policies:t1": json.dumps(
                {"planning": {"routing_mode": "model_pinned", "preferred_model": "pinned-m"}}
            )
        }
    )
    registry_store.set_model_registry_store(registry_store.ModelRegistryStore(redis))
    assert tenant_policy_role_models("t1") == {"planning": "pinned-m"}


def test_strict_policy_read_raises_on_store_errors(no_store: Any) -> None:
    from app.ai_router.registry import ModelRegistry

    registry_store.set_model_registry_store(
        registry_store.ModelRegistryStore(_SyncRedis(broken=True))
    )
    reg = ModelRegistry()
    with pytest.raises(ConnectionError):
        reg.get_route_policy("t1", TaskType.PLANNING, strict=True)
    assert reg.get_route_policy("t1", TaskType.PLANNING) is None  # API read path degrades


def test_worker_policy_roles_fail_closed(no_store: Any) -> None:
    from app.scaling.tasks import _worker_tenant_policy_roles

    registry_store.set_model_registry_store(
        registry_store.ModelRegistryStore(_SyncRedis(broken=True))
    )
    with pytest.raises(RuntimeError, match="routing policies"):
        _worker_tenant_policy_roles("t1", None)


async def test_goal_override_lookup_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.db.session as session_mod
    from app.scaling.tasks import _read_goal_model_override

    def _broken() -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(session_mod, "get_session_factory", _broken)
    with pytest.raises(RuntimeError, match="model_override"):
        await _read_goal_model_override("g1", "t1")
