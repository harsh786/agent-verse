"""e2e_full: Skills (composable instruction packs) and the Skills Runtime, wired.

a10-F228-04: Skills had route/unit tests and Postgres app-role tests built on a
hand-assembled FastAPI app, but nothing proved them through the real
``create_app(manage_pools=True)`` lifespan (DB-backed stores, auth middleware,
RLS on the least-privilege app role). This drives both APIs end to end:

* ``/skills`` — a tenant's custom skill persists, is listed only for its owner,
  cannot be deleted by another tenant, and is gone after the owner deletes it.
* ``/skills-runtime`` — a tenant skill is tenant-scoped; disabling a platform
  skill is persisted (it survives the module caches being dropped, i.e. a
  restart / another replica) and the execute route refuses it with 403 before
  any LLM call; re-enabling restores it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _signup(app: Any, client: Any) -> AsyncClient:
    email = f"skills-{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post("/tenants/signup", json={"name": "Skills", "email": email})
    assert r.status_code == 201, r.text
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": r.json()["api_key"]},
    )


def _drop_process_state() -> None:
    """Model a restart / another replica: forget every per-process skill cache."""
    from app.api import skills_runtime
    from app.skills_runtime.executor import permission_checker

    skills_runtime._enabled_skills.clear()
    permission_checker._disabled.clear()


async def test_custom_skills_persist_and_are_tenant_scoped(app: Any, client: Any) -> None:
    owner, other = await _signup(app, client), await _signup(app, client)
    try:
        name = f"release-notes-{uuid.uuid4().hex[:6]}"
        created = await owner.post(
            "/skills",
            json={
                "name": name,
                "description": "Write release notes from merged PRs",
                "trigger_hints": ["release notes", "changelog"],
                "instructions": "Group changes by area; one line per PR.",
            },
        )
        assert created.status_code in (200, 201), created.text
        sid = created.json()["id"]

        mine = (await owner.get("/skills")).json()["skills"]
        assert any(s["id"] == sid and not s["is_platform"] for s in mine)
        theirs = (await other.get("/skills")).json()["skills"]
        assert all(s["id"] != sid for s in theirs)

        assert (await other.delete(f"/skills/{sid}")).status_code == 404
        assert any(s["id"] == sid for s in (await owner.get("/skills")).json()["skills"])

        assert (await owner.delete(f"/skills/{sid}")).status_code == 200
        assert all(s["id"] != sid for s in (await owner.get("/skills")).json()["skills"])
    finally:
        await owner.aclose()
        await other.aclose()


async def test_skills_runtime_tenant_skill_and_durable_disable(app: Any, client: Any) -> None:
    owner, other = await _signup(app, client), await _signup(app, client)
    try:
        created = await owner.post(
            "/skills-runtime",
            json={
                "name": "triage",
                "description": "Triage incoming incidents",
                "trigger_hints": ["incident"],
                "instructions": "Classify severity, then route.",
            },
        )
        assert created.status_code == 200, created.text
        tsid = created.json()["skill_id"]
        assert (await owner.get(f"/skills-runtime/{tsid}")).status_code == 200
        assert (await other.get(f"/skills-runtime/{tsid}")).status_code == 404

        listed = (await owner.get("/skills-runtime")).json()["skills"]
        builtin = next(s for s in listed if s.get("is_builtin"))
        bid = builtin["skill_id"]

        assert (await owner.post(f"/skills-runtime/{bid}/disable")).status_code == 200
        _drop_process_state()
        refused = await owner.post(
            f"/skills-runtime/{bid}/execute", json={"input_context": "do the thing"}
        )
        assert refused.status_code == 403, refused.text
        after = (await owner.get("/skills-runtime")).json()["skills"]
        assert next(s for s in after if s["skill_id"] == bid)["enabled"] is False
        # The other tenant is unaffected by the owner's disable.
        theirs = (await other.get("/skills-runtime")).json()["skills"]
        assert next(s for s in theirs if s["skill_id"] == bid)["enabled"] is True

        assert (await owner.post(f"/skills-runtime/{bid}/enable")).status_code == 200
        _drop_process_state()
        again = (await owner.get("/skills-runtime")).json()["skills"]
        assert next(s for s in again if s["skill_id"] == bid)["enabled"] is True
    finally:
        await owner.aclose()
        await other.aclose()
