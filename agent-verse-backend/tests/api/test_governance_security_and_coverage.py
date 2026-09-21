"""Functional/contract tests for app/api/governance.py, focused on security and
correctness of the HITL approval workflow (double-approve, RBAC, org-gate
bridging, tenant isolation) rather than line-coverage padding.

Covers, among other things:
  - RBAC: an authenticated tenant WITHOUT the "approver"/"admin" role is
    rejected with 403 (not just unauthenticated -> 401, which is already
    covered elsewhere).
  - Double-approve / reject-after-approve / approve-after-reject all return
    404 (the request is no longer "pending" so the second call can't resolve
    it, and it isn't a durable org-gate task either).
  - Batch approve of the same request_id twice in one call: only the first
    resolves; the second is reported "not_found".
  - Durable org approval-gate bridging (_org_gate_approvals / _resolve_org_gate
    / _drop_terminal_owner_approvals): approving the last pending gate for a
    mission dispatches the mission goal; approving with gates still
    remaining does not; rejecting fails the mission; a gate lookup failure
    that occurs *after* the gate was found re-raises as a 500 instead of a
    silent False.
  - Cross-tenant / terminal-goal approval hiding.
  - The email one-click approve/reject links, including a regression test
    for a fixed bug where a locally-shadowed ``HTTPException`` import meant
    any non-403 error path in those two endpoints crashed with an unhandled
    UnboundLocalError (-> 500) instead of returning the intended status code.
"""

from __future__ import annotations

import datetime as _dt
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.policies import PolicyEngine
from app.integrations.email.approval_sender import _sign
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_TENANT_ID = "tid-secgov"
_APPROVER_KEY = "av_secgov_approver"
_VIEWER_KEY = "av_secgov_viewer"

_APPROVER_CTX = TenantContext(
    tenant_id=_TENANT_ID, plan=PlanTier.ENTERPRISE, api_key_id="kid-approver",
    roles=("approver",),
)
_ADMIN_CTX = TenantContext(
    tenant_id=_TENANT_ID, plan=PlanTier.ENTERPRISE, api_key_id="kid-admin",
    roles=("admin",),
)
_VIEWER_CTX = TenantContext(
    tenant_id=_TENANT_ID, plan=PlanTier.ENTERPRISE, api_key_id="kid-viewer",
    roles=("viewer",),
)


def _make_app(
    *,
    hitl: HITLGateway | None = None,
    audit: AuditLog | None = None,
    cost: CostController | None = None,
    policy_engine: PolicyEngine | None = None,
    notification_service: Any = None,
    goal_service: Any = None,
    redis: Any = None,
    db_session_factory: Any = None,
    tenant_service: Any = None,
    ctx: TenantContext = _APPROVER_CTX,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key == _APPROVER_KEY:
            return ctx
        if key == _VIEWER_KEY:
            return _VIEWER_CTX
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(governance_router)
    app.state.hitl_gateway = hitl or HITLGateway()
    app.state.audit_log = audit or AuditLog()
    app.state.cost_controller = cost or CostController()
    app.state.policy_engine = policy_engine or PolicyEngine()
    if notification_service is not None:
        app.state.notification_service = notification_service
    if goal_service is not None:
        app.state.goal_service = goal_service
    if redis is not None:
        app.state._policy_pubsub_redis = redis
    if db_session_factory is not None:
        app.state.db_session_factory = db_session_factory
    if tenant_service is not None:
        app.state.tenant_service = tenant_service
    return app


def _h(key: str = _APPROVER_KEY) -> dict[str, str]:
    return {"X-API-Key": key}


# ---------------------------------------------------------------------------
# RBAC: authenticated-but-insufficient-role must be 403, not just 401
# ---------------------------------------------------------------------------


def test_approve_with_viewer_role_is_403_not_401() -> None:
    """A real tenant lacking the 'approver' role must be forbidden (403),
    distinct from the already-covered unauthenticated (401) case."""
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-rbac", action="delete_prod_db", risk_level="critical",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
    resp = client.post(
        f"/governance/approvals/{request_id}/approve",
        json={"approver": "eve", "note": "trying anyway"},
        headers=_h(_VIEWER_KEY),
    )
    assert resp.status_code == 403
    # The request must still be pending — a forbidden call must have no side effect.
    pending = client.get("/governance/approvals", headers=_h()).json()
    assert len(pending) == 1


def test_batch_approve_with_viewer_role_is_403() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/hitl/batch-approve",
        json={"action": "approve", "request_ids": ["r1"], "approver": "eve"},
        headers=_h(_VIEWER_KEY),
    )
    assert resp.status_code == 403


