"""GRANT-01 / GRANT-05: the executor's spawn path really delegates grants.

The civilization-spawn branch passed ``state.agent_id`` (a field AgentState does
not have) as the delegating parent inside ``contextlib.suppress(Exception)``, so
``delegate_active_grants`` always returned ``[]`` and every spawned child held no
grant. No test exercised the executor call site — only the helper with a valid id.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.agent.nodes.executor_mixin import ExecutorMixin
from app.governance.grants.models import Grant
from app.governance.grants.store import InMemoryGrantStore

TENANT = SimpleNamespace(tenant_id="t-deleg")


class _RecLogger:
    def __init__(self) -> None:
        self.events: list[str] = []

    def warning(self, event: str, **_: Any) -> None:
        self.events.append(event)

    info = warning


def _executor(store: Any, agent_id: str | None) -> Any:
    ex = ExecutorMixin.__new__(ExecutorMixin)
    ex._agent_id = agent_id
    ex._grant_store = store
    ex._enforce_grants = True
    ex._logger = _RecLogger()
    return ex


async def _parent_grant(store: InMemoryGrantStore, agent: str) -> Grant:
    now = datetime.now(UTC)
    return await store.issue(
        Grant(
            grant_id="g-parent",
            tenant_id=TENANT.tenant_id,
            grantor="user:alice",
            grantee_agent_id=agent,
            scopes=("github.*",),
            not_before=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
            max_cost_usd=5.0,
        )
    )


async def test_spawned_child_receives_narrowed_grants_of_the_enforced_agent() -> None:
    store = InMemoryGrantStore()
    await _parent_grant(store, "agent-parent")
    ex = _executor(store, "agent-parent")
    state = SimpleNamespace(context={})

    minted = await ex._delegate_grants_to_child(state, TENANT, "agent-child")

    assert len(minted) == 1
    child = (await store.list_for_agent(TENANT.tenant_id, "agent-child"))[0]
    assert child.parent_grant_id == "g-parent"
    assert child.scopes == ("github.*",)


async def test_parent_falls_back_to_the_goals_agent_context() -> None:
    store = InMemoryGrantStore()
    await _parent_grant(store, "agent-ctx")
    ex = _executor(store, None)

    minted = await ex._delegate_grants_to_child(
        SimpleNamespace(context={"agent_id": "agent-ctx"}), TENANT, "agent-child"
    )
    assert [g.grantee_agent_id for g in minted] == ["agent-child"]


async def test_delegation_failure_is_logged_and_grants_nothing() -> None:
    class _Broken(InMemoryGrantStore):
        async def list_for_agent(self, tenant_id: str, agent_id: str) -> tuple[Grant, ...]:
            raise RuntimeError("store down")

        async def active_for_agent(
            self, tenant_id: str, agent_id: str, *, now: Any
        ) -> tuple[Grant, ...]:
            raise RuntimeError("store down")

    ex = _executor(_Broken(), "agent-parent")
    assert await ex._delegate_grants_to_child(SimpleNamespace(context={}), TENANT, "c") == []
    assert "grant_delegation_failed" in ex._logger.events
