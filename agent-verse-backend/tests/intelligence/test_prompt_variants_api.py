"""Behavioral tests for /intelligence/prompt-variants API endpoints.

All tests use the signed_up_client fixture (creates a tenant and sets the API key header).
"""
from __future__ import annotations

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

VARIANT_PAYLOAD = {
    "key": "planner",
    "name": "Test Challenger",
    "prompt_text": "You are an excellent planner. Think step by step.",
}


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_variants_requires_auth(app) -> None:
    """GET /intelligence/prompt-variants must return 401 without an API key."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/intelligence/prompt-variants")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_create_variant_registers_with_optimizer(signed_up_client) -> None:
    """POST creates a new challenger variant and it appears in the listing."""
    # Create
    r = await signed_up_client.post("/intelligence/prompt-variants", json=VARIANT_PAYLOAD)
    assert r.status_code == 201
    body = r.json()
    assert body["key"] == "planner"
    assert body["name"] == "Test Challenger"
    assert body["is_control"] is False
    variant_id = body["id"]
    assert variant_id  # non-empty UUID string

    # Verify it appears in list
    r2 = await signed_up_client.get("/intelligence/prompt-variants?key=planner")
    assert r2.status_code == 200
    variants = r2.json()
    ids = [v["id"] for v in variants]
    assert variant_id in ids


@pytest.mark.anyio
async def test_promote_variant(signed_up_client) -> None:
    """POST /{id}/promote marks the variant as control and sets promoted_at."""
    # Create a challenger variant
    r = await signed_up_client.post("/intelligence/prompt-variants", json=VARIANT_PAYLOAD)
    assert r.status_code == 201
    variant_id = r.json()["id"]

    # Promote it
    r2 = await signed_up_client.post(f"/intelligence/prompt-variants/{variant_id}/promote")
    assert r2.status_code == 200
    body = r2.json()
    assert body["promoted"] is True
    assert body["id"] == variant_id
    assert body["promoted_at"] is not None

    # Verify it shows as control in the listing
    r3 = await signed_up_client.get("/intelligence/prompt-variants?key=planner")
    variants = r3.json()
    promoted = next((v for v in variants if v["id"] == variant_id), None)
    assert promoted is not None
    assert promoted["is_control"] is True


@pytest.mark.anyio
async def test_get_report_returns_scores(signed_up_client) -> None:
    """GET /{id}/report returns score fields for a known variant."""
    # Create a variant
    r = await signed_up_client.post("/intelligence/prompt-variants", json=VARIANT_PAYLOAD)
    assert r.status_code == 201
    variant_id = r.json()["id"]

    # Fetch the report
    r2 = await signed_up_client.get(f"/intelligence/prompt-variants/{variant_id}/report")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == variant_id
    assert body["key"] == "planner"
    assert "run_count" in body
    assert "mean_score" in body
    assert "p95_score" in body


@pytest.mark.anyio
async def test_delete_variant(signed_up_client) -> None:
    """DELETE /{id} removes the variant; subsequent list no longer contains it."""
    # Create a variant
    r = await signed_up_client.post("/intelligence/prompt-variants", json=VARIANT_PAYLOAD)
    assert r.status_code == 201
    variant_id = r.json()["id"]

    # Delete it
    r2 = await signed_up_client.delete(f"/intelligence/prompt-variants/{variant_id}")
    assert r2.status_code == 204

    # Verify it is gone
    r3 = await signed_up_client.get("/intelligence/prompt-variants?key=planner")
    variants = r3.json()
    ids = [v["id"] for v in variants]
    assert variant_id not in ids