def test_set_budget_with_approver_role_is_403_requires_admin() -> None:
    """Budget changes require 'admin', not merely 'approver'."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/governance/budget",
        json={"per_goal_usd": 999.0, "per_tenant_daily_usd": 9999.0},
        headers=_h(),  # approver role, not admin
    )
    assert resp.status_code == 403


def test_rollback_policy_with_approver_role_is_403_requires_admin() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/policies/pol-1/rollback",
        json={"target_version": 1, "reason": "revert"},
        headers=_h(),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Double-approve / conflicting resolution attempts
# ---------------------------------------------------------------------------


def test_double_approve_second_call_returns_404() -> None:
    """Approving an already-approved request must not silently succeed again."""
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-1", action="wipe_bucket", risk_level="critical",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)

    first = client.post(
        f"/governance/approvals/{request_id}/approve",
        json={"approver": "alice", "note": "ok"},
        headers=_h(),
    )
    assert first.status_code == 200

    second = client.post(
        f"/governance/approvals/{request_id}/approve",
        json={"approver": "mallory", "note": "me too"},
        headers=_h(),
    )
    assert second.status_code == 404


def test_reject_after_approve_returns_404() -> None:
    """Once approved, a request can no longer be rejected."""
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-2", action="delete_all_users", risk_level="critical",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
    client.post(
        f"/governance/approvals/{request_id}/approve",
        json={"approver": "alice"}, headers=_h(),
    )
    resp = client.post(
        f"/governance/approvals/{request_id}/reject",
        json={"approver": "bob", "note": "too late"},
        headers=_h(),
    )
    assert resp.status_code == 404


def test_approve_after_reject_returns_404() -> None:
    """Once rejected, a request can no longer be approved."""
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-3", action="rotate_prod_keys", risk_level="high",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
    client.post(
        f"/governance/approvals/{request_id}/reject",
        json={"approver": "bob"}, headers=_h(),
    )
    resp = client.post(
        f"/governance/approvals/{request_id}/approve",
        json={"approver": "alice", "note": "changed my mind"},
        headers=_h(),
    )
    assert resp.status_code == 404


def test_batch_approve_same_id_twice_second_is_not_found() -> None:
    """Submitting the same request_id twice in one batch must resolve it once."""
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-batch", action="grant_admin", risk_level="high",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
    resp = client.post(
        "/governance/hitl/batch-approve",
        json={
            "action": "approve",
            "request_ids": [request_id, request_id],
            "approver": "alice",
        },
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] == 1
    assert body["not_found"] == 1
    assert body["results"][0]["result"] == "approved"
    assert body["results"][1]["result"] == "not_found"


def test_batch_approve_invalid_action_422() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/hitl/batch-approve",
        json={"action": "explode", "request_ids": ["x"], "approver": "a"},
        headers=_h(),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Small helpers / branches unreachable through the normal middleware flow
# ---------------------------------------------------------------------------


def test_require_tenant_401_when_state_has_no_tenant() -> None:
    """_require_tenant's own 401 branch (distinct from TenantMiddleware's
    401, which short-circuits before the route body runs in every other
    test in this suite)."""
    app = FastAPI()

    @app.middleware("http")
    async def _no_tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = None
        return await call_next(request)

    app.include_router(governance_router)
    app.state.hitl_gateway = HITLGateway()
    app.state.audit_log = AuditLog()
    app.state.cost_controller = CostController()
    app.state.policy_engine = PolicyEngine()

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/governance/policies")
    assert resp.status_code == 401


def test_cost_helper_returns_configured_cost_controller() -> None:
    """The `_cost` accessor is not wired to any endpoint but must still do
    what it says: return app.state.cost_controller."""
    from fastapi import Request

    from app.api.governance import _cost

    app = _make_app()
    scope = {
        "type": "http", "method": "GET", "path": "/x", "headers": [],
        "app": app,
    }
    request = Request(scope)
    assert _cost(request) is app.state.cost_controller


# ---------------------------------------------------------------------------
# Policy CRUD: DB failure paths must degrade gracefully, not 500
# ---------------------------------------------------------------------------


class _RaisingSession:
    async def __aenter__(self) -> _RaisingSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def begin(self) -> Any:
        @asynccontextmanager
        async def _cm() -> Any:
            yield self

        return _cm()

    async def execute(self, *_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("db exploded")


def test_list_policies_db_exception_falls_back_to_in_memory_registry() -> None:
    db = lambda: _RaisingSession()  # noqa: E731
    client = TestClient(_make_app(db_session_factory=db), raise_server_exceptions=False)

    create = client.post(
        "/governance/policies",
        json={"name": "p1", "tools_pattern": "x_*", "action": "deny"},
        headers=_h(),
    )
    assert create.status_code == 201  # create swallows the DB error too

    listing = client.get("/governance/policies", headers=_h())
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["name"] == "p1"


def test_delete_policy_db_exception_swallowed_then_removed_from_registry() -> None:
    db = lambda: _RaisingSession()  # noqa: E731
    client = TestClient(_make_app(db_session_factory=db), raise_server_exceptions=False)
    created = client.post(
        "/governance/policies",
        json={"name": "p2", "tools_pattern": "y_*", "action": "deny"},
        headers=_h(),
    )
    policy_id = created.json()["policy_id"]
    resp = client.delete(f"/governance/policies/{policy_id}", headers=_h())
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Notification "test" endpoint
# ---------------------------------------------------------------------------


def test_notification_test_channel_success() -> None:
    from app.services.notification_service import NotificationChannel, NotificationService

    svc = NotificationService()
    svc.add_channel(
        NotificationChannel(channel_id="c1", tenant_id=_TENANT_ID, channel_type="webhook", config={})
    )
    client = TestClient(
        _make_app(notification_service=svc), raise_server_exceptions=False
    )
    resp = client.post("/governance/notifications/c1/test", headers=_h())
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "webhook" in body["message"]


def test_notification_test_channel_delivery_failure_returns_200_with_success_false() -> None:
    from app.services.notification_service import NotificationChannel, NotificationService

    svc = NotificationService()
    svc.add_channel(
        NotificationChannel(channel_id="c1", tenant_id=_TENANT_ID, channel_type="webhook", config={})
    )
    svc.notify_approval_required = AsyncMock(side_effect=RuntimeError("smtp down"))
    client = TestClient(
        _make_app(notification_service=svc), raise_server_exceptions=False
    )
    resp = client.post("/governance/notifications/c1/test", headers=_h())
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "smtp down" in body["message"]


def test_notification_test_channel_unknown_channel_404() -> None:
    from app.services.notification_service import NotificationService

    svc = NotificationService()
    client = TestClient(_make_app(notification_service=svc), raise_server_exceptions=False)
    resp = client.post("/governance/notifications/ghost/test", headers=_h())
    assert resp.status_code == 404


def test_notification_test_channel_no_service_503() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/governance/notifications/c1/test", headers=_h())
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# rollback_policy / verify_audit_chain: generic DB exception -> 500
# ---------------------------------------------------------------------------


def test_rollback_policy_generic_db_exception_returns_500() -> None:
    session = _RaisingSession()
    db = lambda: session  # noqa: E731
    client = TestClient(
        _make_app(db_session_factory=db, ctx=_ADMIN_CTX), raise_server_exceptions=False
    )
    # admin role needed
    resp = client.post(
        "/governance/policies/pol-1/rollback",
        json={"target_version": 1, "reason": "revert"},
        headers=_h(),
    )
    assert resp.status_code == 500


def test_verify_audit_chain_generic_exception_returns_500() -> None:
    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    db = lambda: _Session()  # noqa: E731
    with patch(
        "app.governance.audit_v3.HashChainVerifier.verify",
        AsyncMock(side_effect=RuntimeError("hash mismatch boom")),
    ):
        client = TestClient(_make_app(db_session_factory=db), raise_server_exceptions=False)
        resp = client.get("/governance/audit/integrity/verify", headers=_h())
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Org-gate approval bridging: approve_request / reject_request fall back to
# a durable org approval-gate task when the id isn't a live HITL request.
# ---------------------------------------------------------------------------


class _DictLikeSession:
    """Fake AsyncSession whose .execute() dispatches on SQL content."""

    def __init__(self, dispatch: Any) -> None:
        self._dispatch = dispatch

    async def __aenter__(self) -> _DictLikeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def begin(self) -> Any:
        @asynccontextmanager
        async def _cm() -> Any:
            yield self

        return _cm()

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        return self._dispatch(str(stmt), params or {})

    async def commit(self) -> None:
        return None


def _mapping_result(rows: list[dict[str, Any]]) -> Any:
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


def _scalars_result(values: list[Any]) -> Any:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _fetchall_result(rows: list[tuple[Any, ...]]) -> Any:
    result = MagicMock()
    result.fetchall.return_value = rows
    return result


def _make_org_task(*, mission_id: str | None, org_id: str = "org-1", status: str = "approval_required") -> Any:
    task = MagicMock()
    task.extra_data = {"task_kind": "approval_gate"}
    task.mission_id = mission_id
    task.org_id = org_id
    task.status = status
    return task


def test_approve_resolves_org_gate_and_dispatches_mission_when_last_gate() -> None:
    task = _make_org_task(mission_id="mission-1")

    org_service = MagicMock()
    org_service.get_task = AsyncMock(return_value=task)
    org_service.update_task_status = AsyncMock()
    org_service.list_tasks = AsyncMock(return_value=[])  # no remaining pending gates
    org_service.dispatch_mission_goal = AsyncMock()

    session = _DictLikeSession(lambda sql, params: MagicMock())

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.org.service.OrgService", return_value=org_service),
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/approvals/gate-task-1/approve",
            json={"approver": "alice", "note": "go"},
            headers=_h(),
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    org_service.update_task_status.assert_awaited_once()
    org_service.dispatch_mission_goal.assert_awaited_once_with(
        "mission-1", app_state=client.app.state
    )


def test_approve_resolves_org_gate_but_does_not_dispatch_when_gates_remain() -> None:
    task = _make_org_task(mission_id="mission-2")
    remaining_task = _make_org_task(mission_id="mission-2")  # still approval_required

    org_service = MagicMock()
    org_service.get_task = AsyncMock(return_value=task)
    org_service.update_task_status = AsyncMock()
    org_service.list_tasks = AsyncMock(return_value=[remaining_task])
    org_service.dispatch_mission_goal = AsyncMock()

    session = _DictLikeSession(lambda sql, params: MagicMock())

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.org.service.OrgService", return_value=org_service),
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/approvals/gate-task-2/approve",
            json={"approver": "alice"},
            headers=_h(),
        )

    assert resp.status_code == 200
    org_service.dispatch_mission_goal.assert_not_awaited()


def test_reject_resolves_org_gate_and_fails_active_mission() -> None:
    task = _make_org_task(mission_id="mission-3")
    mission = MagicMock()
    mission.status = "running"

    org_service = MagicMock()
    org_service.get_task = AsyncMock(return_value=task)
    org_service.update_task_status = AsyncMock()
    org_service.get_mission = AsyncMock(return_value=mission)
    org_service.update_mission_status = AsyncMock()

    session = _DictLikeSession(lambda sql, params: MagicMock())

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.org.service.OrgService", return_value=org_service),
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/approvals/gate-task-3/reject",
            json={"approver": "bob", "note": "no"},
            headers=_h(),
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    org_service.update_mission_status.assert_awaited_once_with("mission-3", "failed")


def test_reject_resolves_org_gate_leaves_terminal_mission_alone() -> None:
    task = _make_org_task(mission_id="mission-4")
    mission = MagicMock()
    mission.status = "completed"  # already terminal — must not be touched

    org_service = MagicMock()
    org_service.get_task = AsyncMock(return_value=task)
    org_service.update_task_status = AsyncMock()
    org_service.get_mission = AsyncMock(return_value=mission)
    org_service.update_mission_status = AsyncMock()

    session = _DictLikeSession(lambda sql, params: MagicMock())

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.org.service.OrgService", return_value=org_service),
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/approvals/gate-task-4/reject",
            json={"approver": "bob"},
            headers=_h(),
        )

    assert resp.status_code == 200
    org_service.update_mission_status.assert_not_awaited()


def test_approve_unknown_id_that_is_not_an_org_gate_returns_404() -> None:
    org_service = MagicMock()
    org_service.get_task = AsyncMock(return_value=None)

    session = _DictLikeSession(lambda sql, params: MagicMock())

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.org.service.OrgService", return_value=org_service),
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/approvals/totally-unknown/approve",
            json={"approver": "alice"},
            headers=_h(),
        )
    assert resp.status_code == 404


def test_approve_org_gate_task_of_wrong_kind_returns_404() -> None:
    """A real org_tasks row that isn't an approval_gate must not be resolvable
    via the HITL approve endpoint."""
    task = MagicMock()
    task.extra_data = {"task_kind": "regular_step"}

    org_service = MagicMock()
    org_service.get_task = AsyncMock(return_value=task)

    session = _DictLikeSession(lambda sql, params: MagicMock())

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.org.service.OrgService", return_value=org_service),
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/approvals/step-1/approve",
            json={"approver": "alice"},
            headers=_h(),
        )
    assert resp.status_code == 404


def test_resolve_org_gate_exception_after_found_reraises_as_500() -> None:
    """Once the gate is confirmed to exist, a failure applying the decision
    is a genuine server error and must propagate (not silently 404)."""
    task = _make_org_task(mission_id=None)

    org_service = MagicMock()
    org_service.get_task = AsyncMock(return_value=task)
    org_service.update_task_status = AsyncMock(side_effect=RuntimeError("db write failed"))

    session = _DictLikeSession(lambda sql, params: MagicMock())

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.org.service.OrgService", return_value=org_service),
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/approvals/gate-broken/approve",
            json={"approver": "alice"},
            headers=_h(),
        )
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# _org_gate_approvals bridging into /approvals and /approvals/history
# ---------------------------------------------------------------------------


def test_list_approvals_includes_pending_org_gate_task() -> None:
    now = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)
    row = {
        "id": "gate-9",
        "org_id": "org-9",
        "mission_id": None,
        "title": "Deploy to prod",
        "why": "Deploy to prod",
        "risk_level": "critical",
        "status": "approval_required",
        "created_at": now,
        "updated_at": now,
        "outputs": [],
    }

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        return _mapping_result([row])

    session = _DictLikeSession(_dispatch)
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/approvals", headers=_h())

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["request_id"] == "gate-9"
    assert body[0]["source"] == "org_gate"
    assert body[0]["status"] == "pending"


def test_list_approval_history_includes_resolved_org_gate_filtered_by_status() -> None:
    now = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)
    approved_row = {
        "id": "gate-approved", "org_id": "org-1", "mission_id": None,
        "title": "t", "why": "t", "risk_level": "high", "status": "running",
        "created_at": now, "updated_at": now,
        "outputs": [{"approved_by": "alice", "approval_note": "ok"}],
    }
    rejected_row = {
        "id": "gate-rejected", "org_id": "org-1", "mission_id": None,
        "title": "t2", "why": "t2", "risk_level": "high", "status": "cancelled",
        "created_at": now, "updated_at": now,
        "outputs": [{"rejected_by": "bob", "rejection_note": "no"}],
    }

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        return _mapping_result([approved_row, rejected_row])

    session = _DictLikeSession(_dispatch)
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get(
            "/governance/approvals/history?status_filter=rejected", headers=_h()
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["request_id"] == "gate-rejected"
    assert body[0]["approver"] == "bob"


# ---------------------------------------------------------------------------
# _drop_terminal_owner_approvals: hides approvals whose owning goal already
# finished, and never lets a filter failure break the whole inbox.
# ---------------------------------------------------------------------------


def test_list_approvals_hides_entries_whose_goal_already_completed() -> None:
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="goal-done", action="archive_project", risk_level="low",
            tenant_ctx=_APPROVER_CTX,
        )
    )

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        if "org_tasks" in sql:
            return _mapping_result([])  # no org gates
        if "org_missions" in sql:
            return _scalars_result([])
        if "FROM goals" in sql:
            return _scalars_result(["goal-done"])  # this goal is terminal
        return MagicMock()

    session = _DictLikeSession(_dispatch)
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get("/governance/approvals", headers=_h())

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_approvals_terminal_filter_db_exception_keeps_original_list() -> None:
    """A DB failure while checking for terminal goals must never hide a
    legitimately pending approval — fail open on the filter, not on the
    approval itself."""
    gateway = HITLGateway()
    gateway.request_approval(
        goal_id="goal-x", action="some_action", risk_level="low", tenant_ctx=_APPROVER_CTX,
    )

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        if "org_tasks" in sql:
            return _mapping_result([])
        raise RuntimeError("db down")

    session = _DictLikeSession(_dispatch)
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get("/governance/approvals", headers=_h())

    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# G-10 org_id scoping filter on /approvals
# ---------------------------------------------------------------------------


def test_list_approvals_org_id_filter_keeps_matching_goal() -> None:
    gateway = HITLGateway()
    gateway.request_approval(
        goal_id="goal-scoped", action="deploy", risk_level="high", tenant_ctx=_APPROVER_CTX,
    )

    goal_row = MagicMock()
    goal_row.__getitem__ = lambda self, i: {"org_id": "org-match"}

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        if "org_tasks" in sql:
            return _mapping_result([])
        result = MagicMock()
        result.first.return_value = ({"org_id": "org-match"},)
        return result

    session = _DictLikeSession(_dispatch)
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get("/governance/approvals?org_id=org-match", headers=_h())

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_approvals_org_id_filter_excludes_non_matching_goal() -> None:
    gateway = HITLGateway()
    gateway.request_approval(
        goal_id="goal-other", action="deploy", risk_level="high", tenant_ctx=_APPROVER_CTX,
    )

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        if "org_tasks" in sql:
            return _mapping_result([])
        result = MagicMock()
        result.first.return_value = ({"org_id": "org-different"},)
        return result

    session = _DictLikeSession(_dispatch)
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get("/governance/approvals?org_id=org-match", headers=_h())

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_approvals_org_id_filter_swallows_lookup_exception() -> None:
    gateway = HITLGateway()
    gateway.request_approval(
        goal_id="goal-broken", action="deploy", risk_level="high", tenant_ctx=_APPROVER_CTX,
    )

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        if "org_tasks" in sql:
            return _mapping_result([])
        raise RuntimeError("lookup broke")

    session = _DictLikeSession(_dispatch)
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get("/governance/approvals?org_id=org-match", headers=_h())

    assert resp.status_code == 200
    # Unresolvable goal is treated as "not in scope" -> excluded, not 500.
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Email one-click approve/reject links
# ---------------------------------------------------------------------------


def test_email_approve_link_success_flow_with_tenant_plan_lookup() -> None:
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-email", action="restart_service", risk_level="medium",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    tenant_svc = MagicMock()
    tenant_svc.get_tenant = AsyncMock(return_value={"plan": "professional"})

    sig = _sign(request_id, "approve")
    client = TestClient(
        _make_app(hitl=gateway, tenant_service=tenant_svc), raise_server_exceptions=False
    )
    resp = client.get(f"/governance/hitl/{request_id}/approve?sig={sig}", headers=_h())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["approver"] == "email-link"

    # And it is reflected in the gateway's own state.
    assert gateway.get_request(request_id, tenant_ctx=_APPROVER_CTX).status.value == "approved"


def test_email_approve_link_tenant_lookup_exception_falls_back_to_free_plan() -> None:
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-email2", action="restart_service", risk_level="medium",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    tenant_svc = MagicMock()
    tenant_svc.get_tenant = AsyncMock(side_effect=RuntimeError("tenant lookup down"))

    sig = _sign(request_id, "approve")
    client = TestClient(
        _make_app(hitl=gateway, tenant_service=tenant_svc), raise_server_exceptions=False
    )
    resp = client.get(f"/governance/hitl/{request_id}/approve?sig={sig}", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_email_reject_link_success_flow() -> None:
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-email3", action="restart_service", risk_level="medium",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    sig = _sign(request_id, "reject")
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
    resp = client.get(f"/governance/hitl/{request_id}/reject?sig={sig}", headers=_h())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert gateway.get_request(request_id, tenant_ctx=_APPROVER_CTX).status.value == "rejected"


def test_email_reject_link_tenant_lookup_success_uses_real_plan() -> None:
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-email4", action="restart_service", risk_level="medium",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    tenant_svc = MagicMock()
    tenant_svc.get_tenant = AsyncMock(return_value={"plan": "enterprise"})

    sig = _sign(request_id, "reject")
    client = TestClient(
        _make_app(hitl=gateway, tenant_service=tenant_svc), raise_server_exceptions=False
    )
    resp = client.get(f"/governance/hitl/{request_id}/reject?sig={sig}", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_email_approve_link_cross_action_signature_is_rejected() -> None:
    """A signature minted for 'approve' must not authorize the 'reject' link
    (or vice versa) — the action is bound into the HMAC."""
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-cross", action="restart_service", risk_level="medium",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    approve_sig = _sign(request_id, "approve")
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
    resp = client.get(f"/governance/hitl/{request_id}/reject?sig={approve_sig}", headers=_h())
    assert resp.status_code == 403

    # The request must remain untouched — still pending.
    assert (
        gateway.get_request(request_id, tenant_ctx=_APPROVER_CTX).status.value == "pending"
    )


def test_email_approve_link_already_resolved_returns_409() -> None:
    """Regression test: governance.py's email_approve_link used to shadow the
    module-level `HTTPException` with a *local* `from fastapi import
    HTTPException` inside the signature-check `if` block. Because Python
    resolves a name's scope for the whole function at compile time, once sig
    was valid every later `raise HTTPException(...)` in the function (503,
    404, 409) read an unbound local and blew up with UnboundLocalError
    instead of returning the intended status code. The shadowing import has
    been removed; this locks in the correct 409 Conflict on double-resolve."""
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-already", action="restart_service", risk_level="medium",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    sig = _sign(request_id, "approve")
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)

    first = client.get(f"/governance/hitl/{request_id}/approve?sig={sig}", headers=_h())
    assert first.status_code == 200

    second = client.get(f"/governance/hitl/{request_id}/approve?sig={sig}", headers=_h())
    assert second.status_code == 409


def test_email_reject_link_already_resolved_returns_409() -> None:
    """Same regression as above, mirrored on the reject link."""
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-already2", action="restart_service", risk_level="medium",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    sig = _sign(request_id, "reject")
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)

    first = client.get(f"/governance/hitl/{request_id}/reject?sig={sig}", headers=_h())
    assert first.status_code == 200

    second = client.get(f"/governance/hitl/{request_id}/reject?sig={sig}", headers=_h())
    assert second.status_code == 409


def test_email_reject_link_tenant_lookup_exception_falls_back_to_free_plan() -> None:
    gateway = HITLGateway()
    request_id = str(
        gateway.request_approval(
            goal_id="g-email5", action="restart_service", risk_level="medium",
            tenant_ctx=_APPROVER_CTX,
        )
    )
    tenant_svc = MagicMock()
    tenant_svc.get_tenant = AsyncMock(side_effect=RuntimeError("tenant lookup down"))

    sig = _sign(request_id, "reject")
    client = TestClient(
        _make_app(hitl=gateway, tenant_service=tenant_svc), raise_server_exceptions=False
    )
    resp = client.get(f"/governance/hitl/{request_id}/reject?sig={sig}", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


# ---------------------------------------------------------------------------
# /approvals/history: no-db and DB-exception paths
# ---------------------------------------------------------------------------


def test_list_approval_history_no_db_returns_empty() -> None:
    with patch(
        "app.db.session.get_session_factory", side_effect=RuntimeError("no db configured")
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/approvals/history", headers=_h())
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_approval_history_db_exception_falls_back_to_gate_history_only() -> None:
    now = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)
    gate_row = {
        "id": "gate-only", "org_id": "org-1", "mission_id": None,
        "title": "t", "why": "t", "risk_level": "high", "status": "running",
        "created_at": now, "updated_at": now,
        "outputs": [{"approved_by": "alice"}],
    }

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        if "org_tasks" in sql:
            return _mapping_result([gate_row])
        raise RuntimeError("approval_requests table unreachable")

    session = _DictLikeSession(_dispatch)
    # Both `_get_db` (app.state) and `_org_gate_approvals` (module-level import)
    # need to see the same fake session for this endpoint's two DB round-trips.
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(
            _make_app(db_session_factory=lambda: session), raise_server_exceptions=False
        )
        resp = client.get("/governance/approvals/history", headers=_h())
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["request_id"] == "gate-only"


# ---------------------------------------------------------------------------
# org_id filter: pending approvals with no goal_id are skipped (not resolved)
# ---------------------------------------------------------------------------


def test_list_approvals_org_id_filter_skips_entries_with_no_goal_id() -> None:
    gateway = HITLGateway()
    gateway.request_approval(
        goal_id="", action="no_goal_action", risk_level="low", tenant_ctx=_APPROVER_CTX,
    )

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        return _mapping_result([])

    session = _DictLikeSession(_dispatch)
    with patch("app.db.session.get_session_factory", return_value=lambda: session):
        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get("/governance/approvals?org_id=org-match", headers=_h())

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# delete_policy: DB-authoritative lookup finds the record (cross-pod delete)
# ---------------------------------------------------------------------------


def test_delete_policy_found_via_db_lookup_not_local_registry() -> None:
    """A policy created on a different pod (so it's absent from this pod's
    in-memory registry) must still be deletable when the DB has it."""
    # _db_list_policies selects: id, name, tools_pattern, action, priority, description
    row = ("pol-from-other-pod", "cross-pod-policy", "z_*", "deny", 0, "")

    def _dispatch(sql: str, params: dict[str, Any]) -> Any:
        return _fetchall_result([row])

    session = _DictLikeSession(_dispatch)
    client = TestClient(
        _make_app(db_session_factory=lambda: session), raise_server_exceptions=False
    )
    resp = client.delete("/governance/policies/pol-from-other-pod", headers=_h())
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# _get_db: exception constructing the fallback session factory
# ---------------------------------------------------------------------------


def test_get_db_exception_falls_back_gracefully() -> None:
    with patch(
        "app.db.session.get_session_factory", side_effect=RuntimeError("no db configured")
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/legal-holds", headers=_h())
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# SSE tails: skip non-"message" frames, invalid JSON, non-dict payloads, and
# (approvals stream only) event types outside the allow-list.
# ---------------------------------------------------------------------------


def test_stream_approvals_skips_noise_and_forwards_allowed_event() -> None:
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.close = AsyncMock()

    async def _listen():  # type: ignore[return]
        yield {"type": "subscribe"}  # not a "message" -> skipped
        yield {"type": "message", "data": "{not-json"}  # invalid JSON -> skipped
        yield {"type": "message", "data": "\"just a string\""}  # valid JSON, not dict -> skipped
        yield {"type": "message", "data": '{"type": "irrelevant_event"}'}  # not allow-listed
        yield {"type": "message", "data": '{"type": "goal_complete", "id": "ok"}'}

    pubsub.listen = _listen
    redis = MagicMock()
    redis.pubsub = MagicMock(return_value=pubsub)

    client = TestClient(_make_app(redis=redis), raise_server_exceptions=False)
    resp = client.get("/governance/approvals/stream", headers=_h())
    assert resp.status_code == 200
    assert "goal_complete" in resp.text
    assert "irrelevant_event" not in resp.text


def test_stream_policies_skips_noise_and_forwards_own_tenant_event() -> None:
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.close = AsyncMock()

    async def _listen():  # type: ignore[return]
        yield {"type": "subscribe"}
        yield {"type": "message", "data": "{not-json"}
        yield {"type": "message", "data": "42"}  # valid JSON, not a dict
        yield {
            "type": "message",
            "data": f'{{"tenant_id": "{_TENANT_ID}", "action": "updated"}}',
        }

    pubsub.listen = _listen
    redis = MagicMock()
    redis.pubsub = MagicMock(return_value=pubsub)

    client = TestClient(_make_app(redis=redis), raise_server_exceptions=False)
    resp = client.get("/governance/policies/stream", headers=_h())
    assert resp.status_code == 200
    assert "policy_changed" in resp.text


# ---------------------------------------------------------------------------
# emergency_stop: exceptions in each sub-step must be swallowed, never
# aborting the overall stop sequence.
# ---------------------------------------------------------------------------


def test_emergency_stop_goal_cancel_exception_is_swallowed() -> None:
    goal_service = MagicMock()
    goal_record = MagicMock()
    goal_record.tenant_id = _TENANT_ID
    goal_record.status = "running"
    goal_service._goals = {"goal-1": goal_record}
    goal_service.cancel_goal = AsyncMock(side_effect=RuntimeError("cancel exploded"))

    client = TestClient(
        _make_app(goal_service=goal_service, ctx=_ADMIN_CTX), raise_server_exceptions=False
    )
    resp = client.post("/governance/emergency-stop", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["cancelled_goals"] == 0


class _ExplodingGoals:
    """dict-like stand-in whose `.items()` raises, simulating an internal
    store failure while merely *enumerating* running goals (distinct from a
    failure cancelling one, which is a separate, inner try/except)."""

    def items(self) -> Any:
        raise RuntimeError("store corrupted")


def test_emergency_stop_goal_enumeration_exception_is_swallowed() -> None:
    goal_service = MagicMock()
    goal_service._goals = _ExplodingGoals()

    client = TestClient(
        _make_app(goal_service=goal_service, ctx=_ADMIN_CTX), raise_server_exceptions=False
    )
    resp = client.post("/governance/emergency-stop", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["cancelled_goals"] == 0


def test_emergency_stop_redis_publish_exception_is_swallowed() -> None:
    redis = MagicMock()
    redis.publish = AsyncMock(side_effect=RuntimeError("redis down"))
    redis.set = AsyncMock()

    client = TestClient(
        _make_app(redis=redis, ctx=_ADMIN_CTX), raise_server_exceptions=False
    )
    resp = client.post("/governance/emergency-stop", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["celery_signal_sent"] is True


def test_emergency_stop_reject_pending_exception_is_swallowed() -> None:
    gateway = HITLGateway()
    gateway.request_approval(
        goal_id="g-stop", action="risky", risk_level="high", tenant_ctx=_ADMIN_CTX,
    )
    gateway.reject = AsyncMock(side_effect=RuntimeError("reject exploded"))

    client = TestClient(
        _make_app(hitl=gateway, ctx=_ADMIN_CTX), raise_server_exceptions=False
    )
    resp = client.post("/governance/emergency-stop", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["rejected_approvals"] == 0


def test_emergency_stop_pending_approvals_enumeration_exception_is_swallowed() -> None:
    """A failure just listing pending approvals (not merely rejecting one)
    must not blow up the whole emergency-stop request."""
    gateway = HITLGateway()
    gateway.list_pending = MagicMock(side_effect=RuntimeError("gateway store corrupted"))

    client = TestClient(
        _make_app(hitl=gateway, ctx=_ADMIN_CTX), raise_server_exceptions=False
    )
    resp = client.post("/governance/emergency-stop", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["rejected_approvals"] == 0


def test_emergency_stop_audit_log_exception_is_swallowed() -> None:
    audit = AuditLog()
    audit.record = MagicMock(side_effect=RuntimeError("audit sink down"))

    client = TestClient(
        _make_app(audit=audit, ctx=_ADMIN_CTX), raise_server_exceptions=False
    )
    resp = client.post("/governance/emergency-stop", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["status"] == "emergency_stop_activated"
