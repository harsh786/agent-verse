"""Regression: worker-run goals ignored the agent's pinned model (model_override).

Only the API path applied it (GoalService -> ModelRouter.with_override); the
Celery worker's agent lookup didn't even select the column.
"""

from __future__ import annotations

import inspect


def test_worker_reads_and_applies_the_agent_model_override() -> None:
    from app.scaling import tasks

    src = inspect.getsource(tasks.run_goal)
    assert "model_override FROM agents" in src
    assert "with_override(_effective_override)" in src
    assert "_effective_override = _goal_level_override or _agent_model_override" in src


def test_with_override_pins_every_role() -> None:
    from app.agent.model_router import ModelRouter

    router = ModelRouter().with_override("pinned-model")
    for task in ("planning", "execution", "verification"):
        assert router.model_for(task) == "pinned-model"


def _api_loop(execution_context: dict | None):  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id="mo-t", plan=PlanTier.ENTERPRISE, api_key_id="k")
    app_state = MagicMock()
    for name in (
        "audit_log", "cost_controller", "redis_cost_controller", "hitl_gateway",
        "knowledge_store", "long_term_memory", "eval_runner", "policy_engine",
        "permission_matrix",
    ):
        setattr(app_state, name, None)
    app_state._llm_configs = {}
    svc = GoalService()
    svc._app_state = app_state
    return svc._make_agent_loop_for_tenant(
        ctx, app_state, execution_context=execution_context
    )


def test_api_goal_level_model_override_pins_every_role() -> None:
    """POST /goals model_override was written to execution_context and never read."""
    loop = _api_loop({"model_override": "goal-pinned-model"})
    router = loop._model_router
    assert router is not None
    for task in ("planning", "execution", "verification"):
        assert router.model_for(task) == "goal-pinned-model"


def test_api_without_goal_override_is_not_pinned() -> None:
    loop = _api_loop({})
    router = loop._model_router
    if router is not None:
        assert router.model_for("planning") != "goal-pinned-model"


async def test_worker_goal_model_override_lookup_reads_execution_context() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.scaling import tasks

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar=MagicMock(return_value="worker-goal-model"))
    )

    class _Ctx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return session

        async def __aexit__(self, *a: object) -> None:
            return None

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: _Ctx()),
        patch("app.db.rls.sqlalchemy_rls_context", return_value=_Ctx()),
    ):
        value = await tasks._goal_model_override("g1", "t1")
    assert value == "worker-goal-model"
    sql = str(session.execute.await_args.args[0])
    assert "model_override" in sql and "execution_context" in sql
