"""e2e_full: behavioural cross-tenant isolation (D-25 / scopes row 15).

The existing RLS tests assert ``set_config`` SQL strings and migration text, not
the actual guarantee. This proves it behaviourally against real Postgres+RLS:
tenant B cannot read, search, or enumerate tenant A's knowledge — isolation is
enforced at the database, not just in app code.

Two tenants are seeded via their own API keys (a second signup on top of the
session-scoped one — well within the signup rate limit).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_MARKER = "Nyctographic salamander ledger"
_CONTENT = (
    "The Nyctographic salamander ledger records every nocturnal migration of the "
    "protected salamander colonies under seal."
)


async def _signup(client: Any) -> str:
    email = f"rls-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "RLS", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    return str(resp.json()["api_key"])


@pytest.fixture
def _fake_embedder(app: Any) -> Any:
    import contextlib
    import dataclasses

    fake = FakeProvider(embed_dim=768)
    prev = getattr(app.state, "embedder", None)
    app.state.embedder = fake
    gw = getattr(app.state, "retrieval_gateway", None)
    prev_deps = None
    if gw is not None and hasattr(gw, "dependencies"):
        prev_deps = gw.dependencies
        with contextlib.suppress(Exception):
            gw.dependencies = dataclasses.replace(gw.dependencies, embedder=fake)
    try:
        yield
    finally:
        app.state.embedder = prev
        if gw is not None and prev_deps is not None:
            gw.dependencies = prev_deps


async def test_tenant_b_cannot_read_tenant_a_knowledge(
    app: Any, client: Any, _fake_embedder: Any
) -> None:
    from httpx import ASGITransport, AsyncClient

    key_a = await _signup(client)
    key_b = await _signup(client)
    transport = ASGITransport(app=app)

    async with (
        AsyncClient(transport=transport, base_url="http://e2e-full",
                    headers={"X-API-Key": key_a}) as ca,
        AsyncClient(transport=transport, base_url="http://e2e-full",
                    headers={"X-API-Key": key_b}) as cb,
    ):
        # Tenant A creates a collection and ingests a document.
        coll = await ca.post("/knowledge/collections", json={"name": f"a-{uuid.uuid4().hex[:8]}"})
        assert coll.status_code == 201, coll.text
        coll_a = coll.json()["collection_id"]
        ing = await ca.post(
            "/knowledge/ingest",
            json={"collection_id": coll_a, "source_type": "text", "content": _CONTENT},
        )
        assert ing.status_code == 201, ing.text

        # Tenant A can retrieve its own document.
        own = await ca.get(
            "/knowledge/search",
            params={"q": _MARKER, "collection_id": coll_a, "top_k": 5, "threshold": 0.0},
        )
        assert own.status_code == 200 and own.json(), "tenant A must see its own document"

        # ── Isolation: tenant B must NOT reach tenant A's collection ────────────
        # 1. Cannot enumerate A's collection in its own list.
        b_list = await cb.get("/knowledge/collections")
        assert b_list.status_code == 200
        b_ids = {c.get("collection_id") or c.get("id") for c in b_list.json()}
        assert coll_a not in b_ids, "tenant B enumerated tenant A's collection (RLS leak)"

        # 2. Searching A's collection_id as tenant B yields nothing (or is denied),
        #    never A's content.
        leak = await cb.get(
            "/knowledge/search",
            params={"q": _MARKER, "collection_id": coll_a, "top_k": 5, "threshold": 0.0},
        )
        assert leak.status_code in (200, 403, 404), leak.text
        if leak.status_code == 200:
            assert leak.json() == [], "tenant B retrieved tenant A's content (RLS leak)"
