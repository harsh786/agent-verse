"""Strategy readiness is probed against what the app actually wires.

Regression: ``ReadinessEvaluator()`` in create_app had no ``register()`` calls, so every
strategy in production reported ``missing_probe:*`` for every dependency.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.observability.health import HealthCheck, HealthRegistry
from app.orchestration.strategy_probes import (
    build_strategy_probes,
    register_strategy_readiness_probes,
)
from app.orchestration.strategy_readiness import ReadinessEvaluator
from app.orchestration.strategy_registry import build_default_registry
from app.providers.fake import FakeProvider


def test_every_declared_dependency_has_a_probe() -> None:
    probes = build_strategy_probes(SimpleNamespace())
    declared = {
        dependency
        for capability in build_default_registry().list_all()
        for dependency in capability.readiness_requirements
        if dependency != "registry_contract"
    }
    assert declared - probes.keys() == set()


class _Runner:
    has_real_executor = True


class InMemoryFakeRepo:
    pass


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("refused")


def _evaluator(state: Any) -> ReadinessEvaluator:
    evaluator = ReadinessEvaluator()
    register_strategy_readiness_probes(evaluator, state)
    return evaluator


async def test_react_is_ready_when_its_runtime_is_wired() -> None:
    state = SimpleNamespace(strategy_runner=_Runner(), _app_provider=object())
    react = build_default_registry().resolve("react").capability
    decision = await _evaluator(state).evaluate(react, production=True)
    assert decision.ready is True
    assert decision.blocking_reasons == ()


async def test_unwired_runtime_blocks_with_stable_reason_codes() -> None:
    react = build_default_registry().resolve("react").capability
    decision = await _evaluator(SimpleNamespace()).evaluate(react, production=True)
    assert decision.ready is False
    assert decision.blocking_reasons == ("strategy_runner:not_wired",)


async def test_rag_readiness_follows_the_database_health_check() -> None:
    hybrid = build_default_registry().resolve("hybrid").capability
    assert "database" in hybrid.readiness_requirements
    up = SimpleNamespace(
        strategy_runner=_Runner(),
        embedder=object(),
        health=HealthRegistry([HealthCheck("postgres", _ok)]),
    )
    down = SimpleNamespace(
        strategy_runner=_Runner(),
        embedder=object(),
        health=HealthRegistry([HealthCheck("postgres", _down)]),
    )
    assert "database:unreachable" not in (await _evaluator(up).evaluate(hybrid)).blocking_reasons
    assert "database:unreachable" in (await _evaluator(down).evaluate(hybrid)).blocking_reasons


async def test_in_memory_and_simulated_dependencies_are_degraded_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    probes = build_strategy_probes(
        SimpleNamespace(
            _app_provider=FakeProvider(),
            moa_repository=InMemoryFakeRepo(),
            langgraph_checkpointer=None,
        )
    )
    assert probes["provider"]().reason == "simulated_provider"  # type: ignore[union-attr]
    assert probes["moa_repository"]().is_degraded  # type: ignore[union-attr]
    assert probes["checkpoint_store"]().is_degraded  # type: ignore[union-attr]
    assert not probes["code_interpreter"]().available  # type: ignore[union-attr]

    monkeypatch.setenv("ENVIRONMENT", "production")
    assert not probes["provider"]().available  # type: ignore[union-attr]


def test_create_app_registers_the_probes() -> None:
    from app.main import create_app

    app = create_app()
    probes = app.state.strategy_readiness._probes
    assert {"strategy_runner", "provider", "database", "embedder", "checkpoint_store"} <= set(
        probes
    )
