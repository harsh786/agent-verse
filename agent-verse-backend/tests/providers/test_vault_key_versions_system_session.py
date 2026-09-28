"""``vault_key_versions`` is platform-global: rotation records via system_session.

The table versions the single master key that encrypts every tenant's connector
secrets (all rows carry ``tenant_id = 'global'``; retiring the current version
is deliberately unscoped). It has no tenant policy — it is FORCE-RLS with a
platform-only (deny) policy — so the write is cross-tenant system work and must
run as the maintenance role inside ``system_session``, in one transaction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.providers.vault import CredentialVault


class _Tx:
    def __init__(self, s: _Session) -> None:
        self._s = s

    async def __aenter__(self) -> _Tx:
        self._s.in_tx = True
        return self

    async def __aexit__(self, et: Any, exc: Any, tb: Any) -> bool:
        self._s.in_tx = False
        return False


class _Session:
    def __init__(self, fail_on: str | None = None) -> None:
        self.in_tx = False
        self.statements: list[tuple[str, bool]] = []
        self._fail_on = fail_on

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Tx:
        return _Tx(self)

    async def execute(self, stmt: Any, params: Any = None) -> MagicMock:
        sql = str(stmt)
        self.statements.append((sql, self.in_tx))
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError("query would be affected by row-level security policy")
        return MagicMock()


async def test_rotation_records_key_version_inside_system_session() -> None:
    vault = CredentialVault(master_key="a" * 32)
    session = _Session()

    result = await vault.rotate_key(b"n" * 32, db=lambda: session)

    assert result["status"] == "rotation_complete"
    sqls = [s for s, _ in session.statements]
    assert all(in_tx for _, in_tx in session.statements), "must run in one transaction"
    assert sqls[0].strip() == "SET LOCAL row_security = off"
    assert "UPDATE vault_key_versions" in sqls[1]
    assert "INSERT INTO vault_key_versions" in sqls[2]
    assert not any("app.tenant_id" in s for s in sqls), "platform table: no tenant GUC"


async def test_rotation_under_api_role_fails_loudly_but_rotation_still_completes() -> None:
    """Passed the NOBYPASSRLS API factory by mistake, the record fails (and is
    logged) instead of silently writing — the in-process key still rotates."""
    vault = CredentialVault(master_key="a" * 32)
    session = _Session(fail_on="UPDATE vault_key_versions")

    result = await vault.rotate_key(b"m" * 32, db=lambda: session)

    assert result["status"] == "rotation_complete"
    assert not any("INSERT INTO vault_key_versions" in s for s, _ in session.statements)
    assert vault.encrypt("x") and vault.decrypt(vault.encrypt("x")) == "x"
