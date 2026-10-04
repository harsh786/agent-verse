"""a08-F177-04 / a08-F180-05: every mutating /v1/org route enforces an org role.

The router-wide guard only checks that the caller's tenant owns the org, so a
``viewer``-role key could create departments, edit missions, launch goals via
``/command``, and create + dispatch missions via ``/missions/execute`` and
``/missions/batch``. Each mutating route now carries ``require_org_role``; the
only exceptions are the stateless estimate endpoints listed in ``READ_ONLY``.
"""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.org.rbac import OrgRole

# ``app.org`` re-exports the APIRouter as ``router``, shadowing the module.
org_router_module = importlib.import_module("app.org.router")

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# POST endpoints that compute an estimate and change nothing.
READ_ONLY = {"org_twin_simulate", "org_twin_what_if", "org_preview_mission"}

EXPECTED_MINIMUM: dict[str, str] = {
    "create_organization": OrgRole.ORG_ADMIN,
    "org_compose": OrgRole.ORG_ADMIN,
    "create_department": OrgRole.DEPT_ADMIN,
    "update_department": OrgRole.DEPT_ADMIN,
    "update_mission": OrgRole.TEAM_LEAD,
    "update_mission_status": OrgRole.TEAM_LEAD,
    "create_task": OrgRole.TEAM_LEAD,
    "update_task_status": OrgRole.TEAM_LEAD,
    "create_team": OrgRole.DEPT_ADMIN,
    "org_graphify_start": OrgRole.TEAM_LEAD,
    "org_create_role": OrgRole.ORG_ADMIN,
    "org_update_role": OrgRole.ORG_ADMIN,
    "org_delete_role": OrgRole.ORG_ADMIN,
    "org_universal_command": OrgRole.TEAM_LEAD,
    "org_team_lifecycle_transition": OrgRole.DEPT_ADMIN,
    "org_batch_create_missions": OrgRole.TEAM_LEAD,
    "org_graph_snapshot": OrgRole.TEAM_LEAD,
    "org_dept_memory_add": OrgRole.TEAM_LEAD,
    "org_upload_attachment": OrgRole.TEAM_LEAD,
    "org_create_mission_execute": OrgRole.TEAM_LEAD,
    "org_create_schedule": OrgRole.TEAM_LEAD,
    "org_toggle_schedule": OrgRole.TEAM_LEAD,
    "org_delete_schedule": OrgRole.TEAM_LEAD,
    "org_approve_schedule_publishing": OrgRole.DEPT_ADMIN,
    "org_finalize_mission": OrgRole.TEAM_LEAD,
}


def _mutating_routes() -> list[APIRoute]:
    return [
        r
        for r in org_router_module.router.routes
        if isinstance(r, APIRoute) and set(r.methods or ()) & MUTATING
    ]


def _role_minimum(route: APIRoute) -> str | None:
    """The ``minimum_role`` of the route's require_org_role dependency, if any."""
    for dep in route.dependant.dependencies:
        call = dep.call
        if call is not None and getattr(call, "__qualname__", "").startswith(
            "require_org_role."
        ):
            return str(inspect.getclosurevars(call).nonlocals["minimum_role"])
    return None


def test_every_mutating_org_route_requires_an_org_role() -> None:
    missing = [
        f"{sorted(r.methods or ())} {r.path} ({r.endpoint.__name__})"
        for r in _mutating_routes()
        if r.endpoint.__name__ not in READ_ONLY and _role_minimum(r) is None
    ]
    assert missing == [], "mutating org routes without require_org_role:\n" + "\n".join(
        missing
    )


@pytest.mark.parametrize(("endpoint", "minimum"), sorted(EXPECTED_MINIMUM.items()))
def test_route_minimum_role(endpoint: str, minimum: str) -> None:
    routes = [r for r in _mutating_routes() if r.endpoint.__name__ == endpoint]
    assert routes, f"{endpoint} is not a mutating org route"
    for route in routes:
        assert _role_minimum(route) == minimum


def _client(roles: tuple[str, ...]) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(
            tenant_id="t-rbac", roles=roles, api_key_id="k-rbac", scopes=()
        )
        return await call_next(request)

    app.include_router(org_router_module.router)

    async def _owned() -> None:
        return None

    async def _service() -> Any:
        # A service that must never be used: RBAC refuses before any handler work.
        yield MagicMock(name="org-service-must-not-be-called")

    app.dependency_overrides[org_router_module._verify_org_ownership] = _owned
    app.dependency_overrides[org_router_module.get_org_service] = _service
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path",
    [
        "/v1/org/o1/missions/execute",
        "/v1/org/o1/missions/batch",
        "/v1/org/o1/command",
        "/v1/org/o1/departments",
        "/v1/org/o1/schedules",
        "/v1/org/o1/roles",
    ],
)
def test_viewer_key_is_refused_before_any_work(path: str) -> None:
    resp = _client(("viewer",)).post(path, json={})
    assert resp.status_code == 403, resp.text


def test_team_lead_passes_the_role_check_on_mission_dispatch() -> None:
    # Past RBAC the empty body fails validation (422) — never 403.
    resp = _client(("team_lead",)).post("/v1/org/o1/missions/execute", json={})
    assert resp.status_code != 403, resp.text
