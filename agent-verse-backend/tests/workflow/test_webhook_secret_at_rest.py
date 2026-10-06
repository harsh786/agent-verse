"""B2-OPEN-1: a workflow webhook ``hmac_secret`` is vault-encrypted at rest.

It sat in the workflow definition JSON in clear — in ``workflows.definition``,
its run-engine mirror, every version snapshot and every workflow API response.
Now the store seals it (``enc:v1:``, tenant envelope key when the tenant has
one), signature verification opens it, responses mask it, a masked value on
update keeps the stored secret, and a legacy plaintext definition keeps
verifying and is re-sealed on its next read.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import pytest
import yaml

from app.workflow import webhook_secrets as ws
from tests.workflow.test_webhook_hardening import _T, env  # noqa: F401  (fixture)

SECRET = "wf-hmac-" + "s" * 24
NEW_SECRET = "wf-hmac-" + "n" * 24


def _sig(secret: str, data: bytes) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-AgentVerse-Signature": "sha256="
        + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest(),
    }


def _definition(secret: str, *, plural: bool = False) -> dict[str, Any]:
    trigger = {"type": "webhook", "webhook": {"auth": "hmac", "hmac_secret": secret}}
    if plural:
        return {"name": "signed", "steps": [], "triggers": [trigger]}
    return {"name": "signed", "steps": [], "trigger": trigger}


def _stored(env: dict[str, Any], wid: str) -> dict[str, Any]:  # noqa: F811
    return dict(env["svc"]._store._mem[wid]["definition"])


def _secret_of(definition: dict[str, Any]) -> Any:
    trig = definition.get("trigger") or definition["triggers"][0]
    return trig["webhook"]["hmac_secret"]


async def _create(env: dict[str, Any], definition: dict[str, Any], publish: bool = True) -> str:  # noqa: F811
    svc = env["svc"]
    wf = await svc.create(tenant_id=_T, name="signed", definition=definition)
    wid = str(wf["id"])
    if publish:
        await svc.publish(tenant_id=_T, workflow_id=wid)
    return wid


def _hook(env: dict[str, Any], wid: str) -> str:  # noqa: F811
    return str(env["client"].get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"])


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_seal_open_mask_round_trip() -> None:
    for plural in (False, True):
        sealed = ws.seal_plaintext_secrets(_definition(SECRET, plural=plural))
        value = _secret_of(sealed)
        assert ws.is_sealed(value) and SECRET not in value
        assert ws.open_secret(value) == SECRET
        assert not ws.has_plaintext_secret(sealed)
        assert ws.seal_plaintext_secrets(sealed) == sealed  # idempotent
        assert _secret_of(ws.redact_definition(sealed)) == ws.MASK
    # a flat (DSL-less) config is covered too
    flat = {"triggers": [{"type": "api", "auth": "hmac", "hmac_secret": SECRET}]}
    assert ws.is_sealed(ws.seal_plaintext_secrets(flat)["triggers"][0]["hmac_secret"])
    # empty / missing secrets stay as they are
    assert ws.seal_plaintext_secrets(_definition("")) == _definition("")
    assert ws.open_secret("") == "" and ws.open_secret(ws.MASK) == ""
    assert ws.open_secret(SECRET) == SECRET  # legacy plaintext passes through


def test_tenant_envelope_key_seals_and_is_required_to_open() -> None:
    from app.providers.vault import CredentialVault

    tenant_vault = CredentialVault.from_byok(os.urandom(32))
    value = _secret_of(ws.seal_plaintext_secrets(_definition(SECRET), tenant_vault))
    assert value.startswith(ws.ENC_PREFIX + "tv1:")
    assert ws.needs_tenant_vault(value)
    assert ws.open_secret(value, tenant_vault) == SECRET
    with pytest.raises(ws.WebhookSecretError):
        ws.open_secret(value)  # never opened with another key (fail closed)


def test_masked_value_keeps_the_stored_secret() -> None:
    stored = ws.seal_plaintext_secrets(_definition(SECRET))
    edited = ws.merge_masked_secrets(ws.redact_definition(stored), stored)
    assert _secret_of(edited) == _secret_of(stored)
    # A masked value with nothing stored becomes "" (hmac then fails closed).
    assert _secret_of(ws.merge_masked_secrets(_definition(ws.MASK), {})) == ""


def test_vault_rotation_and_tenant_compaction_walk_trigger_lists() -> None:
    from app.providers.tenant_key_compaction import TenantCompaction, _Sealer
    from app.providers.vault import CredentialVault
    from app.providers.vault_rotation import StoreReport, _rotate_source_config

    old, new = CredentialVault("old-master-key-test"), CredentialVault("new-master-key-test")
    sealed_old = ws.ENC_PREFIX + old.encrypt(SECRET)
    definition = _definition(sealed_old, plural=True)
    report = StoreReport()
    rotated = _rotate_source_config(definition, old, new, report)
    assert rotated is not None and report.rotated == 1
    assert new.decrypt(_secret_of(rotated)[len(ws.ENC_PREFIX):]) == SECRET

    k_old, k_new = os.urandom(32), os.urandom(32)
    ring_old = CredentialVault.from_byok(k_old)
    tv1 = _definition(ws.ENC_PREFIX + "tv1:" + ring_old.encrypt(SECRET), plural=True)
    sealer = _Sealer([k_new, k_old], TenantCompaction(tenant_id=_T))
    resealed = sealer.reseal_source(tv1)
    assert resealed is not None
    body = _secret_of(resealed)[len(ws.ENC_PREFIX + "tv1:"):]
    assert CredentialVault.from_byok(k_new).decrypt(body) == SECRET


# ── store + API ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_secret_is_sealed_at_rest_and_masked_in_responses(env: dict[str, Any]) -> None:  # noqa: F811
    client = env["client"]
    wid = await _create(env, _definition(SECRET))

    stored = _secret_of(_stored(env, wid))
    assert ws.is_sealed(stored) and SECRET not in stored

    detail = client.get(f"/api/v1/workflows/{wid}")
    assert detail.status_code == 200
    assert _secret_of(detail.json()["definition"]) == ws.MASK
    assert SECRET not in detail.text and stored not in detail.text

    from app.workflow.router_versions import router as versions_router

    client.app.include_router(versions_router, prefix="/api/v1")  # type: ignore[attr-defined]
    exported = client.get(f"/api/v1/workflows/{wid}/yaml")
    assert exported.status_code == 200 and SECRET not in exported.text
    assert stored not in exported.text

    # ... and the signed delivery still verifies against the sealed secret.
    body = b'{"batch": "B-1"}'
    assert client.post(_hook(env, wid), content=body, headers=_sig(SECRET, body)).status_code == 200
    assert client.post(_hook(env, wid), content=body, headers=_sig("x", body)).status_code == 401


@pytest.mark.asyncio
async def test_round_tripping_the_masked_definition_keeps_the_secret(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc = env["client"], env["svc"]
    wid = await _create(env, _definition(SECRET), publish=False)
    shown = client.get(f"/api/v1/workflows/{wid}").json()["definition"]
    assert _secret_of(shown) == ws.MASK

    shown["steps"] = []
    r = client.patch(f"/api/v1/workflows/{wid}", json={"definition": shown})
    assert r.status_code == 200, r.text
    assert _secret_of(r.json()["definition"]) == ws.MASK
    await svc.publish(tenant_id=_T, workflow_id=wid)
    body = b"{}"
    assert client.post(_hook(env, wid), content=body, headers=_sig(SECRET, body)).status_code == 200


@pytest.mark.asyncio
async def test_a_new_secret_replaces_the_old_one(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc = env["client"], env["svc"]
    wid = await _create(env, _definition(SECRET), publish=False)
    r = client.patch(f"/api/v1/workflows/{wid}", json={"definition": _definition(NEW_SECRET)})
    assert r.status_code == 200, r.text
    assert ws.open_secret(_secret_of(_stored(env, wid))) == NEW_SECRET
    await svc.publish(tenant_id=_T, workflow_id=wid)
    body = b"{}"
    assert client.post(_hook(env, wid), content=body, headers=_sig(SECRET, body)).status_code == 401
    assert client.post(_hook(env, wid), content=body, headers=_sig(NEW_SECRET, body)).status_code == 200


@pytest.mark.asyncio
async def test_legacy_plaintext_definition_keeps_working_and_is_resealed(
    env: dict[str, Any],  # noqa: F811
) -> None:
    client, svc = env["client"], env["svc"]
    wid = await _create(env, _definition(""))
    # A row written before B2-OPEN-1: the secret in clear.
    svc._store._mem[wid]["definition"] = _definition(SECRET)

    body = b'{"n": 1}'
    assert client.post(_hook(env, wid), content=body, headers=_sig(SECRET, body)).status_code == 200
    # The read on the way re-sealed it in place (no version bump).
    stored = _secret_of(_stored(env, wid))
    assert ws.is_sealed(stored) and ws.open_secret(stored) == SECRET
    assert client.post(_hook(env, wid), content=body, headers=_sig(SECRET, body)).status_code == 200


@pytest.mark.asyncio
async def test_unreadable_secret_refuses_the_delivery_with_503(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc, runner = env["client"], env["svc"], env["runner"]
    wid = await _create(env, _definition(""))
    svc._store._mem[wid]["definition"] = _definition(ws.ENC_PREFIX + "not-a-fernet-token")
    body = b"{}"
    r = client.post(_hook(env, wid), content=body, headers=_sig("", body))
    assert r.status_code == 503
    assert runner.runs == []


@pytest.mark.asyncio
async def test_visual_builder_api_masks_the_secret(env: dict[str, Any]) -> None:  # noqa: F811
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
    created = client.post("/workflows", json={"name": "b", "definition": _definition(SECRET)})
    assert created.status_code == 201, created.text
    assert _secret_of(created.json()["definition"]) == ws.MASK
    wid = created.json()["id"]
    assert ws.is_sealed(_secret_of(store._mem[wid]["definition"]))
    listed = client.get("/workflows").json()
    assert all(SECRET not in str(w["definition"]) for w in listed)
    # PUT with the masked definition (what the builder saves back) keeps it.
    shown = client.get(f"/workflows/{wid}").json()["definition"]
    assert client.put(
        f"/workflows/{wid}", json={"name": "b", "definition": shown}
    ).status_code == 204
    assert ws.open_secret(_secret_of(store._mem[wid]["definition"])) == SECRET


@pytest.mark.asyncio
async def test_version_snapshot_and_response_never_carry_the_secret() -> None:
    from tests.workflow.test_run_store_mocked import FakeDBFactory, FakeResult

    from app.workflow.run_store import PostgresWorkflowRunStore

    db = FakeDBFactory([[FakeResult(), FakeResult(first=(None,))]])
    store = PostgresWorkflowRunStore(db)
    sealed = ws.seal_plaintext_secrets(_definition(SECRET))
    await store.record_definition_version(
        "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002",
        version="2", definition=sealed,
    )
    params = db.sessions[0].executed[1][1]
    assert SECRET not in params["yaml"] and _secret_of(sealed) not in params["yaml"]
    assert ws.MASK in params["yaml"]
    assert _secret_of(__import__("json").loads(params["def"])) == _secret_of(sealed)

    # A snapshot recorded before B2-OPEN-1 (plaintext in JSON and YAML) is masked
    # in the version response.
    from app.workflow.router_versions import _masked_version

    legacy = _definition(SECRET)
    shown = _masked_version(
        {"version": "1", "definition": legacy, "definition_yaml": yaml.safe_dump(legacy)}
    )
    assert _secret_of(shown["definition"]) == ws.MASK
    assert SECRET not in shown["definition_yaml"]
