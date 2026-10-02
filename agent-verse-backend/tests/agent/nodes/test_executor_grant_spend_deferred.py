"""GRANT-03: grant spend charged before the authorising grant is known is not lost.

The step's LLM cost is charged at step start, before this step's tool gate sets
``_authorizing_grant_id``. With more than one capped grant the charge had no
target and was only logged as ``grant_spend_unattributed`` — so the first step
of every such goal escaped every cap. It is now held and charged to the grant the
gate names.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.agent.nodes.executor_mixin import ExecutorMixin
from app.governance.grants.models import Grant
from app.governance.grants.store import InMemoryGrantStore

TENANT = SimpleNamespace(tenant_id="t-spend")


class _Log:
    def warning(self, *a: Any, **k: Any) -> None:
        return None

    info = warning


def _grant(gid: str) -> Grant:
    now = datetime.now(UTC)
    return Grant(
        grant_id=gid,
        tenant_id=TENANT.tenant_id,
        grantor="user:alice",
        grantee_agent_id="agent-1",
        scopes=("*",),
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        max_cost_usd=10.0,
    )


async def test_step_start_spend_is_charged_to_the_grant_the_gate_names() -> None:
    store = InMemoryGrantStore()
    await store.issue(_grant("g-a"))
    await store.issue(_grant("g-b"))
    ex = ExecutorMixin.__new__(ExecutorMixin)
    ex._agent_id = "agent-1"
    ex._grant_store = store
    ex._logger = _Log()
    state = SimpleNamespace(context={})

    await ex._charge_grant_spend(state, TENANT, 2.5)  # step start: gate not run yet
    assert (await store.get(TENANT.tenant_id, "g-b")).spent_usd == 0.0

    await ex._set_authorizing_grant(state, TENANT, "g-b")  # the gate decides

    assert (await store.get(TENANT.tenant_id, "g-b")).spent_usd == 2.5
    assert (await store.get(TENANT.tenant_id, "g-a")).spent_usd == 0.0
    assert "_pending_grant_spend" not in state.context
    # Later spend in the goal goes straight to the authorising grant.
    await ex._charge_grant_spend(state, TENANT, 1.0)
    assert (await store.get(TENANT.tenant_id, "g-b")).spent_usd == 3.5
