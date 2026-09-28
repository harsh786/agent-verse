"""Regression: the simulation sandbox never saw the app's LLM provider.

``SimulationRunner._provider`` was never set and ``POST /enterprise/simulation``
passed no ``app_state``, so every simulation silently ran the keyword stub even
with a real LLM configured; and a full-pipeline run was reported ``complete`` /
``success`` whatever the graph's outcome.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.enterprise.simulation import SimulationRunner
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-sim", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _RealLLM:
    async def complete(self, req: Any) -> Any:  # pragma: no cover - not reached
        raise AssertionError


def test_set_provider_binds_real_provider_but_not_fake() -> None:
    r = SimulationRunner()
    r.set_provider(FakeProvider())
    assert r._provider is None
    llm = _RealLLM()
    r.set_provider(llm)
    assert r._provider is llm


def test_create_app_binds_the_app_provider() -> None:
    from app.main import create_app

    app = create_app()
    runner = app.state.simulation_runner
    app_provider = app.state._app_provider
    expected = None if isinstance(app_provider, FakeProvider) else app_provider
    assert runner._provider is expected


class _Graph:
    status = "failed"

    def __init__(self, **_: Any) -> None:
        pass

    async def run(self, **_: Any) -> Any:
        return SimpleNamespace(status="failed", steps=[], error_message="planner blew up")


@pytest.mark.asyncio
async def test_full_pipeline_failure_is_not_reported_as_success() -> None:
    r = SimulationRunner()
    with patch("app.agent.graph.AgentGraph", _Graph):
        run = await r.start(goal="g", tenant_ctx=_CTX, provider=_RealLLM())
    assert run.status == "failed"
    assert run.result["status"] == "failed"
    assert run.result["outcome"] == "failed (simulated)"
    assert run.result["error"] == "planner blew up"


@pytest.mark.asyncio
async def test_app_state_fake_provider_runs_the_labelled_stub() -> None:
    r = SimulationRunner()
    state = SimpleNamespace(_app_provider=FakeProvider())
    run = await r.start(goal="search", tenant_ctx=_CTX, app_state=state)
    assert run.used_real_llm is False
