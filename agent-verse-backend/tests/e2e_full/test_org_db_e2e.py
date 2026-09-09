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
