"""RV-09: queued single-agent goals get the default-deny permission matrix.

The API path builds the AgentGraph with ``app.state.permission_matrix`` (SAFE-1:
destructive tool globs DENIED unless the tenant has an explicit ALLOW rule).
The Celery worker — which runs every QUEUED (production) goal — built its graph
services without any ``permission_matrix``, so the executor's per-tool
governance check was skipped and a destructive tool was never denied by it.

Pins, through ``run_goal``:
* the graph gets a permission matrix; a destructive tool is DENIED for a tenant
  without an ALLOW rule, and ALLOWED for a tenant holding one;
* a matrix that cannot be built fails the goal (closed), never runs it ungoverned.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.governance.permissions import ActionLevel, PermissionRule
from app.tenancy.context import PlanTier, TenantContext

_DESTRUCTIVE = "github.delete_repository"


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k")


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    seen: dict[str, Any] = {"graph_runs": 0}

    class _State:
        class Status:
            value = "complete"

        status = Status()
        iterations = 1

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            self._pause_gate: Any = None
            seen["graph_kwargs"] = kwargs

        async def run(self, **kwargs: Any) -> Any:
            seen["graph_runs"] += 1
            return _State()

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    return seen


def _run(goal_id: str, tenant_id: str) -> dict[str, Any]:
    from app.scaling import tasks

    result: dict[str, Any] = tasks.run_goal.run(
        goal_id, tenant_id, "clean up the old repositories", "normal", False
    )
    return result


def test_queued_goal_denies_a_destructive_tool_without_a_permission(
    worker: dict[str, Any],
) -> None:
    result = _run("g-rv09-deny", "t-rv09")

    assert result["status"] == "complete", result
    assert worker["graph_runs"] == 1
    matrix = worker["graph_kwargs"].get("permission_matrix")
    assert matrix is not None, "the worker graph must carry the permission matrix"
    assert matrix.check(_DESTRUCTIVE, tenant_ctx=_ctx("t-rv09")) == ActionLevel.DENY
    # Non-destructive tools keep the audited-allow default.
    assert matrix.check("github.list_issues", tenant_ctx=_ctx("t-rv09")) == ActionLevel.ALLOW_LOG


def test_queued_goal_allows_a_destructive_tool_with_a_permission(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.governance.permissions as perms_mod

    real_builder = perms_mod.build_default_permission_matrix

    def _with_tenant_opt_in() -> Any:
        matrix = real_builder()
        matrix.set_rule(
            PermissionRule(tool_name=_DESTRUCTIVE, level=ActionLevel.ALLOW),
            tenant_ctx=_ctx("t-rv09-optin"),
        )
        return matrix

    monkeypatch.setattr(perms_mod, "build_default_permission_matrix", _with_tenant_opt_in)

    result = _run("g-rv09-allow", "t-rv09-optin")

    assert result["status"] == "complete", result
    matrix = worker["graph_kwargs"].get("permission_matrix")
    assert matrix is not None
    assert matrix.check(_DESTRUCTIVE, tenant_ctx=_ctx("t-rv09-optin")) == ActionLevel.ALLOW
    # The opt-in is the tenant's alone.
    assert matrix.check(_DESTRUCTIVE, tenant_ctx=_ctx("t-other")) == ActionLevel.DENY


def test_an_unbuildable_permission_matrix_fails_the_goal_closed(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.governance.permissions as perms_mod

    def _broken() -> Any:
        raise RuntimeError("permission matrix unavailable")

    monkeypatch.setattr(perms_mod, "build_default_permission_matrix", _broken)

    result = _run("g-rv09-broken", "t-rv09")

    assert result["status"] == "failed", result
    assert worker["graph_runs"] == 0, "an ungoverned graph must not run"
