"""RV-07: a deferred intention needs goal-submit permission and runs as its creator.

POST /memory/prospective needed only ``memory:write``, yet every intention later
ran as a full goal under a synthetic ``TenantContext(api_key_id=
"prospective-memory")`` with none of the creator's roles/scopes — so a
custom-role user or scoped key without ``goals:write`` could launch goals.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.memory.prospective import ProspectiveMemoryService
from app.memory.prospective_auth import IntentionPrincipal
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = "pm-auth-tenant"
OPERATOR = TenantContext(
    tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="key-op", roles=("operator",)
)
SCOPED = TenantContext(
    tenant_id=TENANT,
    plan=PlanTier.PROFESSIONAL,
    api_key_id="key-scoped",
    roles=("operator",),
    scopes=("memory:write", "memory:read"),
)
CUSTOM = TenantContext(
    tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="key-custom", roles=("custom",)
)
KEYS = {"av_op": OPERATOR, "av_scoped": SCOPED, "av_custom": CUSTOM}

#: What Postgres says about each key (None = revoked / unknown).
DB_KEYS: dict[str, IntentionPrincipal | None] = {}
#: role_assignments / api_key_scopes rows per key (empty = role fallback).
DB_GRANTS: dict[str, set[str]] = {}


async def _allow(*, content: str, **_kw: Any) -> dict[str, Any]:
    return {"blocked": False}


async def _fake_load_key(
    db_factory: Any, tenant_id: str, api_key_id: str, *, now: Any = None
) -> IntentionPrincipal | None:
    return DB_KEYS.get(api_key_id)


async def _fake_load_scopes(
    db_factory: Any, tenant_id: str, key_id: str, roles: tuple[str, ...]
) -> set[str]:
    from app.auth.scope_enforcement import ROLE_SCOPES

    if key_id in DB_GRANTS:
        return set(DB_GRANTS[key_id])
    out: set[str] = set()
    for r in roles:
        out.update(ROLE_SCOPES.get(r, frozenset()))
    return out


@pytest.fixture(autouse=True)
def _fakes() -> Any:
    DB_KEYS.clear()
    DB_GRANTS.clear()
    DB_KEYS["key-op"] = IntentionPrincipal("key-op", ("operator",), ())
    DB_KEYS["key-scoped"] = IntentionPrincipal(
        "key-scoped", ("operator",), ("memory:write", "memory:read")
    )
    DB_KEYS["key-custom"] = IntentionPrincipal("key-custom", ("custom",), ())
    DB_GRANTS["key-custom"] = {"memory:read", "memory:write"}
    with (
        patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_allow),
        patch("app.memory.prospective_auth.load_key_principal", side_effect=_fake_load_key),
        patch(
            "app.auth.scope_enforcement.ScopeEnforcementMiddleware._load_scopes",
            side_effect=_fake_load_scopes,
        ),
    ):
        yield


class _AgentStore:
    def __init__(self, agents: set[str]) -> None:
        self._agents = agents

    async def get_async(self, agent_id: str, tenant_ctx: Any = None) -> dict | None:
        return {"id": agent_id} if agent_id in self._agents else None


def _client(service: Any, *, agent_store: Any = None, db: Any = object()) -> TestClient:
    from app.api.memory import router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.prospective_memory_service = service
    app.state.db_session_factory = db
    app.state.agent_store = agent_store
    return TestClient(app, raise_server_exceptions=False)


def _due() -> str:
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat()


async def _stored(service: Any) -> list[Any]:
    return list(await service.list_active(TENANT, now=datetime.now(UTC)))


# ── API: creation needs goals:write (+ agent access) ─────────────────────────


async def test_scoped_key_without_goals_write_cannot_schedule() -> None:
    svc = ProspectiveMemoryService()
    r = _client(svc).post(
        "/memory/prospective",
        headers={"X-API-Key": "av_scoped"},
        json={"intention": "run payroll", "due_at": _due()},
    )
    assert r.status_code == 403, r.text
    assert "goals:write" in r.text
    assert await _stored(svc) == []


async def test_custom_role_without_goals_write_cannot_schedule() -> None:
    svc = ProspectiveMemoryService()
    r = _client(svc).post(
        "/memory/prospective",
        headers={"X-API-Key": "av_custom"},
        json={"intention": "run payroll", "due_at": _due()},
    )
    assert r.status_code == 403, r.text
    assert await _stored(svc) == []


async def test_operator_schedules_and_principal_is_persisted() -> None:
    svc = ProspectiveMemoryService()
    r = _client(svc, agent_store=_AgentStore({"agent-1"})).post(
        "/memory/prospective",
        headers={"X-API-Key": "av_op"},
        json={"intention": "check the deploy", "due_at": _due(), "agent_id": "agent-1"},
    )
    assert r.status_code == 201, r.text
    (item,) = await _stored(svc)
    assert IntentionPrincipal.from_snapshot(item.policy_snapshot) == IntentionPrincipal(
        "key-op", ("operator",), ()
    )
    assert item.policy_snapshot["agent_id"] == "agent-1"


async def test_unknown_agent_is_refused() -> None:
    svc = ProspectiveMemoryService()
    r = _client(svc, agent_store=_AgentStore(set())).post(
        "/memory/prospective",
        headers={"X-API-Key": "av_op"},
        json={"intention": "x", "due_at": _due(), "agent_id": "other-tenant-agent"},
    )
    assert r.status_code == 404, r.text
    assert await _stored(svc) == []


async def test_agent_cannot_be_verified_without_agent_store() -> None:
    svc = ProspectiveMemoryService()
    r = _client(svc, agent_store=None).post(
        "/memory/prospective",
        headers={"X-API-Key": "av_op"},
        json={"intention": "x", "due_at": _due(), "agent_id": "agent-1"},
    )
    assert r.status_code == 503, r.text
    assert await _stored(svc) == []


async def test_principal_that_is_not_an_active_key_is_refused() -> None:
    DB_KEYS["key-op"] = None  # revoked between auth-cache fill and this request
    svc = ProspectiveMemoryService()
    r = _client(svc).post(
        "/memory/prospective",
        headers={"X-API-Key": "av_op"},
        json={"intention": "x", "due_at": _due()},
    )
    assert r.status_code == 403, r.text
    assert await _stored(svc) == []


async def test_no_database_to_verify_the_principal_is_503() -> None:
    from app.memory import prospective_auth

    svc = ProspectiveMemoryService()
    with patch.object(
        prospective_auth,
        "load_key_principal",
        side_effect=prospective_auth.PrincipalCheckUnavailableError("no db"),
    ):
        r = _client(svc, db=None).post(
            "/memory/prospective",
            headers={"X-API-Key": "av_op"},
            json={"intention": "x", "due_at": _due()},
        )
    assert r.status_code == 503, r.text
    assert await _stored(svc) == []


# ── Fire time: re-check the principal, run as it, fail closed ────────────────


class _GoalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"goal_id": f"goal-{len(self.calls)}"}


async def _seed(svc: Any, principal: IntentionPrincipal | None, text: str = "do it") -> Any:
    from app.memory.prospective_runtime import create_intention

    now = datetime.now(UTC)
    return await create_intention(
        svc,
        tenant_id=TENANT,
        intention=text,
        due_at=now - timedelta(minutes=1),
        now=now - timedelta(minutes=2),
        principal=principal,
    )


async def _fire(svc: Any, goal_svc: _GoalService) -> tuple[list[Any], list[dict[str, Any]]]:
    from app.memory.prospective_runtime import fire_due_intentions
    from app.scaling import tasks

    audits: list[dict[str, Any]] = []

    async def _audit(db_factory: Any, **kw: Any) -> None:
        audits.append(kw)

    async def _submit(item: Any) -> dict[str, Any]:
        return await tasks._submit_intention_as_goal(item, "professional")

    with (
        patch.object(tasks, "_build_worker_goal_service", return_value=(goal_svc, None)),
        patch("app.db.session.get_session_factory", return_value=object()),
        patch("app.memory.prospective_auth.audit_intention_denied", side_effect=_audit),
    ):
        fired = await fire_due_intentions(svc, tenant_id=TENANT, submit=_submit)
    return fired, audits


async def test_fire_runs_goal_attributed_to_the_creator() -> None:
    svc = ProspectiveMemoryService()
    await _seed(svc, IntentionPrincipal("key-op", ("operator",), ()))
    goal_svc = _GoalService()
    fired, audits = await _fire(svc, goal_svc)
    assert len(fired) == 1 and audits == []
    ctx = goal_svc.calls[0]["tenant_ctx"]
    assert ctx.api_key_id == "key-op" and ctx.roles == ("operator",)
    assert ctx.tenant_id == TENANT and ctx.plan == PlanTier.PROFESSIONAL


async def test_fire_skips_and_audits_when_key_revoked() -> None:
    svc = ProspectiveMemoryService()
    item = await _seed(svc, IntentionPrincipal("key-op", ("operator",), ()))
    DB_KEYS["key-op"] = None
    goal_svc = _GoalService()
    fired, audits = await _fire(svc, goal_svc)
    assert fired == [] and goal_svc.calls == []
    stored = await svc.get(TENANT, item.memory_id)
    assert stored.state == "failed"  # terminal on the first denial, not retried
    assert audits and audits[0]["memory_id"] == item.memory_id
    assert audits[0]["api_key_id"] == "key-op"


async def test_fire_skips_when_scope_no_longer_held() -> None:
    svc = ProspectiveMemoryService()
    item = await _seed(svc, IntentionPrincipal("key-op", ("operator",), ()))
    DB_GRANTS["key-op"] = {"memory:write"}  # role assignment narrowed since creation
    goal_svc = _GoalService()
    fired, audits = await _fire(svc, goal_svc)
    assert fired == [] and goal_svc.calls == []
    assert (await svc.get(TENANT, item.memory_id)).state == "failed"
    assert len(audits) == 1


async def test_fire_skips_when_key_scopes_were_narrowed() -> None:
    svc = ProspectiveMemoryService()
    await _seed(svc, IntentionPrincipal("key-op", ("operator",), ()))
    DB_KEYS["key-op"] = IntentionPrincipal("key-op", ("operator",), ("memory:write",))
    goal_svc = _GoalService()
    fired, audits = await _fire(svc, goal_svc)
    assert fired == [] and goal_svc.calls == [] and len(audits) == 1


async def test_fire_skips_intention_with_no_recorded_principal() -> None:
    svc = ProspectiveMemoryService()
    item = await _seed(svc, None)
    goal_svc = _GoalService()
    fired, audits = await _fire(svc, goal_svc)
    assert fired == [] and goal_svc.calls == []
    assert (await svc.get(TENANT, item.memory_id)).state == "failed"
    assert len(audits) == 1


async def test_fire_retries_when_principal_cannot_be_verified() -> None:
    from app.memory import prospective_auth

    svc = ProspectiveMemoryService()
    item = await _seed(svc, IntentionPrincipal("key-op", ("operator",), ()))
    goal_svc = _GoalService()
    with patch.object(
        prospective_auth,
        "load_key_principal",
        side_effect=prospective_auth.PrincipalCheckUnavailableError("db down"),
    ):
        fired, _ = await _fire(svc, goal_svc)
    assert fired == [] and goal_svc.calls == []
    # Not run, not completed: left leased for a later retry (never "allowed").
    assert (await svc.get(TENANT, item.memory_id)).state == "leased"


async def test_agent_tool_refuses_without_a_verifiable_principal() -> None:
    from app.memory import prospective_runtime

    svc = ProspectiveMemoryService()
    prospective_runtime.set_prospective_service(svc)
    try:
        worker_ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.FREE, api_key_id="celery-worker")
        DB_KEYS["celery-worker"] = None
        with patch("app.db.session.get_session_factory", return_value=object()):
            out = await prospective_runtime.call_tool(
                "defer_intention", {"intention": "later"}, None, worker_ctx
            )
            assert "error" in out
            assert await _stored(svc) == []
            ok = await prospective_runtime.call_tool(
                "defer_intention", {"intention": "later"}, None, OPERATOR
            )
        assert "deferred" in ok, ok
        (item,) = await _stored(svc)
        assert IntentionPrincipal.from_snapshot(item.policy_snapshot) is not None
    finally:
        prospective_runtime.set_prospective_service(None)
