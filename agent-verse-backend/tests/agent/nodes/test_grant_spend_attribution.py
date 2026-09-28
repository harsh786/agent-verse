"""Regression: step spend is charged to the grant that authorised the calls.

It went to the FIRST active capped grant (inside ``suppress(Exception)``), so
spend under grant B exhausted unrelated grant A while B's cap never bound, and a
failed write vanished silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.state import AgentState
from app.governance.grants import Grant, InMemoryGrantStore
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


def _grant(gid: str, cap: float | None) -> Grant:
    now = datetime.now(UTC)
    return Grant(
        grant_id=gid,
        tenant_id="t1",
        grantor="key:k",
        grantee_agent_id="agent-1",
        scopes=("*",),
        not_before=now - timedelta(seconds=5),
        expires_at=now + timedelta(hours=1),
        max_cost_usd=cap,
    )


def _executor(store: object) -> object:
    from app.agent.nodes.executor_mixin import ExecutorMixin

    ex = ExecutorMixin.__new__(ExecutorMixin)
    ex._agent_id = "agent-1"
    ex._grant_store = store
    ex._logger = MagicMock()
    return ex


@pytest.mark.asyncio
async def test_spend_goes_to_the_authorising_grant() -> None:
    store = InMemoryGrantStore()
    await store.issue(_grant("g-a", 10.0))
    await store.issue(_grant("g-b", 10.0))
    state = AgentState(goal="g", goal_id="goal-1", tenant_ctx=CTX)
    state.context["_authorizing_grant_id"] = "g-b"
    await _executor(store)._charge_grant_spend(state, CTX, 4.0)  # type: ignore[attr-defined]
    assert (await store.get("t1", "g-b")).spent_usd == 4.0
    assert (await store.get("t1", "g-a")).spent_usd == 0.0


@pytest.mark.asyncio
async def test_ambiguous_spend_is_logged_not_charged_to_a_guess() -> None:
    store = InMemoryGrantStore()
    await store.issue(_grant("g-a", 10.0))
    await store.issue(_grant("g-b", 10.0))
    ex = _executor(store)
    state = AgentState(goal="g", goal_id="goal-1", tenant_ctx=CTX)
    await ex._charge_grant_spend(state, CTX, 4.0)  # type: ignore[attr-defined]
    assert (await store.get("t1", "g-a")).spent_usd == 0.0
    ex._logger.warning.assert_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_store_error_is_logged_not_swallowed() -> None:
    store = MagicMock()
    store.list_for_agent = AsyncMock(side_effect=RuntimeError("db down"))
    ex = _executor(store)
    state = AgentState(goal="g", goal_id="goal-1", tenant_ctx=CTX)
    await ex._charge_grant_spend(state, CTX, 1.0)  # type: ignore[attr-defined]
    ex._logger.warning.assert_called_once()  # type: ignore[attr-defined]
