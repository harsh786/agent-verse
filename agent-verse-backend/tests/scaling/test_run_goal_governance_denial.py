"""CORE-01: a governance denial in the worker is a final, honest failure.

The executor raises ``PermissionError`` when a step is denied by governance —
an approval-required step outside supervised mode, a policy DENY, a rejected
or timed-out approval. ``run_goal`` used to treat that like a transient error:
Celery retried the goal (re-running the same denial) and, after the last
attempt, dead-lettered it as "exceeded max retries", burying the real reason.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

_DENIAL = (
    "Step 'deploy the api' requires human approval (high-risk step), but the goal "
    "runs in 'bounded-autonomous' mode where no approval is awaited; the step was "
    "not executed. Run the goal in supervised mode to approve it."
)


class _DeniedAgentGraph:
    def __init__(self, **kwargs: Any) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        raise PermissionError(_DENIAL)


def test_governance_denial_fails_goal_without_retry_or_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    finalize_calls: list[tuple[str, str]] = []
    dlq_calls: list[dict[str, Any]] = []
    decrement_calls: list[str] = []
    retry_calls: list[Any] = []

    async def fake_finalize(goal_id: str, tenant_id: str) -> None:
        finalize_calls.append((goal_id, tenant_id))

    async def fake_decrement(tenant_id: str, redis_url: str) -> None:
        decrement_calls.append(tenant_id)

    def fake_retry(*args: Any, **kwargs: Any) -> Any:
        retry_calls.append(kwargs)
        raise AssertionError("a governance denial must not be retried")

    monkeypatch.setattr(_graph_mod, "AgentGraph", _DeniedAgentGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    with (
        patch.object(tasks, "_finalize_owning_mission", fake_finalize),
        patch.object(tasks.run_goal_dlq, "delay", lambda **kw: dlq_calls.append(kw)),
        patch.object(tasks, "_decrement_after_completion", fake_decrement),
        patch.object(tasks.run_goal, "retry", fake_retry),
    ):
        tasks.run_goal.push_request(retries=0, called_directly=False)
        try:
            result = tasks.run_goal.run("goal-denied-1", "tenant-1", "deploy the api")
        finally:
            tasks.run_goal.pop_request()

    assert result["status"] == "failed"
    assert result["goal_id"] == "goal-denied-1"
    assert "supervised mode" in result["reason"]
    assert retry_calls == []
    assert dlq_calls == []
    assert decrement_calls == ["tenant-1"]
    assert finalize_calls == [("goal-denied-1", "tenant-1")]
