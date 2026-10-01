"""PROV-15: a tenant's own vault key is stored (wrapped) and used for its new secrets.

``POST /tenants/me/vault-key`` validated the key and returned
``validated_not_persisted``: tenants could not bring their own vault key.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.providers.tenant_vault import (
    TENANT_CIPHER_PREFIX,
    TenantVaultError,
    decrypt_tenant_secret,
    encrypt_tenant_secret,
)
from app.providers.vault import get_vault

TENANT = "t-vault"
KEY_A = bytes(range(32))
KEY_B = bytes(range(1, 33))


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Db:
    """tenant_vault_keys + tenant_llm_configs rows behind fake async sessions."""

    def __init__(self) -> None:
        self.vault_keys: dict[str, str] = {}
        self.llm_keys: dict[str, str] = {}

    def __call__(self) -> _Session:
        return _Session(self)


class _Tx:
    async def __aenter__(self) -> _Tx:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False


class _Session:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Tx:
        return _Tx()

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        p = params or {}
        if sql.startswith("SELECT wrapped_key FROM tenant_vault_keys"):
            w = self.db.vault_keys.get(p["t"])
            return _Result((w,) if w else None)
        if sql.startswith("INSERT INTO tenant_vault_keys"):
            self.db.vault_keys[p["t"]] = p["w"]
        if sql.startswith("SELECT encrypted_key FROM tenant_llm_configs"):
            k = self.db.llm_keys.get(p["t"])
            return _Result((k,) if k else None)
        if sql.startswith("UPDATE tenant_llm_configs"):
            self.db.llm_keys[p["t"]] = p["k"]
        return _Result(None)


def _request(db: Any) -> Any:
    request = MagicMock()
    request.app.state = SimpleNamespace(db_session_factory=db)
    return request


def _ctx() -> Any:
    return SimpleNamespace(tenant_id=TENANT)


async def test_vault_key_is_persisted_wrapped() -> None:
    from app.api.tenants import VaultKeyRequest, set_byok_vault_key

    db = _Db()
    out = await set_byok_vault_key(
        _request(db), VaultKeyRequest(key_base64=base64.b64encode(KEY_A).decode()), _ctx()
    )
    assert out["persisted"] is True and out["status"] == "stored"
    wrapped = db.vault_keys[TENANT]
    assert base64.b64encode(KEY_A).decode() not in wrapped  # never stored in plaintext
    assert base64.b64decode(get_vault().decrypt(wrapped)) == KEY_A  # wrapped by the KEK


async def test_new_secrets_use_the_tenant_key() -> None:
    from app.providers.tenant_vault import store_tenant_vault_key

    db = _Db()
    await store_tenant_vault_key(db, TENANT, KEY_A)
    ct = await encrypt_tenant_secret(db, TENANT, "sk-tenant")
    assert ct.startswith(TENANT_CIPHER_PREFIX)
    assert await decrypt_tenant_secret(db, TENANT, ct) == "sk-tenant"
    with pytest.raises(Exception):
        get_vault().decrypt(ct[len(TENANT_CIPHER_PREFIX) :])


async def test_tenants_without_a_key_keep_the_platform_vault() -> None:
    db = _Db()
    ct = await encrypt_tenant_secret(db, TENANT, "sk-x")
    assert not ct.startswith(TENANT_CIPHER_PREFIX)
    assert get_vault().decrypt(ct) == "sk-x"


async def test_replacing_the_key_reencrypts_the_tenant_llm_key() -> None:
    from app.providers.tenant_vault import store_tenant_vault_key

    db = _Db()
    await store_tenant_vault_key(db, TENANT, KEY_A)
    db.llm_keys[TENANT] = await encrypt_tenant_secret(db, TENANT, "sk-keep-me")
    await store_tenant_vault_key(db, TENANT, KEY_B)
    assert await decrypt_tenant_secret(db, TENANT, db.llm_keys[TENANT]) == "sk-keep-me"


async def test_missing_tenant_key_fails_closed() -> None:
    from app.providers.tenant_vault import store_tenant_vault_key

    db = _Db()
    await store_tenant_vault_key(db, TENANT, KEY_A)
    ct = await encrypt_tenant_secret(db, TENANT, "sk")
    db.vault_keys.clear()
    with pytest.raises(TenantVaultError):
        await decrypt_tenant_secret(db, TENANT, ct)


async def test_no_database_means_503_not_a_fake_success() -> None:
    from app.api.tenants import VaultKeyRequest, set_byok_vault_key

    with pytest.raises(HTTPException) as exc:
        await set_byok_vault_key(
            _request(None), VaultKeyRequest(key_base64=base64.b64encode(KEY_A).decode()), _ctx()
        )
    assert exc.value.status_code == 503


async def test_byok_provider_is_built_with_the_tenant_vault_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.tenant_provider as tp
    from app.providers.tenant_vault import store_tenant_vault_key

    db = _Db()
    await store_tenant_vault_key(db, TENANT, KEY_A)
    enc = await encrypt_tenant_secret(db, TENANT, "sk-ant-tenant-secret")
    seen: dict[str, Any] = {}

    def _construct(pname: str, api_key: str, *a: Any, **k: Any) -> Any:
        seen["api_key"] = api_key
        return SimpleNamespace()

    monkeypatch.setattr(tp, "_construct", _construct)

    class _Store:
        async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
            return {"provider": "anthropic", "encrypted_key": enc}

    state = SimpleNamespace(llm_config_store=_Store(), db_session_factory=db)
    await tp.resolve_tenant_byok_provider(state, TENANT)
    assert seen["api_key"] == "sk-ant-tenant-secret"
