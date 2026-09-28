"""Ingestion Source credentials: encrypted at rest, never returned by the API.

``connection_config`` was written to ``source_configs`` verbatim and echoed back
on every ``GET/POST/PATCH /sources`` response — Jira tokens, S3 secret keys,
Postgres DSNs with inline passwords. These tests pin both halves of the fix and
the legacy-row read-through re-encryption.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.ingestion as ingestion_mod
from app.api.ingestion import router
from app.ingestion.source_config import SourceConfig, SourceFamily
from app.ingestion.source_secrets import (
    ENC_PREFIX,
    MASK,
    decrypt_connection_config,
    encrypt_connection_config,
    is_secret_key,
    mask_connection_config,
    merge_masked_update,
)
from app.ingestion.source_store import SourceConfigStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_SECRET = "jira-api-token-DO-NOT-LEAK"
_CTX = TenantContext(tenant_id="tid-sec", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "av_professional_secrets"


@pytest.fixture(autouse=True)
def _vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-master-key-for-source-secrets")
    ingestion_mod._SOURCES.clear()


def _cfg(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-1",
        tenant_id="tid-sec",
        name="jira",
        family=SourceFamily.SUPPORT,
        source_type="jira",
        connection_config=cc
        or {"base_url": "https://acme.atlassian.net", "username": "u", "api_token": _SECRET},
    )


# ── policy helpers ────────────────────────────────────────────────────────────


def test_secret_key_classification() -> None:
    for key in (
        "api_token", "password", "secret_access_key", "client_secret", "dsn", "uri",
        "connection_string", "service_account_json", "account_key", "bot_token",
        "sasl_password", "credentials", "headers", "auth", "api_key", "private_key",
    ):
        assert is_secret_key(key), key
    for key in (
        "base_url", "bucket", "username", "access_key_id", "client_id", "region",
        "max_tokens", "tables", "host", "port", "url",
    ):
        assert not is_secret_key(key), key


def test_encrypt_roundtrip_including_non_string_secrets() -> None:
    cc = {"api_token": _SECRET, "headers": {"Authorization": "Bearer x"}, "base_url": "u"}
    enc = encrypt_connection_config(cc)
    assert enc["base_url"] == "u"
    assert enc["api_token"].startswith(ENC_PREFIX)
    assert enc["headers"].startswith(ENC_PREFIX)
    assert _SECRET not in json.dumps(enc)
    assert encrypt_connection_config(enc) == enc  # idempotent
    plain, legacy = decrypt_connection_config(enc)
    assert plain == cc
    assert legacy is False


def test_legacy_plaintext_is_tolerated_and_flagged() -> None:
    plain, legacy = decrypt_connection_config({"api_token": _SECRET, "base_url": "u"})
    assert plain["api_token"] == _SECRET
    assert legacy is True


def test_undecryptable_secret_fails_closed_to_empty() -> None:
    plain, _ = decrypt_connection_config({"api_token": ENC_PREFIX + "garbage"})
    assert plain["api_token"] == ""


def test_mask_and_merge() -> None:
    masked, has = mask_connection_config({"api_token": _SECRET, "base_url": "u", "password": ""})
    assert masked == {"api_token": MASK, "base_url": "u", "password": ""}
    assert has is True
    merged = merge_masked_update(
        {"api_token": _SECRET, "base_url": "u"}, {"api_token": MASK, "base_url": "v"}
    )
    assert merged == {"api_token": _SECRET, "base_url": "v"}


# ── API never returns credentials ─────────────────────────────────────────────


def _client(**state: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    for k, v in state.items():
        setattr(app.state, k, v)
    return TestClient(app, raise_server_exceptions=False)


def test_api_masks_credentials_on_create_get_list_and_patch() -> None:
    store = SourceConfigStore()  # in-memory
    client = _client(ingestion_source_store=store)
    h = {"X-API-Key": _KEY}
    body = {
        "name": "jira",
        "family": "support",
        "source_type": "jira",
        "connection_config": {
            "base_url": "https://acme.atlassian.net",
            "username": "u",
            "api_token": _SECRET,
        },
    }
    created = client.post("/sources", json=body, headers=h)
    assert created.status_code == 201, created.text
    assert _SECRET not in created.text
    data = created.json()
    assert data["connection_config"]["api_token"] == MASK
    assert data["connection_config"]["base_url"] == "https://acme.atlassian.net"
    assert data["has_credentials"] is True
    sid = data["source_id"]

    for resp in (client.get(f"/sources/{sid}", headers=h), client.get("/sources", headers=h)):
        assert resp.status_code == 200
        assert _SECRET not in resp.text

    # Round-tripping the masked config (UI edits base_url only) keeps the secret.
    cc = dict(data["connection_config"], base_url="https://other.atlassian.net")
    patched = client.patch(f"/sources/{sid}", json={"connection_config": cc}, headers=h)
    assert patched.status_code == 200
    assert _SECRET not in patched.text
    assert store._mem[sid].connection_config["api_token"] == _SECRET
    assert store._mem[sid].connection_config["base_url"] == "https://other.atlassian.net"


# ── DB path: encrypted at rest + legacy read-through re-encryption ───────────


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeSession:
    def __init__(self, table: dict[str, dict[str, Any]]) -> None:
        self.table = table
        self.executed: list[tuple[str, dict[str, Any]]] = []

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self

    @asynccontextmanager
    async def begin_nested(self) -> Any:
        yield self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        p = dict(params or {})
        self.executed.append((sql, p))
        if sql.startswith("INSERT INTO source_configs"):
            row = dict(p.items())
            row["connection_config"] = json.loads(p["connection_config"])
            self.table[p["id"]] = row
        elif "SET connection_config" in sql:
            self.table[p.get("id") or p["source_id"]]["connection_config"] = json.loads(
                p.get("cc") or p["connection_config"]
            )
        elif sql.startswith("SELECT * FROM source_configs"):
            row = self.table.get(p.get("id", ""))
            if "WHERE id" in sql:
                return _Result([dict(row)] if row else [])
            return _Result([dict(r) for r in self.table.values()])
        return _Result([])


def _factory(session: _FakeSession) -> Any:
    @asynccontextmanager
    async def _open() -> Any:
        yield session

    return _open


@pytest.mark.asyncio
async def test_db_store_never_persists_plaintext_secret() -> None:
    table: dict[str, dict[str, Any]] = {}
    session = _FakeSession(table)
    store = SourceConfigStore(db=_factory(session))
    await store.create(_cfg())

    stored = json.dumps(table["src-1"]["connection_config"])
    assert _SECRET not in stored
    assert table["src-1"]["connection_config"]["api_token"].startswith(ENC_PREFIX)

    got = await store.get("src-1", "tid-sec")
    assert got is not None
    assert got.connection_config["api_token"] == _SECRET  # connectors get plaintext

    await store.update("src-1", "tid-sec", connection_config={"api_token": "rotated"})
    assert "rotated" not in json.dumps(table["src-1"]["connection_config"])


@pytest.mark.asyncio
async def test_legacy_plaintext_row_is_reencrypted_on_read() -> None:
    table = {
        "src-1": {
            "id": "src-1",
            "tenant_id": "tid-sec",
            "name": "jira",
            "family": "support",
            "source_type": "jira",
            "connection_config": {"base_url": "https://acme.atlassian.net", "api_token": _SECRET},
        }
    }
    session = _FakeSession(table)
    store = SourceConfigStore(db=_factory(session))
    got = await store.get("src-1", "tid-sec")
    assert got is not None
    assert got.connection_config["api_token"] == _SECRET
    assert table["src-1"]["connection_config"]["api_token"].startswith(ENC_PREFIX)
    # The rewrite ran inside the tenant's RLS transaction, tenant-predicated.
    update_sql, update_params = next(
        (s, p) for s, p in session.executed if "SET connection_config" in s
    )
    assert "tenant_id = :tid" in update_sql
    assert update_params["tid"] == "tid-sec"
