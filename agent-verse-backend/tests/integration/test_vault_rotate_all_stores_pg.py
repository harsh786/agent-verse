"""ROTATE-ALL-STORES: after ``vault-rotate`` every store opens with ONLY the new key.

Real Postgres (migrated) + real Redis. Seeds one value in every ciphertext store
under the OLD master key, then checks: a dry run changes nothing; an interrupted
batched run resumes from its checkpoint; the completed run leaves every value
readable with the new key alone (so VAULT_PREVIOUS_MASTER_KEYS can be retired) and
unreadable with the old key; a re-run is a no-op; tenant-envelope (``tv1:``)
values are untouched while their wrapping key is re-wrapped.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration

OLD_KEY = "old-master-key-for-rotation-test-0123456789"
NEW_KEY = "new-master-key-for-rotation-test-9876543210"
T1, T2 = "rot-t1-" + uuid.uuid4().hex[:6], "rot-t2-" + uuid.uuid4().hex[:6]


def _fill_value(data_type: str) -> Any:
    if data_type.startswith("timestamp"):
        from datetime import UTC, datetime

        return datetime.now(UTC)
    return {
        "integer": 0,
        "bigint": 0,
        "smallint": 0,
        "boolean": False,
        "double precision": 0.0,
        "real": 0.0,
        "numeric": 0,
        "jsonb": "{}",
        "json": "{}",
        "ARRAY": [],
    }.get(data_type, "x" + uuid.uuid4().hex[:10])  # unique: some text columns are UNIQUE


async def _insert(conn: Any, table: str, values: dict[str, Any]) -> None:
    """INSERT filling every other NOT NULL column without a default with a dummy."""
    from sqlalchemy import text

    cols = (
        await conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :t AND is_nullable = 'NO' AND column_default IS NULL"
            ),
            {"t": table},
        )
    ).fetchall()
    row = dict(values)
    for name, data_type in cols:
        if name not in row and data_type != "USER-DEFINED":
            row[name] = _fill_value(data_type)
    names = ", ".join(row)
    params = ", ".join(
        f"CAST(:{k} AS jsonb)" if isinstance(v, str) and v.startswith(("{", "["))
        and k in ("connection_config", "config", "announcement", "evidence_refs", "metadata",
                  "channel_config")
        else f":{k}"
        for k, v in row.items()
    )
    await conn.execute(text(f"INSERT INTO {table} ({names}) VALUES ({params})"), row)


@pytest.fixture(scope="module")
def isolated_pg_url(pg_url: str) -> Any:
    """A database of this module's own: rotation scans EVERY tenant's rows, so a
    value another test sealed with a different master key (in the shared
    ``pg_url`` database) made the rotation fail honestly in full-suite order."""
    from tests._test_backends import fresh_migrated_database

    with fresh_migrated_database(pg_url) as url:
        yield url


@pytest.fixture
async def env(isolated_pg_url: str, redis_url: str) -> Any:
    import redis.asyncio as aioredis
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.providers.tenant_vault import TENANT_CIPHER_PREFIX
    from app.providers.vault import CredentialVault

    global T1, T2  # fresh tenants (and rotation state) per test
    T1, T2 = "rot-t1-" + uuid.uuid4().hex[:6], "rot-t2-" + uuid.uuid4().hex[:6]
    old = CredentialVault(master_key=OLD_KEY)
    tenant_key = bytes(range(32))
    tenant_vault = CredentialVault.from_byok(tenant_key)
    engine = create_async_engine(isolated_pg_url)
    async with engine.begin() as conn:
        await conn.execute(text("SET session_replication_role = replica"))  # skip FK seeding
        for t in (T1, T2):
            await _insert(conn, "tenants", {"id": t, "name": t, "email": f"{t}@x.io"})
        await _insert(
            conn, "tenant_vault_keys",
            {"tenant_id": T1, "wrapped_key": old.encrypt(base64.b64encode(tenant_key).decode()),
             "fingerprint": "fp"},
        )
        await _insert(
            conn, "tenant_llm_configs",
            {"tenant_id": T1, "provider": "openai",
             "encrypted_key": TENANT_CIPHER_PREFIX + tenant_vault.encrypt("sk-t1")},
        )
        await _insert(
            conn, "tenant_llm_configs",
            {"tenant_id": T2, "provider": "openai", "encrypted_key": old.encrypt("sk-t2")},
        )
        for i, t in enumerate((T1, T2)):
            await _insert(
                conn, "oauth_tokens",
                {"id": f"ot{i}-{t}", "server_id": f"srv-{t}", "tenant_id": t,
                 "access_token": old.encrypt(f"access-{t}"),
                 "refresh_token": old.encrypt(f"refresh-{t}")},
            )
            await _insert(
                conn, "schedules",
                {"id": f"sch{i}-{t}", "tenant_id": t, "goal_id_template": "g",
                 "trigger_type": "webhook",
                 "webhook_signature_secret_enc": old.encrypt(f"whsec-{t}"),
                 "webhook_signature_secret_prev_enc": old.encrypt(f"whprev-{t}")},
            )
            await _insert(
                conn, "memory_records",
                {"id": f"mem{i}-{t}", "tenant_id": t, "embedding_dimension": 2048,
                 "classification": "confidential", "idempotency_key": f"idem-{i}-{t}",
                 "sealed_content": "enc:v1:" + old.encrypt(json.dumps({"content": t}))},
            )
            await _insert(
                conn, "source_configs",
                {"id": f"src{i}-{t}", "tenant_id": t,
                 "connection_config": json.dumps(
                     {"base_url": "https://jira", "api_token": "enc:v1:"
                      + old.encrypt(json.dumps(f"tok-{t}")),
                      "nested": {"password": "enc:v1:" + old.encrypt(json.dumps("pw"))}}
                 )},
            )
            await _insert(
                conn, "agent_credentials",
                {"id": f"ac{i}-{t}", "tenant_id": t,
                 "private_key_ref": "vault:v1:" + old.encrypt(f"pem-{t}")},
            )
            await _insert(
                conn, "auction_registry",
                {"id": f"au{i}-{t}", "tenant_id": t, "announcement": "{}", "state": "open",
                 "sealed_keys": old.encrypt(f"keys-{t}")},
            )
        # Messaging-gateway bindings (B2-GAP-2): T2 sealed with the platform vault,
        # T1 with its tenant envelope key (left alone by vault-rotate).
        await _insert(
            conn, "channel_tenant_mappings",
            {"id": f"ctm-{T2}", "tenant_id": T2, "channel_type": "telegram",
             "channel_id": f"bot-{T2}",
             "channel_config": json.dumps(
                 {"secret_enc": old.encrypt(f"bind-secret-{T2}"),
                  "outbound_token_enc": old.encrypt(f"bind-out-{T2}"),
                  "verify_token_enc": "", "org_id": "o"}
             )},
        )
        await _insert(
            conn, "channel_tenant_mappings",
            {"id": f"ctm-{T1}", "tenant_id": T1, "channel_type": "telegram",
             "channel_id": f"bot-{T1}",
             "channel_config": json.dumps(
                 {"secret_enc": TENANT_CIPHER_PREFIX + tenant_vault.encrypt("bind-t1")}
             )},
        )
    redis = aioredis.from_url(redis_url, decode_responses=True)
    await redis.set(f"mcp:connector_secrets:{T1}:srv:token", old.encrypt("conn-secret"))
    await redis.set(
        f"mcp:servers:{T1}:srv",
        json.dumps({"auth_config": {"_encrypted_access_token": old.encrypt("copy-access"),
                                    "_encrypted_refresh_token": old.encrypt("copy-refresh")}}),
    )
    await redis.set(f"llm_config:{T2}", json.dumps({"encrypted_key": old.encrypt("sk-t2")}))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield {"factory": factory, "redis": redis, "engine": engine, "tenant_vault": tenant_vault}
    # Remove everything seeded for T1/T2: the session database is shared, and a
    # later rotation (e.g. the connector-registry rotation test) walks every
    # tenant — values left under this test's keys made it report "failed".
    async with engine.begin() as conn:
        await conn.execute(text("SET session_replication_role = replica"))
        tables = (
            await conn.execute(
                text(
                    "SELECT c.table_name FROM information_schema.columns c "
                    "JOIN information_schema.tables t USING (table_schema, table_name) "
                    "WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' "
                    "AND t.table_type = 'BASE TABLE'"
                )
            )
        ).scalars()
        for table in tables:
            await conn.execute(
                # tenant_id is TEXT in most tables, UUID in a few: compare as text.
                text(f'DELETE FROM "{table}" WHERE tenant_id::text IN (:a, :b)'),
                {"a": T1, "b": T2},
            )
        await conn.execute(text("DELETE FROM tenants WHERE id IN (:a, :b)"), {"a": T1, "b": T2})
    for pattern in (f"*{T1}*", f"*{T2}*"):
        async for key in redis.scan_iter(match=pattern):
            await redis.delete(key)
    await redis.aclose()
    await engine.dispose()


async def _all_ciphertexts(env: Any) -> list[tuple[str, str]]:
    """(label, platform ciphertext) for every seeded value, prefixes stripped."""
    from sqlalchemy import text

    out: list[tuple[str, str]] = []
    async with env["engine"].connect() as conn:
        q = lambda sql: conn.execute(text(sql), {"a": T1, "b": T2})  # noqa: E731
        for (w,) in (await q("SELECT wrapped_key FROM tenant_vault_keys WHERE tenant_id IN (:a,:b)")):
            out.append(("tenant_vault_keys", w))
        for (k,) in (await q("SELECT encrypted_key FROM tenant_llm_configs WHERE tenant_id = :b")):
            out.append(("tenant_llm_configs", k))
        for a, r in (await q("SELECT access_token, refresh_token FROM oauth_tokens WHERE tenant_id IN (:a,:b)")):
            out += [("oauth.access", a), ("oauth.refresh", r)]
        for s1, s2 in (await q(
            "SELECT webhook_signature_secret_enc, webhook_signature_secret_prev_enc "
            "FROM schedules WHERE tenant_id IN (:a,:b)"
        )):
            out += [("trigger", s1), ("trigger.prev", s2)]
        for (m,) in (await q("SELECT sealed_content FROM memory_records WHERE tenant_id IN (:a,:b)")):
            out.append(("memory", m[len("enc:v1:"):]))
        for (c,) in (await q("SELECT connection_config FROM source_configs WHERE tenant_id IN (:a,:b)")):
            cfg = c if isinstance(c, dict) else json.loads(c)
            out.append(("source", cfg["api_token"][len("enc:v1:"):]))
            out.append(("source.nested", cfg["nested"]["password"][len("enc:v1:"):]))
        for (p,) in (await q("SELECT private_key_ref FROM agent_credentials WHERE tenant_id IN (:a,:b)")):
            out.append(("agent_key", p[len("vault:v1:"):]))
        for (k,) in (await q("SELECT sealed_keys FROM auction_registry WHERE tenant_id IN (:a,:b)")):
            out.append(("auction", k))
        for (c,) in (await q("SELECT channel_config FROM channel_tenant_mappings WHERE tenant_id = :b")):
            cfg = c if isinstance(c, dict) else json.loads(c)
            out += [("binding.secret", cfg["secret_enc"]),
                    ("binding.outbound", cfg["outbound_token_enc"])]
    r = env["redis"]
    out.append(("redis.connector", await r.get(f"mcp:connector_secrets:{T1}:srv:token")))
    servers = json.loads(await r.get(f"mcp:servers:{T1}:srv"))["auth_config"]
    out += [("redis.copy.access", servers["_encrypted_access_token"]),
            ("redis.copy.refresh", servers["_encrypted_refresh_token"])]
    out.append(("redis.llm_cache", json.loads(await r.get(f"llm_config:{T2}"))["encrypted_key"]))
    return out


async def test_rotation_moves_every_store_to_the_new_key(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers import vault_rotation
    from app.providers.vault import CredentialVault, rotate_master_key

    old_rw = CredentialVault(master_key=OLD_KEY)
    new_only = CredentialVault(master_key=NEW_KEY)
    before = await _all_ciphertexts(env)

    # 1. Dry run: counts, writes nothing.
    dry = await rotate_master_key(
        old=old_rw, new=new_only, redis=env["redis"], system_db=env["factory"],
        dry_run=True, batch_size=1,
    )
    assert dry["status"] == "dry_run" and dry["would_rotate"] >= len(before) - 1
    assert await _all_ciphertexts(env) == before

    # 2. Interrupted run (fails mid-way), then resumed from the checkpoint.
    real_batch = vault_rotation._rotate_pg_batch
    calls = {"n": 0}

    async def _flaky(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 7:
            raise ConnectionError("worker killed")
        return await real_batch(*a, **k)

    monkeypatch.setattr(vault_rotation, "_rotate_pg_batch", _flaky)
    first = await rotate_master_key(
        old=old_rw, new=new_only, redis=env["redis"], system_db=env["factory"], batch_size=1
    )
    assert first["status"] == "failed" and "worker killed" in " ".join(first["errors"])
    monkeypatch.setattr(vault_rotation, "_rotate_pg_batch", real_batch)
    progress: list[dict[str, Any]] = []
    done = await rotate_master_key(
        old=old_rw, new=new_only, redis=env["redis"], system_db=env["factory"],
        batch_size=1, progress=progress.append,
    )
    assert done["status"] == "complete", done
    assert done["resumed"] is True and done["previous_keys_retirable"] is True
    assert progress and {"store", "tenant", "report"} <= set(progress[0])

    # 3. Every store now opens with ONLY the new key, and no longer with the old one.
    old_only = CredentialVault(master_key=OLD_KEY)
    for label, ct in await _all_ciphertexts(env):
        assert new_only.decrypt(ct), label
        with pytest.raises(Exception):
            old_only.decrypt(ct)

    # 4. Tenant-envelope values are untouched; their wrapping key was re-wrapped.
    from sqlalchemy import text

    async with env["engine"].connect() as conn:
        (llm_t1,) = (await conn.execute(
            text("SELECT encrypted_key FROM tenant_llm_configs WHERE tenant_id = :t"), {"t": T1}
        )).fetchone()
    assert llm_t1.startswith("tv1:")
    assert env["tenant_vault"].decrypt(llm_t1[len("tv1:"):]) == "sk-t1"
    async with env["engine"].connect() as conn:
        (bind_t1,) = (await conn.execute(
            text("SELECT channel_config FROM channel_tenant_mappings WHERE tenant_id = :t"),
            {"t": T1},
        )).fetchone()
    bind_t1 = bind_t1 if isinstance(bind_t1, dict) else json.loads(bind_t1)
    assert env["tenant_vault"].decrypt(bind_t1["secret_enc"][len("tv1:"):]) == "bind-t1"
    assert done["stores"]["channel_binding_secrets"]["rotated"] >= 2
    assert done["stores"]["channel_binding_secrets"]["tenant_key"] >= 1

    # 5. Idempotent: a re-run changes nothing and stays complete.
    snapshot = await _all_ciphertexts(env)
    again = await rotate_master_key(
        old=old_rw, new=new_only, redis=env["redis"], system_db=env["factory"], batch_size=1
    )
    assert again["status"] == "complete"
    assert await _all_ciphertexts(env) == snapshot


async def test_unreadable_value_fails_honestly_and_keeps_previous_keys(env: Any) -> None:
    from sqlalchemy import text

    from app.providers.vault import CredentialVault, rotate_master_key

    stranger = CredentialVault(master_key="some-third-key-nobody-configured-000000")
    async with env["engine"].begin() as conn:
        await conn.execute(
            text("UPDATE schedules SET webhook_signature_secret_enc = :v WHERE tenant_id = :t"),
            {"v": stranger.encrypt("lost"), "t": T2},
        )
    result = await rotate_master_key(
        old=CredentialVault(master_key=OLD_KEY),
        new=CredentialVault(master_key="yet-another-new-key-for-failure-test-0000"),
        redis=env["redis"],
        system_db=env["factory"],
    )
    assert result["status"] == "failed"
    assert result["previous_keys_retirable"] is False
    assert result["stores"]["trigger_secrets"]["failed"] >= 1  # (shared DB: other tests rows too)
