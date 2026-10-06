"""B2-OPEN-1 on real Postgres: workflow webhook secrets are sealed in every copy.

The DB-backed ``_WorkflowStore`` writes ``workflows.definition`` and mirrors it
into ``workflow_definitions.definition_json``; publishing snapshots it into
``workflow_definition_versions``. A new secret must be ciphertext in all of
them; a legacy plaintext row (and its mirror and snapshots) is re-sealed on its
next read; ``agentverse vault-rotate`` re-encrypts all three copies.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.workflows import _WorkflowStore
from app.workflow import webhook_secrets as ws

pytestmark = pytest.mark.integration

SECRET = "wf-hmac-" + "p" * 24


def _definition(secret: str) -> dict[str, Any]:
    return {
        "name": "signed",
        "steps": [],
        "triggers": [{"type": "webhook", "webhook": {"auth": "hmac", "hmac_secret": secret}}],
    }


def _secret_of(definition: Any) -> Any:
    data = json.loads(definition) if isinstance(definition, str) else definition
    return data["triggers"][0]["webhook"]["hmac_secret"]


async def _copies(factory: Any, tenant: str, wid: str) -> dict[str, Any]:
    async with factory() as s:
        row = (
            await s.execute(text("SELECT definition FROM workflows WHERE id = :id"), {"id": wid})
        ).scalar_one()
        mirror = (
            await s.execute(
                text("SELECT definition_json FROM workflow_definitions WHERE id = CAST(:id AS uuid)"),
                {"id": wid},
            )
        ).scalar_one()
        versions = (
            await s.execute(
                text(
                    "SELECT definition_json, definition_yaml FROM workflow_definition_versions "
                    "WHERE workflow_id = CAST(:id AS uuid) AND tenant_id = CAST(:t AS uuid)"
                ),
                {"id": wid, "t": tenant},
            )
        ).all()
    return {"row": row, "mirror": mirror, "versions": versions}


async def test_secret_is_sealed_in_every_copy_and_rotates(pg_url: str) -> None:
    from app.providers.vault import CredentialVault, get_vault
    from app.providers.vault_rotation import PG_STORES, rotate_all_stores

    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = uuid.uuid4().hex
    store = _WorkflowStore()
    store.set_db(factory)
    try:
        async with factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :e, 'free', true)"
                ),
                {"id": tenant, "e": f"{tenant}@example.test"},
            )

        # 1. A new secret is ciphertext in the row and its mirror.
        created = await store.create(tenant, "signed", "", _definition(SECRET))
        wid = str(created["id"])
        copies = await _copies(factory, tenant, wid)
        for name in ("row", "mirror"):
            value = _secret_of(copies[name])
            assert ws.is_sealed(value) and SECRET not in json.dumps(copies[name]), name
        assert await store.open_webhook_secret(tenant, _secret_of(copies["row"])) == SECRET

        # 2. A legacy row: plaintext in the row, the mirror and a snapshot.
        legacy = await store.create(tenant, "legacy", "", _definition(""))
        lid = str(legacy["id"])
        plain = json.dumps(_definition(SECRET))
        async with factory() as s, s.begin():
            await s.execute(
                text("UPDATE workflows SET definition = CAST(:d AS jsonb) WHERE id = :id"),
                {"d": plain, "id": lid},
            )
            await s.execute(
                text(
                    "UPDATE workflow_definitions SET definition_json = CAST(:d AS jsonb) "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"d": plain, "id": lid},
            )
            await s.execute(
                text(
                    "INSERT INTO workflow_definition_versions "
                    "(workflow_id, tenant_id, version, definition_yaml, definition_json, "
                    " published_by) VALUES (CAST(:id AS uuid), CAST(:t AS uuid), '1', :y, "
                    " CAST(:d AS jsonb), gen_random_uuid())"
                ),
                {"id": lid, "t": tenant, "y": yaml.safe_dump(_definition(SECRET)), "d": plain},
            )
        read = await store.get(tenant, lid)
        assert read is not None and ws.is_sealed(_secret_of(read["definition"]))
        copies = await _copies(factory, tenant, lid)
        assert ws.is_sealed(_secret_of(copies["row"]))
        assert ws.is_sealed(_secret_of(copies["mirror"]))
        (snapshot_json, snapshot_yaml), = copies["versions"]
        assert ws.is_sealed(_secret_of(snapshot_json))
        assert SECRET not in snapshot_yaml and ws.MASK in snapshot_yaml
        assert int(read["version"]) == int(legacy["version"])  # no version bump

        # 3. vault-rotate re-encrypts the three workflow copies.
        old = get_vault()
        new = CredentialVault("rotated-master-key-for-b2-open-1-test")
        stores = tuple(s for s in PG_STORES if s.name.startswith("workflow_"))
        assert {s.name for s in stores} == {
            "workflow_webhook_secrets", "workflow_definition_secrets", "workflow_version_secrets"
        }
        result = await rotate_all_stores(
            old=old, new=new, rotation_id=f"b2open1-{tenant[:8]}", system_db=factory,
            pg_stores=stores, record_key_version=False,
        )
        assert result["status"] == "complete", result
        assert sum(r["rotated"] for r in result["stores"].values()) >= 5
        copies = await _copies(factory, tenant, lid)
        for value in (copies["row"], copies["mirror"], copies["versions"][0][0]):
            assert new.decrypt(_secret_of(value)[len(ws.ENC_PREFIX):]) == SECRET
    finally:
        await engine.dispose()
