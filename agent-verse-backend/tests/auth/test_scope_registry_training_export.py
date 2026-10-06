"""a10-F234-01: the training-data export routes have a registered scope.

``/intelligence/export-training-data…`` had no ``ENDPOINT_SCOPES`` entry, and an
unregistered GET passes for every key — so the preview, job status and the job
download (a bulk copy of the tenant's goal transcripts) were open to a key
minted with explicit scopes that exclude goal data.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.auth.scope_enforcement import ROLE_SCOPES, ScopeEnforcementMiddleware

_req = ScopeEnforcementMiddleware._required_scope


@pytest.mark.parametrize(
    ("method", "path", "scope"),
    [
        ("GET", "/intelligence/export-training-data/preview", "goals:read"),
        ("GET", "/intelligence/export-training-data/jobs", "goals:read"),
        ("GET", "/intelligence/export-training-data/jobs/j1", "goals:read"),
        ("GET", "/intelligence/export-training-data/jobs/j1/download", "goals:read"),
        ("POST", "/intelligence/export-training-data", "goals:write"),
        ("POST", "/intelligence/export-training-data/jobs", "goals:write"),
    ],
)
def test_training_export_routes_have_scopes(method: str, path: str, scope: str) -> None:
    assert _req(method, path) == scope


def test_every_mounted_training_export_route_is_scoped() -> None:
    from app.api.training_export import router

    app = FastAPI()
    app.include_router(router)
    missing = [
        (m, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for m in route.methods
        if m not in {"HEAD", "OPTIONS"} and _req(m, route.path) is None
    ]
    assert missing == []


def test_only_writers_may_start_an_export() -> None:
    start = _req("POST", "/intelligence/export-training-data")
    assert start in ROLE_SCOPES["operator"]
    assert start in ROLE_SCOPES["admin"]
    assert start not in ROLE_SCOPES["viewer"]
    assert start not in ROLE_SCOPES["approver"]


def test_other_intelligence_routes_are_unchanged() -> None:
    # The entry is scoped to the export prefix, not all of /intelligence.
    assert _req("GET", "/intelligence/experiments") is None
