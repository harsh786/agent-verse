"""PROV-13 (+TOOL-15): vault key rotation covers Redis AND Postgres, fails honestly.

``CredentialVault.rotate_key`` (no callers) re-encrypted only Redis connector
secrets — ``tenant_llm_configs.encrypted_key`` would have become undecryptable —
and returned ``rotation_complete`` after a swallowed scan failure. Rotation is
now an offline operation (``agentverse vault-rotate``) over every ciphertext
store it knows, with multi-key decrypt so replicas accept old and new keys while
it runs, and ``get_vault()`` caches the PBKDF2-derived vault per key set.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.providers import vault as vault_mod
from app.providers.vault import CredentialVault, rotate_master_key

OLD, NEW = "old-master-key-0123456789abcdef-x", "new-master-key-0123456789abcdef-x"


class _Pipe:
    def __init__(self, r: _Redis) -> None:
        self.r, self.ops = r, []

    def set(self, k: str, v: str) -> None:
        self.ops.append((k, v))

    async def execute(self) -> None:
        if self.r.fail_write:
            raise ConnectionError("redis down")
        for k, v in self.ops:
            self.r.data[k] = v


class _Redis:
    def __init__(self, data: dict[str, str], *, fail_scan: bool = False) -> None:
        self.data, self.fail_scan, self.fail_write = dict(data), fail_scan, False

    async def scan_iter(self, match: str, count: int = 100) -> Any:
        if self.fail_scan:
            raise ConnectionError("scan failed")
        for k in list(self.data):
            if k.startswith(match.rstrip("*")):
                yield k

    async def get(self, k: str) -> str | None:
        return self.data.get(k)

    def pipeline(self) -> _Pipe:
        return _Pipe(self)


class _Result:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str, str]]:
        return self._rows


class _Tx:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def __aenter__(self) -> _Tx:
        self.db.pending = dict(self.db.rows)
        return self

    async def __aexit__(self, et: Any, exc: Any, tb: Any) -> bool:
        if et is None:
            self.db.rows = self.db.pending  # commit
        return False


class _Db:
    """tenant_llm_configs rows + vault_key_versions statements, transactional."""

    def __init__(self, rows: dict[str, str], *, fail_update: bool = False) -> None:
        self.rows, self.pending = dict(rows), dict(rows)
        self.fail_update = fail_update
        self.statements: list[str] = []

    async def __aenter__(self) -> _Db:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Tx:
        return _Tx(self)

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.statements.append(sql)
        if sql.startswith("SELECT tenant_id, encrypted_key"):
            return _Result(list(self.pending.items()))
        if sql.startswith("UPDATE tenant_llm_configs"):
            if self.fail_update:
                raise RuntimeError("write failed")
            self.pending[params["t"]] = params["k"]
        return _Result([])


def _setup() -> tuple[CredentialVault, _Redis, _Db]:
    old = CredentialVault(master_key=OLD)
    redis = _Redis({"mcp:connector_secrets:t1:srv:token": old.encrypt("conn-secret")})
    db = _Db({"t1": old.encrypt("sk-tenant-1"), "t2": old.encrypt("sk-tenant-2")})
    return old, redis, db


async def test_rotation_reencrypts_redis_and_postgres() -> None:
    old, redis, db = _setup()
    new = CredentialVault(master_key=NEW)
    result = await rotate_master_key(old=old, new=new, redis=redis, system_db=lambda: db)
    assert result["status"] == "complete", result
    assert result["redis_connector_secrets"] == 1
    assert result["postgres_tenant_llm_keys"] == 2
    assert new.decrypt(redis.data["mcp:connector_secrets:t1:srv:token"]) == "conn-secret"
    assert new.decrypt(db.rows["t1"]) == "sk-tenant-1"
    assert any("INSERT INTO vault_key_versions" in s for s in db.statements)
    assert db.statements[0].strip() == "SET LOCAL row_security = off"


async def test_failing_scan_reports_failed_and_writes_nothing() -> None:
    old, _redis, db = _setup()
    redis = _Redis({}, fail_scan=True)
    before = dict(db.rows)
    result = await rotate_master_key(
        old=old, new=CredentialVault(master_key=NEW), redis=redis, system_db=lambda: db
    )
    assert result["status"] == "failed"
    assert db.rows == before  # nothing half-rotated


async def test_undecryptable_postgres_row_fails_the_rotation() -> None:
    old, redis, db = _setup()
    db.rows["t3"] = CredentialVault(master_key="some-other-key").encrypt("x")
    before_redis = dict(redis.data)
    result = await rotate_master_key(
        old=old, new=CredentialVault(master_key=NEW), redis=redis, system_db=lambda: db
    )
    assert result["status"] == "failed"
    assert redis.data == before_redis


async def test_postgres_write_failure_rolls_back_and_reports_failed() -> None:
    old, redis, _db = _setup()
    db = _Db({"t1": old.encrypt("a")}, fail_update=True)
    before = dict(db.rows)
    result = await rotate_master_key(
        old=old, new=CredentialVault(master_key=NEW), redis=redis, system_db=lambda: db
    )
    assert result["status"] == "failed" and db.rows == before


def test_previous_keys_decrypt_during_rotation() -> None:
    old = CredentialVault(master_key=OLD)
    ciphertext = old.encrypt("still readable")
    rotated = CredentialVault(master_key=NEW, previous_master_keys=(OLD,))
    assert rotated.decrypt(ciphertext) == "still readable"
    # new writes use the new key only
    assert CredentialVault(master_key=NEW).decrypt(rotated.encrypt("y")) == "y"


def test_get_vault_reads_previous_keys_and_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_MASTER_KEY", NEW)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY", raising=False)
    monkeypatch.setenv("VAULT_PREVIOUS_MASTER_KEYS", OLD)
    vault_mod._cached_vault.cache_clear()
    v1, v2 = vault_mod.get_vault(), vault_mod.get_vault()
    assert v1 is v2  # PBKDF2 (480k iterations) runs once per key set
    assert v1.decrypt(CredentialVault(master_key=OLD).encrypt("z")) == "z"


def test_in_place_rotate_key_is_gone() -> None:
    assert not hasattr(CredentialVault, "rotate_key")


def test_cli_vault_rotate_exits_nonzero_when_rotation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from app.cli.main import app

    async def _failed(**_kw: Any) -> dict[str, Any]:
        return {"status": "failed", "errors": ["redis: scan failed"]}

    monkeypatch.setattr(vault_mod, "rotate_master_key", _failed)
    monkeypatch.setenv("VAULT_NEW_MASTER_KEY", NEW)
    monkeypatch.delenv("REDIS_URL", raising=False)
    result = CliRunner().invoke(app, ["vault-rotate"])
    assert result.exit_code == 1
    assert '"failed"' in result.output


def test_cli_vault_rotate_refuses_a_short_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from app.cli.main import app

    monkeypatch.setenv("VAULT_NEW_MASTER_KEY", "short")
    assert CliRunner().invoke(app, ["vault-rotate"]).exit_code == 1
