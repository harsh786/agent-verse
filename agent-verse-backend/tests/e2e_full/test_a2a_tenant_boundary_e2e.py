"""e2e_full: A2A inbound tasks run as the authenticated caller, never as a fixed tenant.

``POST /a2a/tasks`` requires a tenant API key, but the handler ignored the
authenticated tenant and executed every inbound goal as ``A2A_TENANT_ID`` on the
PROFESSIONAL plan. Any tenant holding a valid key — a free one included — could
therefore run goals inside another tenant's context (its knowledge, tools,
connectors and budget) at an escalated plan, and could not even see its own task
afterwards, while the A2A tenant saw everyone's.

Also pinned: the HMAC check no longer fails open in production, and every task
read is scoped to its owner.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _tenant(app: Any, client: Any) -> tuple[Any, str]:
    from httpx import ASGITransport, AsyncClient

    email = f"a2a-{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post("/tenants/signup", json={"name": "A2A", "email": email})
    assert r.status_code == 201, r.text
    body = r.json()
    c = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": body["api_key"]},
    )
    return c, str(body["tenant_id"])


async def test_inbound_task_executes_as_the_caller_not_a_fixed_tenant(
    app: Any, client: Any, monkeypatch: pytest.MonkeyPatch, _reset_signup_rate_limit: None
) -> None:
    victim, victim_tid = await _tenant(app, client)
    attacker, attacker_tid = await _tenant(app, client)
    # The configuration under which the bug was exploitable: a platform A2A
    # tenant is configured, and an unrelated tenant calls the endpoint.
    monkeypatch.setenv("A2A_TENANT_ID", victim_tid)
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)

    submitted: list[Any] = []
    gs = app.state.goal_service
    original = gs.submit_goal

    async def _spy(*args: Any, **kwargs: Any) -> Any:
        submitted.append(kwargs.get("tenant_ctx"))
        return await original(*args, **kwargs)

    monkeypatch.setattr(gs, "submit_goal", _spy)

    async with attacker:
        resp = await attacker.post(
            "/a2a/tasks", json={"goal": "Summarise the victim's private knowledge base."}
        )
        assert resp.status_code == 202, resp.text
        task_id = resp.json()["task_id"]

        import asyncio

        for _ in range(50):
            if submitted:
                break
            await asyncio.sleep(0.1)
        assert submitted, "the inbound goal was never submitted"
        ctx = submitted[0]
        assert ctx.tenant_id == attacker_tid, (
            f"inbound A2A goal executed as tenant {ctx.tenant_id!r} — the configured "
            "A2A tenant — instead of the authenticated caller"
        )
        assert ctx.api_key_id != "a2a-inbound", (
            "the goal ran under the synthetic A2A context (PROFESSIONAL plan) rather "
            "than the caller's own authenticated context and plan"
        )

        # The caller can see its own task …
        own = await attacker.get(f"/a2a/tasks/{task_id}")
        assert own.status_code == 200, own.text
        assert any(t["task_id"] == task_id for t in (await attacker.get("/a2a/tasks")).json())

    # … and the (formerly impersonated) tenant cannot.
    async with victim:
        foreign = await victim.get(f"/a2a/tasks/{task_id}")
        assert foreign.status_code == 404
        assert all(t["task_id"] != task_id for t in (await victim.get("/a2a/tasks")).json())


async def test_unsigned_inbound_is_refused_in_production(
    app: Any, tenant_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        resp = await tenant_client.post("/a2a/tasks", json={"goal": "anything"})
    finally:
        monkeypatch.setenv("ENVIRONMENT", "development")
        get_settings.cache_clear()
    assert resp.status_code == 503, (
        f"production accepted an unsigned A2A task ({resp.status_code}) — the HMAC "
        "check fails open when A2A_SHARED_SECRET is unset"
    )


async def test_signature_is_verified_when_a_secret_is_configured(
    tenant_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import hmac
    import json

    monkeypatch.setenv("A2A_SHARED_SECRET", "s3cret-for-tests")
    body = json.dumps({"goal": "signed task"}).encode()

    bad = await tenant_client.post(
        "/a2a/tasks",
        content=body,
        headers={"content-type": "application/json", "X-A2A-Signature": "sha256=deadbeef"},
    )
    assert bad.status_code == 401

    sig = hmac.new(b"s3cret-for-tests", body, hashlib.sha256).hexdigest()
    good = await tenant_client.post(
        "/a2a/tasks",
        content=body,
        headers={"content-type": "application/json", "X-A2A-Signature": f"sha256={sig}"},
    )
    assert good.status_code == 202, good.text
