"""B2-GAP-3: a masked webhook secret never silently becomes an empty one.

An exported workflow YAML carries ``auth: hmac`` webhooks with the secret masked
(``hmac_secret: '********'``, B2-OPEN-1). Re-importing it created a workflow
whose secret was ``""``: every delivery was refused (401) with nothing telling
the user why. Now an import (or create / update) whose mask has no stored
secret behind it is refused with a 422 naming the fix; nothing is created, no
secret is copied from another workflow and none is generated or echoed.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest
import yaml

from app.workflow import webhook_secrets as ws
from tests.workflow.test_webhook_hardening import _T, env  # noqa: F401  (fixture)

SECRET = "wf-hmac-" + "s" * 24
NEW_SECRET = "wf-hmac-" + "n" * 24


def _definition(secret: str) -> dict[str, Any]:
    return {
        "name": "signed",
        "steps": [],
        "trigger": {"type": "webhook", "webhook": {"auth": "hmac", "hmac_secret": secret}},
    }


def _secret_of(definition: dict[str, Any]) -> Any:
    return definition["trigger"]["webhook"]["hmac_secret"]


def _with_versions(env: dict[str, Any]) -> Any:  # noqa: F811
    from app.workflow.router_versions import router as versions_router

    client = env["client"]
    client.app.include_router(versions_router, prefix="/api/v1")
    return client


def _import(client: Any, text: str) -> Any:
    return client.post(
        "/api/v1/workflows/import-yaml",
        content=text.encode(),
        headers={"Content-Type": "application/x-yaml"},
    )


def test_strict_merge_refuses_an_unresolvable_mask() -> None:
    stored = ws.seal_plaintext_secrets(_definition(SECRET))
    # Resolvable: the stored secret is kept.
    kept = ws.merge_masked_secrets(_definition(ws.MASK), stored, strict=True)
    assert _secret_of(kept) == _secret_of(stored)
    for existing in (None, {}, _definition("")):
        with pytest.raises(ws.MaskedSecretError, match="new secret"):
            ws.merge_masked_secrets(_definition(ws.MASK), existing, strict=True)
    # Non-strict keeps its documented behaviour.
    assert _secret_of(ws.merge_masked_secrets(_definition(ws.MASK), None)) == ""


@pytest.mark.asyncio
async def test_reimporting_an_export_is_refused_with_a_clear_422(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc = _with_versions(env), env["svc"]
    wf = await svc.create(tenant_id=_T, name="signed", definition=_definition(SECRET))
    exported = client.get(f"/api/v1/workflows/{wf['id']}/yaml")
    assert exported.status_code == 200
    text = exported.json() if exported.headers["content-type"].startswith(
        "application/json"
    ) else exported.text
    assert ws.MASK in text and SECRET not in text
    before = len(svc._store._mem)

    r = _import(client, text)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "masked" in detail and "hmac_secret" in detail and "new secret" in detail
    assert SECRET not in r.text
    assert len(svc._store._mem) == before  # nothing was created


@pytest.mark.asyncio
async def test_import_with_a_new_secret_is_sealed_masked_and_verifies(
    env: dict[str, Any],  # noqa: F811
) -> None:
    client, svc = _with_versions(env), env["svc"]
    r = _import(client, yaml.safe_dump(_definition(NEW_SECRET)))
    assert r.status_code == 201, r.text
    assert _secret_of(r.json()["definition"]) == ws.MASK and NEW_SECRET not in r.text
    wid = str(r.json()["id"])
    stored = _secret_of(svc._store._mem[wid]["definition"])
    assert ws.is_sealed(stored) and ws.open_secret(stored) == NEW_SECRET

    await svc.publish(tenant_id=_T, workflow_id=wid)
    hook = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]
    body = b"{}"
    sig = "sha256=" + hmac.new(NEW_SECRET.encode(), body, hashlib.sha256).hexdigest()
    ok = client.post(
        hook, content=body,
        headers={"Content-Type": "application/json", "X-AgentVerse-Signature": sig},
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_create_and_update_with_a_mask_nothing_backs_are_422(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc = env["client"], env["svc"]
    before = len(svc._store._mem)
    created = client.post(
        "/api/v1/workflows", json={"name": "m", "definition": _definition(ws.MASK)}
    )
    assert created.status_code == 422, created.text
    assert "masked" in created.json()["detail"]
    assert len(svc._store._mem) == before

    wf = await svc.create(tenant_id=_T, name="nosecret", definition=_definition(""))
    r = client.patch(f"/api/v1/workflows/{wf['id']}", json={"definition": _definition(ws.MASK)})
    assert r.status_code == 422, r.text
    assert _secret_of(svc._store._mem[str(wf["id"])]["definition"]) == ""  # unchanged


@pytest.mark.asyncio
async def test_visual_builder_api_refuses_an_unbacked_mask(env: dict[str, Any]) -> None:  # noqa: F811
    from fastapi import FastAPI, Request
    from starlette.testclient import TestClient

    from app.api.workflows import router as builder_router
    from app.tenancy.context import PlanTier, TenantContext

    store = env["svc"]._store
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id="k", roles=("admin",)
        )
        request.app.state.workflow_store = store
        request.app.state.workflow_service = env["svc"]
        return await call_next(request)

    app.include_router(builder_router)
    client = TestClient(app)
    r = client.post("/workflows", json={"name": "b", "definition": _definition(ws.MASK)})
    assert r.status_code == 422, r.text
    ok = client.post("/workflows", json={"name": "b", "definition": _definition("")})
    assert ok.status_code == 201
    put = client.put(
        f"/workflows/{ok.json()['id']}", json={"name": "b", "definition": _definition(ws.MASK)}
    )
    assert put.status_code == 422, put.text
