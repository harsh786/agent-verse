"""MFAStore reads/writes ``tenant_mfa`` under the tenant's RLS GUC.

``tenant_mfa`` is FORCE-RLS and the API runs as a NOBYPASSRLS role. Without
``app.tenant_id`` set, the SELECT sees no row, so ``MFAStore.get`` falls back to
the default state — ``enabled: False`` — and the MFA enforcement middleware lets
the request through without an ``X-MFA-Token``. The save path's INSERT/UPDATE
would violate the policy (and the error is swallowed as "non-fatal"), so an
enrollment never persisted across replicas/restarts.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from app.api.mfa import MFAStore


class _Tx:
    def __init__(self, s: _Session) -> None:
        self._s = s

    async def __aenter__(self) -> _Tx:
        self._s.in_tx = True
        return self

    async def __aexit__(self, et: Any, exc: Any, tb: Any) -> bool:
        self._s.in_tx = False
        self._s.tx_exits.append(et)
        return False


class _Session:
    """Tracks the live ``app.tenant_id`` value the way Postgres would see it."""

    def __init__(self, row: Any) -> None:
        self._row = row
        self.guc: str | None = None
        self.in_tx = False
        self.tx_exits: list[Any] = []
        self.statements: list[tuple[str, str | None, bool]] = []
        self.pending: list[Any] = []
        self.flushed_under: list[str | None] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Tx:
        return _Tx(self)

    def add(self, obj: Any) -> None:
        self.pending.append(obj)

    async def flush(self) -> None:
        if self.pending:
            self.flushed_under.append(self.guc)
            self.pending.clear()

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> MagicMock:
        sql = str(stmt)
        if "set_config('app.tenant_id'" in sql:
            self.guc = (params or {}).get("tid", "")
        self.statements.append((sql, self.guc, self.in_tx))
        result = MagicMock()
        # Emulate RLS: the row is only visible under the matching tenant GUC.
        visible = self._row if (self._row is not None and self.guc == self._row.tenant_id) else None
        result.scalar_one_or_none.return_value = visible
        return result


def _store(session: _Session) -> MFAStore:
    store = MFAStore()
    store.set_db(lambda: session)
    return store


def _tenant_mfa_statements(session: _Session) -> list[tuple[str, str | None, bool]]:
    return [s for s in session.statements if "tenant_mfa" in s[0]]


async def test_get_reads_enabled_state_under_tenant_guc() -> None:
    row = SimpleNamespace(
        tenant_id="t-mfa", enabled=True, encrypted_secret=None, recovery_codes_hashed="h1\nh2\n"
    )
    session = _Session(row)

    state = await _store(session).get("t-mfa")

    assert state["enabled"] is True, "MFA read as disabled -> enforcement would be skipped"
    assert state["recovery_codes_hashed"] == ["h1", "h2"]
    (select,) = _tenant_mfa_statements(session)
    assert select[1] == "t-mfa" and select[2], "tenant_mfa read outside tenant GUC / transaction"


async def test_get_cannot_see_another_tenants_row() -> None:
    row = SimpleNamespace(
        tenant_id="t-owner", enabled=True, encrypted_secret=None, recovery_codes_hashed=None
    )
    session = _Session(row)

    state = await _store(session).get("t-other")

    assert state["enabled"] is False
    (select,) = _tenant_mfa_statements(session)
    assert select[1] == "t-other"


async def test_save_inserts_under_tenant_guc_and_flushes_before_reset() -> None:
    session = _Session(row=None)  # no existing row -> ORM add
    state = {
        "enabled": True,
        "secret": None,
        "pending_secret": None,
        "recovery_codes_hashed": ["abc"],
    }

    await _store(session).save("t-new", state)

    (select,) = _tenant_mfa_statements(session)
    assert select[1] == "t-new" and select[2]
    # The ORM INSERT must reach the DB while app.tenant_id is still the tenant —
    # i.e. flushed inside sqlalchemy_rls_context, not at commit after the reset.
    assert session.flushed_under == ["t-new"]
    assert session.tx_exits == [None]


async def test_save_updates_existing_row_under_tenant_guc() -> None:
    row = SimpleNamespace(
        tenant_id="t-upd",
        enabled=False,
        encrypted_secret=None,
        recovery_codes_hashed="",
        enrolled_at=None,
        updated_at=None,
    )
    session = _Session(row)
    state = {"enabled": True, "secret": None, "pending_secret": None, "recovery_codes_hashed": []}

    await _store(session).save("t-upd", state)

    assert row.enabled is True  # the visible row was the one mutated
    assert row.enrolled_at is not None
    (select,) = _tenant_mfa_statements(session)
    assert select[1] == "t-upd" and select[2]
