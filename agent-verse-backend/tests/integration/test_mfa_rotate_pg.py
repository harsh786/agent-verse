"""MFA-KEY-RING on real Postgres: ``agentverse mfa-rotate`` (rotate_mfa_secrets)
re-seals every tenant's TOTP secret with the current SECRET_KEY, after which
SECRET_KEY_PREVIOUS can be dropped; dry run writes nothing; a re-run is a no-op;
a secret no key opens fails the run (previous keys must stay)."""

from __future__ import annotations

import base64
import uuid
from typing import Any

import pytest

from tests.integration.test_vault_rotate_all_stores_pg import _insert

pytestmark = pytest.mark.integration

OLD, NEW = "mfa-rotate-old-key-0123456789abcdef", "mfa-rotate-new-key-fedcba9876543210"


@pytest.fixture
async def env(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api import mfa_crypto

    monkeypatch.setenv("SECRET_KEY", OLD)
    monkeypatch.delenv("SECRET_KEY_PREVIOUS", raising=False)
    tenants = ["mfa-a-" + uuid.uuid4().hex[:6], "mfa-b-" + uuid.uuid4().hex[:6]]
    secrets = {t: f"TOTPSECRET{i}" for i, t in enumerate(tenants)}
    engine = create_async_engine(pg_url)
    async with engine.begin() as conn:
        for t in tenants:
            await _insert(conn, "tenants", {"id": t, "name": t, "email": f"{t}@x.io"})
        await _insert(
            conn, "tenant_mfa",
            {"tenant_id": tenants[0], "enabled": True,
             "encrypted_secret": mfa_crypto.encrypt_secret(secrets[tenants[0]])},
        )
        await _insert(  # legacy plaintext-fallback row
            conn, "tenant_mfa",
            {"tenant_id": tenants[1], "enabled": True,
             "encrypted_secret": base64.b64encode(secrets[tenants[1]].encode()).decode()
             + ".b64"},
        )
    # Rotate: the new key is current, the old one is kept for decryption.
    monkeypatch.setenv("SECRET_KEY", NEW)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", OLD)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), tenants, secrets
    finally:
        await engine.dispose()


async def _stored(db: Any, tenant: str) -> str:
    from sqlalchemy import text

    async with db() as s, s.begin():
        return str(
            (
                await s.execute(
                    text("SELECT encrypted_secret FROM tenant_mfa WHERE tenant_id = :t"),
                    {"t": tenant},
                )
            ).scalar_one()
        )


async def test_rotation_reseals_every_secret_so_previous_keys_can_go(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import mfa_crypto

    db, tenants, secrets = env
    before = {t: await _stored(db, t) for t in tenants}

    dry = await mfa_crypto.rotate_mfa_secrets(system_db=db, tenant_db=db, dry_run=True)
    assert dry["status"] == "dry_run" and dry["would_rotate"] >= 2 and dry["unreadable"] == 0
    assert {t: await _stored(db, t) for t in tenants} == before

    events: list[dict[str, Any]] = []
    done = await mfa_crypto.rotate_mfa_secrets(
        system_db=db, tenant_db=db, batch_size=1, progress=events.append
    )
    assert done["status"] == "complete" and done["previous_keys_retirable"] is True
    assert events and all(e["store"] == "mfa_secrets" for e in events)

    monkeypatch.delenv("SECRET_KEY_PREVIOUS")
    for t in tenants:
        assert mfa_crypto.decrypt_secret(await _stored(db, t)) == secrets[t]

    again = await mfa_crypto.rotate_mfa_secrets(system_db=db, tenant_db=db)
    assert again["status"] == "complete"


async def test_a_secret_no_key_opens_fails_the_run(env: Any) -> None:
    from sqlalchemy import text

    from app.api import mfa_crypto

    db, tenants, _ = env
    async with db() as s, s.begin():
        await s.execute(
            text("UPDATE tenant_mfa SET encrypted_secret = :c WHERE tenant_id = :t"),
            {"c": "gAAAAAnot-a-valid-fernet-token", "t": tenants[0]},
        )
    try:
        result = await mfa_crypto.rotate_mfa_secrets(system_db=db, tenant_db=db, dry_run=True)
        assert result["status"] == "dry_run" and result["unreadable"] >= 1
    finally:  # the testcontainer DB is shared by the session
        async with db() as s, s.begin():
            await s.execute(
                text("DELETE FROM tenant_mfa WHERE tenant_id = :t"), {"t": tenants[0]}
            )
