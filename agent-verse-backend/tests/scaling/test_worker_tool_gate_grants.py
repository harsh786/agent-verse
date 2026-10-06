"""RV-05 (a03-F061-N1): the worker multi_agent workflow gate enforces Grantex grants.

``_worker_tool_gate`` built the gate for a Celery-queued ``multi_agent`` goal from
a namespace with no ``grant_store`` and no ``permission_matrix``. Grant
enforcement is ON by default, and ``enforce_tool_call(None, enabled=True)``
denies with ``grant_store_unavailable`` — so EVERY tool call of a queued
(production) multi_agent goal was denied, while the API path wires the durable
``PostgresGrantStore``. The default-deny permission matrix was missing too.

Pins (worker path):
* the gate run_goal hands the workflow executor carries the DB-backed grant
  store (built on the worker's session factory) and the default-deny matrix;
* a tool call is allowed when the agent holds a covering grant, denied when it
  does not (and for another tenant's grant);
* a grant store that cannot be reached denies — never allows, never raises;
* no DB factory at all still denies (``grant_store_unavailable``);
* destructive tools are denied by the default-deny permission matrix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.governance.grants import Grant, InMemoryGrantStore
from app.tenancy.context import PlanTier, TenantContext

# Taken at import: the grant window must outlast a long full-suite run (the gate
# checks against the real clock), so it spans days, not an hour.
_NOW = datetime.now(UTC)


def _ctx(tenant_id: str = "t-rv05") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k")


def _grant(*, tenant_id: str = "t-rv05", agent_id: str = "agent-1") -> Grant:
    return Grant(
        grant_id=f"g-{tenant_id}-{agent_id}",
        tenant_id=tenant_id,
        grantor="user:alice",
        grantee_agent_id=agent_id,
        scopes=("jira.*",),
        not_before=_NOW - timedelta(hours=1),
        expires_at=_NOW + timedelta(days=7),
    )


class _FakePgGrantStore(InMemoryGrantStore):
    """Stands in for PostgresGrantStore; records the session factory it got."""

    instances: list[_FakePgGrantStore] = []
    seed: list[Grant] = []
    unreachable = False

    def __init__(self, session_factory: Any) -> None:
        super().__init__()
        self.session_factory = session_factory
        type(self).instances.append(self)

    async def list_for_agent(self, tenant_id: str, agent_id: str) -> tuple[Grant, ...]:
        # Reads "the table" on every call, like the Postgres store.
        if type(self).unreachable:
            raise ConnectionError("database unavailable")
        return tuple(
            g
            for g in type(self).seed
            if g.tenant_id == tenant_id and g.grantee_agent_id == agent_id
        )

    # GRANT-08: the per-tool-call lookup reads only active grants (plus an
    # existence probe); both read "the table" like the Postgres store.
    async def active_for_agent(
        self, tenant_id: str, agent_id: str, *, now: datetime
    ) -> tuple[Grant, ...]:
        return tuple(g for g in await self.list_for_agent(tenant_id, agent_id) if g.is_active(now))

    async def has_any_for_agent(self, tenant_id: str, agent_id: str) -> bool:
        return bool(await self.list_for_agent(tenant_id, agent_id))


@pytest.fixture
def grants_on(monkeypatch: pytest.MonkeyPatch) -> type[_FakePgGrantStore]:
    import app.db.session as session_mod
    import app.governance.agent_permissions as perms_mod
    import app.governance.compliance_bundles as bundles_mod
    import app.governance.grants.postgres_store as pg_mod
    import app.governance.policy_rules as rules_mod
    from app.core.config import get_settings

    async def _no_rules(*_a: Any, **_k: Any) -> list[Any]:
        return []

    async def _no_bundle(*_a: Any, **_k: Any) -> None:
        return None

    _FakePgGrantStore.instances = []
    _FakePgGrantStore.seed = []
    _FakePgGrantStore.unreachable = False
    monkeypatch.setattr(get_settings(), "enforce_agent_grants", True)
    monkeypatch.setattr(pg_mod, "PostgresGrantStore", _FakePgGrantStore)
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: "worker-session-factory")
    # The other DB-backed gate steps are covered elsewhere; keep them neutral.
    monkeypatch.setattr(perms_mod, "load_agent_permissions", _no_rules)
    monkeypatch.setattr(rules_mod, "load_active_policy_rules", _no_rules)
    # TRUST-02 compliance bundles (fail closed when unreadable): this tenant has none.
    monkeypatch.setattr(bundles_mod, "bundle_hitl_requirement", _no_bundle)
    return _FakePgGrantStore


async def _authorize(gate: Any, tool: str = "jira.search", tenant: str = "t-rv05") -> Any:
    return await gate.authorize(
        tool_name=tool,
        server_name="jira",
        arguments={"q": "open bugs"},
        tenant_ctx=_ctx(tenant),
        goal_id="g-rv05",
        step_description="search jira",
    )


def test_worker_gate_is_wired_with_the_durable_grant_store_and_matrix(
    grants_on: type[_FakePgGrantStore],
) -> None:
    from app.governance.permissions import ActionLevel
    from app.scaling import tasks

    gate = tasks._worker_tool_gate(None, None, None, "agent-1")

    assert gate._enforce_grants is True
    assert isinstance(gate._grant_store, _FakePgGrantStore)
    assert gate._grant_store.session_factory == "worker-session-factory"
    assert gate._permission_matrix is not None
    assert (
        gate._permission_matrix.check("github.delete_repo", tenant_ctx=_ctx()) == ActionLevel.DENY
    )


async def test_worker_gate_allows_a_granted_tool(grants_on: type[_FakePgGrantStore]) -> None:
    from app.scaling import tasks

    grants_on.seed = [_grant()]
    decision = await _authorize(tasks._worker_tool_gate(None, None, None, "agent-1"))
    assert decision.allowed, decision.reason


async def test_worker_gate_denies_without_a_grant(grants_on: type[_FakePgGrantStore]) -> None:
    from app.scaling import tasks

    grants_on.seed = [_grant(agent_id="someone-else"), _grant(tenant_id="other-tenant")]
    decision = await _authorize(tasks._worker_tool_gate(None, None, None, "agent-1"))
    assert not decision.allowed
    assert "no_grant_for_agent" in decision.reason


async def test_worker_gate_denies_when_the_grant_store_is_unreachable(
    grants_on: type[_FakePgGrantStore],
) -> None:
    from app.scaling import tasks

    grants_on.seed = [_grant()]
    grants_on.unreachable = True
    decision = await _authorize(tasks._worker_tool_gate(None, None, None, "agent-1"))
    assert not decision.allowed
    assert "grant_store_unavailable" in decision.reason


async def test_worker_gate_without_a_db_factory_still_denies(
    grants_on: type[_FakePgGrantStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.db.session as session_mod
    from app.scaling import tasks

    def _boom() -> Any:
        raise RuntimeError("no database configured")

    monkeypatch.setattr(session_mod, "get_session_factory", _boom)
    grants_on.seed = [_grant()]
    gate = tasks._worker_tool_gate(None, None, None, "agent-1")
    assert gate._grant_store is None
    decision = await _authorize(gate)
    assert not decision.allowed
    assert "grant_store_unavailable" in decision.reason


async def test_worker_gate_denies_destructive_tools_by_default_matrix(
    grants_on: type[_FakePgGrantStore],
) -> None:
    from app.scaling import tasks

    grants_on.seed = [
        Grant(
            grant_id="g-all",
            tenant_id="t-rv05",
            grantor="user:alice",
            grantee_agent_id="agent-1",
            scopes=("*",),
            not_before=_NOW - timedelta(hours=1),
            expires_at=_NOW + timedelta(days=7),
        )
    ]
    decision = await _authorize(
        tasks._worker_tool_gate(None, None, None, "agent-1"), tool="jira.purge_issues"
    )
    assert not decision.allowed
    assert "permission matrix" in decision.reason


def test_queued_multi_agent_goal_gets_the_granted_gate(
    grants_on: type[_FakePgGrantStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through run_goal: the workflow executor gets the wired gate."""
    import app.agent.graph as graph_mod
    import app.agent.workflow_executor as wf_mod
    from app.scaling import tasks

    seen: dict[str, Any] = {}

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            self._pause_gate: Any = None

        async def run(self, **kwargs: Any) -> Any:
            raise AssertionError("multi_agent must run the workflow path")

    class _Executor:
        def __init__(self, **kwargs: Any) -> None:
            seen["gate"] = kwargs.get("tool_gate")

        async def execute(self, plan: Any, tenant_ctx: Any, **kwargs: Any) -> dict[str, Any]:
            gate = seen["gate"]
            # The policy engine (fails closed here: the fake DB factory cannot
            # load policies) and the budget are covered by their own tests.
            gate._policy_engine = None
            gate._cost_controller = None
            seen["allowed"] = await _authorize(gate, tenant=tenant_ctx.tenant_id)
            grants_on.seed = []
            seen["denied"] = await _authorize(gate, tenant=tenant_ctx.tenant_id)
            return {"status": "complete"}

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(wf_mod, "WorkflowExecutor", _Executor)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    grants_on.seed = [_grant(agent_id="agent-1")]

    result = tasks.run_goal.run(
        "g-rv05-q",
        "t-rv05",
        "search jira for open bugs",
        "normal",
        False,
        workflow_mode="multi_agent",
        agent_id="agent-1",
    )

    assert result["status"] == "complete", result
    assert seen["allowed"].allowed, seen["allowed"].reason
    assert not seen["denied"].allowed
    assert "no_grant_for_agent" in seen["denied"].reason
