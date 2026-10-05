"""NF-10: run_goal retries only transient infrastructure errors.

It used to retry ANY exception — a programming error (AttributeError/TypeError)
or an error raised after the goal already reached a terminal status was re-run
three times (repeating side effects such as fresh approvals) and then
dead-lettered as "exceeded max retries", hiding the real reason.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
import redis
from sqlalchemy import exc as sa_exc

from app.providers.circuit_breaker import ProviderCircuitOpenError
from app.providers.llm_resolution import NoLLMProviderConfiguredError
from app.scaling.retry_policy import is_transient_infra_error

pytestmark = pytest.mark.usefixtures("readable_emergency_stop")


class _StatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


class APIConnectionError(Exception):  # vendor SDK class, matched by name
    pass


def _wrapped(outer: Exception, cause: BaseException) -> Exception:
    try:
        raise outer from cause
    except Exception as exc:
        return exc


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError("refused"),
        TimeoutError("slow"),
        sa_exc.OperationalError("SELECT 1", {}, Exception("server closed the connection")),
        redis.exceptions.ConnectionError("redis down"),
        redis.exceptions.TimeoutError("redis slow"),
        httpx.ConnectError("connect failed"),
        httpx.ReadTimeout("read timeout"),
        _StatusError(429),
        _StatusError(500),
        _StatusError(503),
        _StatusError(529),
        APIConnectionError("vendor connection"),
        ProviderCircuitOpenError("circuit open"),
        _wrapped(RuntimeError("planner unavailable"), ConnectionResetError("reset")),
    ],
    ids=repr,
)
def test_transient_infrastructure_errors_are_retryable(exc: BaseException) -> None:
    assert is_transient_infra_error(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        AttributeError("'NoneType' object has no attribute 'steps'"),
        TypeError("unsupported operand"),
        KeyError("missing"),
        ValueError("bad value"),
        RuntimeError("tenant routing policies could not be read"),
        NoLLMProviderConfiguredError("no LLM provider configured for tenant"),
        _StatusError(400),
        _StatusError(401),
        _StatusError(404),
        # A bug raised while HANDLING a connection error is still a bug.
        _wrapped(AttributeError("bug"), ConnectionError("x")),
    ],
    ids=repr,
)
def test_programming_and_permanent_errors_are_not_retryable(exc: BaseException) -> None:
    assert is_transient_infra_error(exc) is False


def test_implicit_context_is_not_followed() -> None:
    try:
        try:
            raise ConnectionError("db down")
        except ConnectionError:
            raise RuntimeError("bug in the error handler") from None
    except RuntimeError as exc:
        assert is_transient_infra_error(exc) is False


# ── run_goal ──────────────────────────────────────────────────────────────────


def _raising_graph(exc: BaseException) -> type:
    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> Any:
            raise exc

    return _Graph


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.scaling import tasks
    from app.services.goal_service import GoalService

    rec: dict[str, list[Any]] = {
        "finalize": [],
        "dlq": [],
        "decrement": [],
        "retry": [],
        "status": [],
    }

    async def fake_finalize(goal_id: str, tenant_id: str) -> None:
        rec["finalize"].append(goal_id)

    async def fake_decrement(tenant_id: str, redis_url: str) -> None:
        rec["decrement"].append(tenant_id)

    def fake_retry(*args: Any, **kwargs: Any) -> Any:
        rec["retry"].append(kwargs)
        raise RuntimeError("RETRY-SCHEDULED")

    async def fake_update(
        self: Any, goal_id: str, tenant_id: str, status: str, **kw: Any
    ) -> None:
        rec["status"].append((status, kw.get("error_message", "")))

    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(GoalService, "_db_update_goal_status", fake_update)
    with (
        patch.object(tasks, "_finalize_owning_mission", fake_finalize),
        patch.object(tasks.run_goal_dlq, "delay", lambda **kw: rec["dlq"].append(kw)),
        patch.object(tasks, "_decrement_after_completion", fake_decrement),
        patch.object(tasks.run_goal, "retry", fake_retry),
    ):
        yield rec


def _run(goal_id: str, retries: int = 0) -> Any:
    from app.scaling import tasks

    tasks.run_goal.push_request(retries=retries, called_directly=False)
    try:
        return tasks.run_goal.run(goal_id, "tenant-1", "do the thing")
    finally:
        tasks.run_goal.pop_request()


def test_programming_error_fails_once_without_retry(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]]
) -> None:
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    monkeypatch.setattr(
        _graph_mod, "AgentGraph", _raising_graph(AttributeError("'NoneType' has no 'steps'"))
    )

    async def _active(goal_id: str, tenant_id: str) -> str:
        return "executing"

    monkeypatch.setattr(tasks, "_current_goal_status", _active)

    result = _run("goal-bug-1")

    assert spies["retry"] == []
    assert spies["dlq"] == []
    assert result["status"] == "failed"
    assert result["retryable"] is False
    assert "AttributeError" in result["reason"]
    failed = [m for s, m in spies["status"] if s == "failed"]
    assert failed and "AttributeError" in failed[-1]
    assert "max retries" not in failed[-1]
    assert spies["decrement"] == ["tenant-1"]
    assert spies["finalize"] == ["goal-bug-1"]


def test_transient_error_is_retried(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]]
) -> None:
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    monkeypatch.setattr(
        _graph_mod, "AgentGraph", _raising_graph(redis.exceptions.ConnectionError("down"))
    )

    async def _active(goal_id: str, tenant_id: str) -> str:
        return "executing"

    monkeypatch.setattr(tasks, "_current_goal_status", _active)

    with pytest.raises(RuntimeError, match="RETRY-SCHEDULED"):
        _run("goal-transient-1")

    assert len(spies["retry"]) == 1
    assert [s for s, _ in spies["status"] if s == "failed"] == []
    assert spies["decrement"] == []
    assert spies["finalize"] == []


@pytest.mark.parametrize("terminal", ["complete", "failed", "cancelled", "waiting_human"])
def test_goal_already_terminal_is_never_retried_or_rewritten(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]], terminal: str
) -> None:
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    monkeypatch.setattr(_graph_mod, "AgentGraph", _raising_graph(ConnectionResetError("reset")))

    async def _done(goal_id: str, tenant_id: str) -> str:
        return terminal

    monkeypatch.setattr(tasks, "_current_goal_status", _done)

    result = _run("goal-done-1")

    assert spies["retry"] == []
    assert spies["dlq"] == []
    assert [s for s, _ in spies["status"] if s == "failed"] == []
    assert result["status"] == terminal
    assert result["reason"] == "error_after_terminal_status"
    # The slot is released by whoever finished / suspended it, not again here.
    assert spies["decrement"] == []


def test_error_after_completion_recorded_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]]
) -> None:
    """The goal completed, then post-completion work blew up: no rerun."""
    import app.agent.graph as _graph_mod
    from app.agent.state import AgentState, GoalStatus
    from app.scaling import tasks
    from app.tenancy.context import PlanTier, TenantContext

    class _OkGraph:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> Any:
            st = AgentState(
                goal="do the thing",
                tenant_ctx=TenantContext(tenant_id="tenant-1", plan=PlanTier.FREE,
                                         api_key_id="k"),
                goal_id="goal-post-1",
            )
            st.status = GoalStatus.COMPLETE
            return st

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("learning store down")

    async def _unreadable(goal_id: str, tenant_id: str) -> str:
        raise ConnectionError("db down")

    monkeypatch.setattr(_graph_mod, "AgentGraph", _OkGraph)
    monkeypatch.setattr(tasks, "_learn_from_worker_goal", _boom)
    monkeypatch.setattr(tasks, "_current_goal_status", _unreadable)

    result = _run("goal-post-1")

    assert spies["retry"] == []
    assert spies["dlq"] == []
    assert ("complete", "") in spies["status"]
    assert [s for s, _ in spies["status"] if s == "failed"] == []
    assert result["status"] == "complete"
    assert spies["decrement"] == ["tenant-1"]


def test_transient_error_with_unreadable_status_still_retries(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]]
) -> None:
    """Unknown status + transient error: retry (the next attempt's claim fails closed)."""
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    monkeypatch.setattr(_graph_mod, "AgentGraph", _raising_graph(httpx.ReadTimeout("db slow")))

    async def _unreadable(goal_id: str, tenant_id: str) -> str:
        raise ConnectionError("db down")

    monkeypatch.setattr(tasks, "_current_goal_status", _unreadable)

    with pytest.raises(RuntimeError, match="RETRY-SCHEDULED"):
        _run("goal-transient-2")
    assert len(spies["retry"]) == 1
