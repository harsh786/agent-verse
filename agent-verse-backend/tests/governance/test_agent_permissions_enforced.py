"""Persisted per-agent permissions (agent_permissions) are enforced at tool calls.

They were written by PUT /agents/{id}/permissions but the executor only consulted
the in-memory tenant PermissionMatrix.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.state import AgentState
from app.governance import agent_permissions as ap
from app.governance.hitl import ApprovalStatus
from tests.governance._router_app import tenant


class _Res:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _DB:
    def __init__(self, rows: list[Any] | None = None, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.stmts: list[tuple[str, Any]] = []

    def __call__(self) -> Any:
        s = MagicMock()
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=False)
        b = MagicMock()
        b.__aenter__ = AsyncMock(return_value=s)
        b.__aexit__ = AsyncMock(return_value=False)
        s.begin = MagicMock(return_value=b)
        s.execute = AsyncMock(side_effect=self._execute)
        return s

    async def _execute(self, q: Any, params: Any = None) -> _Res:
        self.stmts.append((str(q), params))
        if "agent_permissions" in str(q):
            if self.fail:
                raise RuntimeError("db down")
            return _Res(self.rows)
        return _Res([])


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    ap._CACHE.clear()


def _executor(db: Any, hitl: Any = None) -> Any:
    from app.agent.nodes.executor_mixin import ExecutorMixin

    ex = ExecutorMixin.__new__(ExecutorMixin)
    ex._agent_id = "agent-1"
    ex._db_session_factory = db
    ex._hitl_gateway = hitl
    ex._hitl_timeout = 1.0
    # Real executors always set this (AgentGraph); approval-level permissions wait
    # for a human only in supervised mode, and OI-1 reads it to reuse approvals.
    ex._autonomy_mode = "supervised"
    ex._app_state = None  # no shared Redis: the OI-1 ledger uses the goal state only
    ex._logger = MagicMock()
    ex._emit = AsyncMock()  # type: ignore[method-assign]
    return ex


def _state() -> AgentState:
    return AgentState(goal="g", goal_id="goal-1", tenant_ctx=tenant("t-p"))


async def _gate(ex: Any, tool: str, step: str = "do the thing") -> str | None:
    return await ex._agent_permission_gate(  # type: ignore[no-any-return]
        state=_state(), tenant_ctx=tenant("t-p"), tool_name=tool, step=step
    )


async def test_persisted_deny_blocks_tool_and_loads_under_rls() -> None:
    db = _DB(rows=[("jira_create_issue", "deny", None, None, "*")])
    ex = _executor(db)
    assert "denied" in (await _gate(ex, "jira_create_issue") or "")
    assert await _gate(ex, "jira_search") is None
    sql, params = next(s for s in db.stmts if "agent_permissions" in s[0])
    assert params == {"tid": "t-p", "aid": "agent-1"}
    assert "set_config('app.tenant_id'" in db.stmts[0][0]


async def test_glob_rule_and_unknown_level_fail_closed() -> None:
    db = _DB(rows=[("github_*", "bogus_level", None, None, None)])
    ex = _executor(db)
    assert await _gate(ex, "github_merge_pr") is not None


async def test_approval_level_blocks_until_approved() -> None:
    db = _DB(rows=[("stripe_refund", "approval", None, None, None)])
    hitl = MagicMock()
    hitl.request_approval = MagicMock(return_value="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.REJECTED)
    ex = _executor(db, hitl)
    assert await _gate(ex, "stripe_refund") is not None
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)
    assert await _gate(ex, "stripe_refund") is None


async def test_approval_level_without_gateway_is_denied() -> None:
    ex = _executor(_DB(rows=[("x_tool", "approval", None, None, None)]))
    assert await _gate(ex, "x_tool") is not None


async def test_load_failure_fails_closed_for_high_risk_only() -> None:
    ex = _executor(_DB(fail=True))
    assert await _gate(ex, "delete_repository") is not None
    assert await _gate(ex, "search_docs") is None


async def test_per_goal_limit_enforced() -> None:
    ex = _executor(_DB(rows=[("web_search", "allow", None, 1, None)]))
    st = _state()
    ctx = tenant("t-p")
    assert await ex._agent_permission_gate(
        state=st, tenant_ctx=ctx, tool_name="web_search", step="s"
    ) is None
    assert await ex._agent_permission_gate(
        state=st, tenant_ctx=ctx, tool_name="web_search", step="s"
    ) is not None


async def test_cache_invalidation_picks_up_new_rules() -> None:
    db = _DB(rows=[])
    ex = _executor(db)
    assert await _gate(ex, "t") is None
    db.rows = [("t", "deny", None, None, None)]
    assert await _gate(ex, "t") is None  # cached
    ap.invalidate_agent_permissions("t-p", "agent-1")
    assert await _gate(ex, "t") is not None
