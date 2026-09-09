"""e2e_full: the AI Organization OS DB path must work end-to-end.

Regression guard for migration 0117: the org ORM models declare an ``extra_data``
JSONB column that no migration had added, so every real query against the ten org
tables failed with UndefinedColumnError and the whole feature returned HTTP 500.
This drives the wired endpoints against a freshly-migrated Postgres.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_org_list_and_create_roundtrip(tenant_client: Any) -> None:
    # List must not 500 (it did before extra_data existed).
    listing = await tenant_client.get("/v1/org")
    assert listing.status_code == 200, f"org list failed: {listing.status_code} {listing.text}"
    body = listing.json()
    assert "data" in body and isinstance(body["data"], list)

    # Create an org (exercises INSERT incl. extra_data), then read it back.
    name = f"Org {uuid.uuid4().hex[:8]}"
    created = await tenant_client.post("/v1/org", json={"name": name})
    assert created.status_code in (200, 201), f"org create failed: {created.status_code} {created.text}"
    org_id = created.json()["id"]

    got = await tenant_client.get(f"/v1/org/{org_id}")
    assert got.status_code == 200, f"org get failed: {got.status_code} {got.text}"
    assert got.json()["name"] == name

    listing2 = await tenant_client.get("/v1/org")
    assert org_id in {o["id"] for o in listing2.json()["data"]}


async def test_org_compose_from_nl_builds_a_real_org(tenant_client: Any) -> None:
    """POST /v1/org/compose must build a real org with departments + missions.

    Regression: the router called ``service.compose_from_nl(...)`` but OrgService
    had no such method, so the N2 Org Composer 500'd on every request
    (AttributeError) on the live path — the "describe your org in plain English and
    we build it" feature was entirely dead. This drives the wired endpoint against
    real Postgres and asserts the org, departments, and initial missions persist.
    """
    resp = await tenant_client.post(
        "/v1/org/compose",
        json={
            "description": "A fintech startup building AI-powered cash-flow tools, "
            "with a strong focus on data analytics and customer support.",
            "goals": ["Reach $1M ARR", "Launch the mobile app"],
            "industry": "fintech",
            "autonomy_level": 2,
            "budget_usd": 5000,
        },
    )
    assert resp.status_code == 201, f"compose failed: {resp.status_code} {resp.text}"
    data = resp.json()
    org_id = data["org_id"]
    assert org_id
    assert data["status"] == "ready"
    assert data["composition_method"] == "template"
    # Departments were created (fintech template + a keyword-driven extra or two).
    assert len(data["departments"]) >= 4
    assert all(d["id"] and d["name"] for d in data["departments"])
    # Data/analytics + support keywords in the description add those departments.
    dept_names = {d["name"].lower() for d in data["departments"]}
    assert any("data" in n for n in dept_names)
    assert any("customer" in n or "success" in n for n in dept_names)
    # One initial mission per stated goal.
    assert len(data["initial_missions"]) == 2

    # The composed org + departments + missions are durably persisted on the wired
    # path — read them back through the real endpoints.
    got = await tenant_client.get(f"/v1/org/{org_id}")
    assert got.status_code == 200
    depts = await tenant_client.get(f"/v1/org/{org_id}/departments")
    assert depts.status_code == 200
    assert len(depts.json()) == len(data["departments"])  # bare list response
    missions = await tenant_client.get(f"/v1/org/{org_id}/missions")
    assert missions.status_code == 200
    assert len(missions.json()["data"]) == 2  # CursorPage response
