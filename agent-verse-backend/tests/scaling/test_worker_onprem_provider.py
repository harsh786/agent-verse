"""The Celery worker must run goals on the SAME hybrid provider + role map as the API.

Regression: the API process builds its provider with ``build_onprem_provider``
(a ``MultiEndpointLLMProvider`` fronting NVIDIA + on-prem Qwen) and GoalService
pins a per-goal role map (planning -> NVIDIA, execution/verification -> Qwen).
``run_goal`` in the worker only consulted the tenant Redis config and the env
registry, so a worker-executed goal never got the multi-endpoint provider nor the
role map — hybrid routing silently did not apply to queued goals.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.observability.traced_provider import TracedProvider

from app.providers.onprem import MultiEndpointLLMProvider
from app.providers.openai_compatible import OpenAICompatibleProvider

pytestmark = pytest.mark.usefixtures("readable_emergency_stop")

_NVIDIA = "nvidia/test-top-model"
_QWEN = "Qwen/test-qwen"


class _State:
    class Status:
        value = "complete"

    status = Status()
    iterations = 1


def _hybrid_provider() -> MultiEndpointLLMProvider:
    return MultiEndpointLLMProvider(
        endpoints={
            _NVIDIA: OpenAICompatibleProvider(
                api_key="x", base_url="http://nvidia.invalid/v1", default_model=_NVIDIA
            ),
            _QWEN: OpenAICompatibleProvider(
                api_key="x", base_url="http://qwen.invalid/v1", default_model=_QWEN
            ),
        },
        default_model=_QWEN,
        provider_type="hybrid",
        fallback_model=_NVIDIA,
    )


@pytest.fixture
def hybrid_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    import app.providers.onprem as onprem_mod
    from app.scaling import tasks

    monkeypatch.setenv("ONPREM_ENABLED", "true")
    monkeypatch.setenv("ONPREM_QWEN_BASE_URL", "http://qwen.invalid/v1")
    monkeypatch.setenv("ONPREM_QWEN_MODEL", _QWEN)
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test")
    monkeypatch.setenv("NVIDIA_MODEL", _NVIDIA)
    for role in ("PLANNING", "EXECUTION", "VERIFICATION"):
        monkeypatch.delenv(f"DEFAULT_{role}_MODEL", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    calls: list[int] = []
    shared = _hybrid_provider()

    def _fake_builder(settings: Any) -> MultiEndpointLLMProvider:
        calls.append(1)
        return shared

    monkeypatch.setattr(onprem_mod, "build_onprem_provider", _fake_builder)
    from app.core.config import get_settings

    get_settings.cache_clear()  # settings are lru-cached; pick up the env above
    tasks._reset_worker_deployment_provider()
    yield calls
    monkeypatch.undo()  # restore env BEFORE dropping the caches built from it
    get_settings.cache_clear()
    tasks._reset_worker_deployment_provider()


def _capture_graph(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import app.agent.graph as graph_mod

    captured: list[dict[str, Any]] = []

    class _CapturingGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

        async def run(self, **kwargs: Any) -> _State:
            return _State()

    monkeypatch.setattr(graph_mod, "AgentGraph", _CapturingGraph)
    return captured


def _raw(provider: object) -> object:
    """The provider inside a TracedProvider (PROV-23 wraps worker role providers)
    and the per-model dispatch wrapper (MR-4) around the deployment provider."""
    inner = getattr(provider, "_inner", provider)
    return getattr(inner, "inner", inner)


def test_worker_agent_runner_gets_multi_endpoint_provider_and_role_map(
    monkeypatch: pytest.MonkeyPatch, hybrid_env: list[int]
) -> None:
    from app.scaling import tasks

    captured = _capture_graph(monkeypatch)

    for n in range(2):
        tasks.run_goal.run(f"goal-onprem-{n}", "tenant-1", "hybrid goal", "normal", False)

    assert len(captured) == 2
    kw = captured[0]
    # PROV-23: worker role providers are traced (GenAI spans); the provider inside
    # is the multi-endpoint dispatcher.
    assert isinstance(kw["planner"], TracedProvider)
    assert isinstance(_raw(kw["planner"]), MultiEndpointLLMProvider)
    assert isinstance(_raw(kw["executor"]), MultiEndpointLLMProvider)
    # The verifier must be able to serve the role map's verification model.
    assert isinstance(_raw(kw["verifier"]), MultiEndpointLLMProvider)
    router = kw["model_router"]
    assert router is not None
    assert router.role_map == {
        "planning": _NVIDIA,
        "execution": _QWEN,
        "verification": _QWEN,
    }
    assert router.model_for("planning") == _NVIDIA
    assert router.model_for("execution") == _QWEN
    assert router.model_for("verification") == _QWEN
    # Built once per worker process and reused across goals.
    assert hybrid_env == [1]
    assert _raw(captured[1]["planner"]) is _raw(kw["planner"])


def test_tenant_configured_provider_still_wins_over_deployment_cluster(
    monkeypatch: pytest.MonkeyPatch, hybrid_env: list[int]
) -> None:
    """A tenant's own provider keeps precedence (same as the API) and gets no role map."""
    from app.scaling import tasks

    tenant_provider = OpenAICompatibleProvider(
        api_key="t", base_url="http://tenant.invalid/v1", default_model="tenant-model"
    )
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: tenant_provider)
    captured = _capture_graph(monkeypatch)

    tasks.run_goal.run("goal-tenant-prov", "tenant-1", "tenant goal", "normal", False)

    assert captured and _raw(captured[0]["planner"]) is tenant_provider
    router = captured[0]["model_router"]
    assert router is None or router.role_map == {}
