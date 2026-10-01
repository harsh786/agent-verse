"""MOA-DIVERSITY: single-model personas are labelled; several models are used distinctly."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from structlog.testing import capture_logs

from tests.coordination.pattern_run_support import (
    TENANT,
    ScriptedProvider,
    active_session,
    pattern_state,
    service,
)


class NamedProvider(ScriptedProvider):
    def __init__(self, model: str) -> None:
        super().__init__()
        self.default_model = model
        self.models_requested: list[str] = []

    async def complete(self, request: Any) -> Any:
        self.models_requested.append(request.model)
        return await super().complete(request)


async def _run_moa(state: Any, session_id: str, **options: Any) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    async with state.coordination_live_bus.subscribe("tenant", session_id) as frames:
        result = await service(state).run(
            TENANT,
            session_id,
            "mixture_of_agents",
            objective="Compare the three vendors",
            participants=(),
            max_rounds=3,
            options=options,
            idempotency_key="moa",
        )
        while True:
            try:
                events.append(await asyncio.wait_for(anext(frames), 0.05))
            except TimeoutError:
                break
    result["_events"] = events
    return result


@pytest.mark.asyncio
async def test_single_model_is_labelled_personas_and_warned() -> None:
    state = pattern_state(NamedProvider("only-model"))
    session_id = await active_session(state)
    with capture_logs() as logs:
        result = await _run_moa(state, session_id)
    assert result["phase"] == "completed"
    assert result["view"]["diversity"] == "single_model_personas"
    diversity_events = [
        frame
        for frame in result["_events"]
        if frame.get("event_type") == "pattern_run.moa_diversity.v1"
    ]
    assert diversity_events[0]["payload"]["diversity"] == "single_model_personas"
    warnings = [log for log in logs if log["event"] == "moa_single_model_personas"]
    assert warnings and warnings[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_configured_providers_give_each_proposer_a_distinct_model() -> None:
    primary = NamedProvider("model-a")
    second = NamedProvider("model-b")
    third = NamedProvider("model-c")
    state = pattern_state(primary)
    state.moa_providers = [primary, second, third]
    session_id = await active_session(state)
    result = await _run_moa(state, session_id)
    assert result["phase"] == "completed"
    assert result["view"]["diversity"] == "multi_model"
    assert sorted(result["view"]["proposer_models"].values()) == ["model-a", "model-b", "model-c"]
    # Each proposer's calls went to its own provider with its own model.
    assert "model-b" in second.models_requested and "model-c" in third.models_requested
    layers = await state.moa_repository.layers("tenant", session_id)
    proposals = await state.moa_repository.proposals("tenant", layers[0].strategy_execution_id, 0)
    assert {item.model_family for item in proposals} == {"model-a", "model-b", "model-c"}


@pytest.mark.asyncio
async def test_proposer_model_list_is_configurable_per_run() -> None:
    primary = NamedProvider("router-default")
    state = pattern_state(primary)
    session_id = await active_session(state)
    result = await _run_moa(state, session_id, proposer_models=["m-1", "m-2", "m-3"])
    assert result["view"]["diversity"] == "multi_model"
    assert {"m-1", "m-2", "m-3"} <= set(primary.models_requested)


@pytest.mark.asyncio
async def test_operator_default_models_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOA_PROPOSER_MODELS", "env-1, env-2,env-3")
    primary = NamedProvider("router-default")
    state = pattern_state(primary)
    session_id = await active_session(state)
    result = await _run_moa(state, session_id)
    assert result["view"]["diversity"] == "multi_model"
    assert set(result["view"]["proposer_models"].values()) == {"env-1", "env-2", "env-3"}


def test_configured_pool_skips_the_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.registry as registry
    from app.coordination.pattern_runs.moa import configured_provider_pool
    from app.providers.fake import FakeProvider

    real = NamedProvider("real-model")
    configs = [
        registry.ProviderConfig(provider_type="a"),
        registry.ProviderConfig(provider_type="b"),
    ]
    monkeypatch.setattr(registry, "_detect_providers", lambda: configs)
    monkeypatch.setattr(
        registry,
        "_instantiate_provider",
        lambda cfg: real if cfg.provider_type == "a" else FakeProvider(responses=["x"]),
    )
    pool = configured_provider_pool()
    assert pool == [real]
    assert real._agentverse_provider_type == "a"
