"""Workers build the same registry-backed LLM provider as the API.

Goal, sub-goal and scheduled goals all execute in ``run_goal``; with no env LLM
key and models only in the Model Registry the worker used to run them on the
canned FakeProvider — in every environment, because the "unconfigured"
stand-in is a FakeProvider subclass.
"""

# Isolate ambient provider/model env (keys) so only the registry configures LLMs.
_ISOLATE_PROVIDER_ENV = True

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.ai_router.registry import model_registry
from tests.providers._registry_llm_server import (
    LocalLLMServer,
    find_registry,
    shared_store_with_registry_model,
)

pytestmark = pytest.mark.usefixtures("readable_emergency_stop")


@pytest.fixture
def llm_server() -> Iterator[LocalLLMServer]:
    server = LocalLLMServer().start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.registry_store as store_mod
    import app.ai_router.selection as sel
    from app.providers import registry_llm
    from app.scaling import tasks

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(store_mod, "_store", None)
    monkeypatch.setattr(tasks, "_wire_worker_model_registry_store", lambda: None)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    model_registry.clear_configured()
    registry_llm.reset_shared_registry_provider()
    tasks._reset_worker_deployment_provider()
    yield
    model_registry.clear_configured()
    registry_llm.reset_shared_registry_provider()
    tasks._reset_worker_deployment_provider()


def test_worker_goal_builds_the_same_registry_provider(
    llm_server: LocalLLMServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_goal (goal / sub-goal / scheduled goals all run here) gets the registry."""
    import app.agent.graph as graph_mod
    from app.providers.registry_llm import RegistryLLMProvider
    from app.scaling import tasks

    captured: dict[str, Any] = {}

    class _CapturingGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            raise RuntimeError("stop after assembly")

    # The operator's model lives in the SHARED registry store: the worker
    # re-seeds from it per goal.
    import app.ai_router.registry_store as store_mod
    from app.ai_router.seeder import seed_registry_from_config

    monkeypatch.setattr(store_mod, "_store", shared_store_with_registry_model(llm_server.url))
    seed_registry_from_config()
    monkeypatch.setattr(graph_mod, "AgentGraph", _CapturingGraph)
    tasks.run_goal.run(f"goal-reg-{uuid.uuid4().hex}", "tenant-1", "say hello", "normal",
                       False)

    assert isinstance(find_registry(captured["planner"]), RegistryLLMProvider)
    captured.clear()
    tasks.run_goal.run(f"goal-sub-{uuid.uuid4().hex}", "tenant-1", "child step", "normal",
                       False, subgoal=True)
    assert isinstance(find_registry(captured["planner"]), RegistryLLMProvider)
    assert isinstance(tasks._worker_llm_provider(), RegistryLLMProvider)
    assert isinstance(tasks._ai_ops_worker_platform_provider(), RegistryLLMProvider)


def test_production_worker_goal_with_nothing_configured_fails_not_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    monkeypatch.setenv("ENVIRONMENT", "production")
    result = tasks.run_goal.run(f"goal-none-{uuid.uuid4().hex}", "tenant-1", "say hello",
                                "normal", False)
    assert result["status"] == "failed"
    assert result["reason"] == "no_llm_provider_configured"
    assert "Model Registry" in result["message"]
