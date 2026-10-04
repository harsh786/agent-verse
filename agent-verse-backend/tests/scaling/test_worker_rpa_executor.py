"""Regression: the Celery worker's AgentGraph never had an RPA executor.

Only the in-process path (goal_service) set ``graph._rpa_executor``; the worker —
which runs every queued (production) goal — built its graph and ``_app_state``
without one, so an agent's ``rpa_open_url`` call on a queued goal fell through to
"Tool not found". The worker now builds the same RPAExecutor the API lifespan
builds, attaches it to the graph and ``_app_state``, and closes its browser
sessions when the run ends.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent.state import AgentState, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.rpa.executor import RPAExecutor, RPAResult
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="tenant-rpa-w", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _State:
    class Status:
        value = "complete"

    status = Status()
    iterations = 1


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    import app.rpa.executor as rpa_exec_mod
    from app.scaling import tasks

    seen: dict[str, Any] = {"outputs": [], "graphs": []}
    real_graph_cls = graph_mod.AgentGraph

    # The mock executor stands in for Playwright; everything else is the real
    # worker assembly and the real AgentGraph executor node.
    rpa = RPAExecutor()
    rpa.execute = AsyncMock(  # type: ignore[method-assign]
        return_value=RPAResult(success=True, output="Navigated to https://example.com")
    )
    rpa.aclose = AsyncMock()  # type: ignore[method-assign]
    seen["rpa"] = rpa
    built: list[dict[str, Any]] = []

    def _build(**kwargs: Any) -> RPAExecutor:
        built.append(kwargs)
        return rpa

    seen["built"] = built
    monkeypatch.setattr(rpa_exec_mod, "build_rpa_executor", _build, raising=False)

    class _Graph(real_graph_cls):  # type: ignore[misc, valid-type]
        async def run(self, **kwargs: Any) -> Any:
            seen["graphs"].append(self)
            seen["initial_context"] = kwargs.get("initial_context") or {}
            # The planned step calls rpa_open_url; drive the real executor node.
            self._executor = FakeProvider(
                responses=['{"tool": "rpa_open_url", "arguments": {"url": "https://example.com"}}']
            )
            self._policy_engine = None  # governance is covered elsewhere
            self._enforce_grants = False
            # Budgets are covered elsewhere; the worker's Redis-backed controller
            # has no Redis here and would (correctly) fail closed.
            self._cost_controller = None
            state = AgentState(goal="open it", tenant_ctx=T)
            state.steps.append(StepResult(description="open the page", status=StepStatus.RUNNING))
            seen["outputs"].append(await self._execute_step("open the page", state, T))
            seen["aclose_calls_during_run"] = rpa.aclose.await_count
            return _State()

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    # POL-01: every tool call evaluates the tenant's policy-as-code rules, and an
    # unloadable rule set (no DB here) fails closed. This tenant has no rules.
    monkeypatch.setattr(
        "app.governance.policy_rules.load_active_policy_rules", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    return seen


def test_worker_goal_rpa_open_url_dispatches_to_the_rpa_executor(
    worker: dict[str, Any],
) -> None:
    from app.scaling import tasks

    result = tasks.run_goal.run("g-rpa-w1", T.tenant_id, "open example.com", "normal", False)

    assert result["status"] == "complete"
    assert worker["graphs"], "the worker never ran the AgentGraph"
    graph = worker["graphs"][0]
    rpa = worker["rpa"]
    assert graph._rpa_executor is rpa
    assert graph._app_state.rpa_executor is rpa
    rpa.execute.assert_awaited_once()
    call = rpa.execute.await_args.kwargs
    assert call["tool_name"] == "rpa_open_url"
    assert call["tenant_id"] == T.tenant_id
    output = worker["outputs"][0]
    assert "Tool not found" not in output
    assert output == "Navigated to https://example.com"


def test_worker_tool_context_offers_the_rpa_tools(worker: dict[str, Any]) -> None:
    """RPA-01: queued goals plan with the same rpa_* ToolRefs as in-process goals."""
    from app.rpa.tools import RPA_TOOLS
    from app.scaling import tasks

    tasks.run_goal.run("g-rpa-w0", T.tenant_id, "open example.com", "normal", False)
    tool_context = worker["initial_context"].get("tool_context")
    assert tool_context is not None, "the worker built no tool context"
    rpa_refs = {t.name for t in tool_context.tools if t.server_id == "rpa"}
    assert rpa_refs == {str(t["name"]) for t in RPA_TOOLS}
    assert "rpa_open_url" in tool_context.to_prompt_block()


def test_worker_closes_rpa_sessions_when_the_run_ends(worker: dict[str, Any]) -> None:
    from app.scaling import tasks

    tasks.run_goal.run("g-rpa-w2", T.tenant_id, "open example.com", "normal", False)

    rpa = worker["rpa"]
    assert worker["aclose_calls_during_run"] == 0
    rpa.aclose.assert_awaited_once()
    # The worker resolves vault:// refs through a tenant-aware secret store.
    assert rpa._secret_store_resolver is not None


def test_worker_builds_rpa_executor_like_the_api(worker: dict[str, Any]) -> None:
    from app.scaling import tasks

    tasks.run_goal.run("g-rpa-w3", T.tenant_id, "open example.com", "normal", False)
    assert len(worker["built"]) == 1
    assert "vision_provider" in worker["built"][0]


def test_build_rpa_executor_reads_allowlist_and_wires_session_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rpa.executor import build_rpa_executor
    from app.rpa.session_manager import BrowserSessionManager

    monkeypatch.setenv("RPA_SSRF_ALLOWED_DOMAINS", " staging.internal , ,intra.example ")
    ex = build_rpa_executor()
    assert ex._allowed_domains == ["staging.internal", "intra.example"]
    assert isinstance(ex._session_manager, BrowserSessionManager)
    assert ex._artifact_store is not None

    monkeypatch.setenv("RPA_SSRF_ALLOWED_DOMAINS", "")
    assert build_rpa_executor()._allowed_domains is None


@pytest.mark.asyncio
async def test_rpa_executor_aclose_closes_every_live_session() -> None:
    from app.rpa.session_manager import BrowserSession, BrowserSessionManager

    mgr = BrowserSessionManager()
    closed: list[str] = []
    for sid in ("s1", "s2"):
        sess = BrowserSession(session_id=sid, tenant_id="t")
        sess.close = AsyncMock(side_effect=lambda sid=sid: closed.append(sid))  # type: ignore[method-assign]
        mgr._sessions[(sid, "t")] = sess
    ex = RPAExecutor(session_manager=mgr)
    ex._http_pages["s1"] = "cached"

    await ex.aclose()

    assert sorted(closed) == ["s1", "s2"]
    assert mgr._sessions == {}
    assert ex._http_pages == {}
