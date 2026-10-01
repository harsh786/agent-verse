"""PROV-22: health circuits half-open after a cool-down and are shared across replicas.

ProviderHealthPolicy closed a circuit only on record_success, so a provider that
was never selected again (because its circuit was open) stayed open forever;
and the state lived in process memory, so replicas disagreed.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_router import registry_store
from app.ai_router.provider_health_policy import ProviderHealthPolicy


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _open(policy: ProviderHealthPolicy, provider: str = "openai") -> None:
    for _ in range(5):
        policy.record_failure(provider)
    assert policy.check(provider).circuit_open


@pytest.fixture
def no_store() -> Any:
    saved = registry_store.get_model_registry_store()
    registry_store._store = None  # type: ignore[attr-defined]
    yield
    registry_store._store = saved  # type: ignore[attr-defined]


def test_cool_down_allows_one_probe_then_success_closes(no_store: Any) -> None:
    clock = _Clock()
    policy = ProviderHealthPolicy(cooldown_seconds=60, clock=clock)
    _open(policy)

    clock.now += 30
    assert policy.check("openai").circuit_open  # still cooling down

    clock.now += 31
    assert policy.check("openai").circuit_open is False  # half-open: one probe allowed
    assert policy.check("openai").circuit_open is True  # only one probe in flight

    policy.record_success("openai", 120.0)
    status = policy.check("openai")
    assert status.circuit_open is False and status.healthy is True


def test_failed_probe_reopens_for_another_cool_down(no_store: Any) -> None:
    clock = _Clock()
    policy = ProviderHealthPolicy(cooldown_seconds=60, clock=clock)
    _open(policy)
    clock.now += 61
    assert policy.check("openai").circuit_open is False
    policy.record_failure("openai")
    assert policy.check("openai").circuit_open is True
    clock.now += 61
    assert policy.check("openai").circuit_open is False  # next probe after cool-down


class _SyncRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value


def test_replicas_share_circuit_state_through_the_store(no_store: Any) -> None:
    registry_store.set_model_registry_store(registry_store.ModelRegistryStore(_SyncRedis()))
    clock = _Clock()
    replica_a = ProviderHealthPolicy(cooldown_seconds=60, clock=clock)
    replica_b = ProviderHealthPolicy(cooldown_seconds=60, clock=clock)
    _open(replica_a, "anthropic")
    assert replica_b.check("anthropic").circuit_open is True
