"""SAML / SCIM request paths run their SQL under the tenant's RLS GUC.

``saml_configs``, ``scim_configs`` and ``scim_tokens`` are FORCE-RLS tables. The
API connects as a NOBYPASSRLS role, so a statement that runs without
``app.tenant_id`` set sees no row (reads come back empty — "SAML not configured",
SCIM restrictions silently replaced by permissive defaults) and every write
violates the policy. Each path must therefore:

* open an explicit transaction (the GUC is ``SET LOCAL``),
* set ``app.tenant_id`` to the *caller's* tenant before touching the table, and
* keep the explicit ``tenant_id`` predicate (defence in depth).

The SCIM bearer lookup is the one pre-auth path: it presents the token hash via
``app.scim_token_hash`` and must never set a tenant GUC or disable row security.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api import enterprise as ent
from app.tenancy.context import PlanTier, TenantContext

TENANT = "tenant-identity-a"


class _Tx:
    def __init__(self, session: _FakeSession) -> None:
        self._s = session

    async def __aenter__(self) -> _Tx:
        self._s.in_tx = True
        return self

    async def __aexit__(self, et: Any, exc: Any, tb: Any) -> bool:
        self._s.in_tx = False
        self._s.tx_exits.append(et)
        return False


class _FakeSession:
    """Records (sql, params, in_transaction) for every statement."""

    def __init__(self, rows: dict[str, Any]) -> None:
        self._rows = rows
        self.statements: list[tuple[str, dict[str, Any], bool]] = []
        self.in_tx = False
        self.tx_exits: list[Any] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    def begin(self) -> _Tx:
        return _Tx(self)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> MagicMock:
        sql = str(stmt)
        self.statements.append((sql, dict(params or {}), self.in_tx))
        result = MagicMock()
        result.fetchone.return_value = next(
            (row for key, row in self._rows.items() if key in sql and "set_config" not in sql),
            None,
        )
        return result


class _Factory:
    def __init__(self, rows: dict[str, Any] | None = None) -> None:
        self._rows = rows or {}
        self.sessions: list[_FakeSession] = []

    def __call__(self) -> _FakeSession:
        s = _FakeSession(self._rows)
        self.sessions.append(s)
        return s


def _request(factory: _Factory, *, tenant: str | None = TENANT, headers: dict | None = None):
    request = MagicMock()
    request.state.tenant = (
        TenantContext(tenant_id=tenant, plan=PlanTier.ENTERPRISE, api_key_id="k", roles=("admin",))
        if tenant
        else None
    )
    request.app.state.db_session_factory = factory
    request.app.state.redis = None
    request.base_url = "http://testserver/"
    request.headers = headers or {}
    return request


def _assert_tenant_scoped(session: _FakeSession, table: str, tenant: str) -> None:
    """Every statement ran in a transaction; the tenant GUC precedes *table*."""
    assert session.statements, "no SQL executed"
    assert all(in_tx for _, _, in_tx in session.statements), "SQL ran outside a transaction"
    first_table = next(i for i, (sql, _, _) in enumerate(session.statements) if table in sql)
    guc = [
        i
        for i, (sql, params, _) in enumerate(session.statements[:first_table])
        if "set_config('app.tenant_id'" in sql and params.get("tid") == tenant
    ]
    assert guc, f"app.tenant_id={tenant!r} not set before the first {table} statement"
    # Defence in depth: the table statement keeps its own tenant predicate.
    assert session.statements[first_table][1].get("tid") == tenant
    joined = " ".join(sql for sql, _, _ in session.statements).lower()
    assert "row_security" not in joined, "request path must never disable row security"


_SAML_ROW = ("idp-entity", "https://idp.example/sso", "CERT", "sp-entity", {}, "email")


async def test_saml_configure_writes_under_tenant_guc() -> None:
    factory = _Factory()
    body = ent.SAMLConfigRequest(
        idp_entity_id="idp", idp_sso_url="https://idp/sso", idp_cert="c", sp_entity_id="sp"
    )
    out = await ent.configure_saml(_request(factory), body)
    assert out == {"status": "configured", "tenant_id": TENANT}
    (session,) = factory.sessions
    _assert_tenant_scoped(session, "INSERT INTO saml_configs", TENANT)
    assert session.tx_exits == [None]  # committed cleanly


async def test_saml_login_reads_under_tenant_guc() -> None:
    factory = _Factory({"FROM saml_configs": _SAML_ROW[:4]})
    try:
        resp = await ent.saml_login(_request(factory))
        assert resp.status_code in (302, 307)
    except HTTPException as exc:
        assert exc.status_code == 501  # python3-saml (optional extra) absent
    _assert_tenant_scoped(factory.sessions[0], "FROM saml_configs", TENANT)


async def test_saml_metadata_reads_under_tenant_guc() -> None:
    factory = _Factory({"FROM saml_configs": _SAML_ROW})
    # python3-saml may be absent — the endpoint may then 500 building metadata,
    # but only AFTER the scoped read, which is what this test pins.
    try:
        await ent.get_saml_metadata(_request(factory))
    except HTTPException as exc:
        assert exc.status_code == 500
    _assert_tenant_scoped(factory.sessions[0], "FROM saml_configs", TENANT)


async def test_saml_metadata_404_when_tenant_has_no_config() -> None:
    factory = _Factory()  # the scoped read finds nothing
    with pytest.raises(HTTPException) as exc:
        await ent.get_saml_metadata(_request(factory))
    assert exc.value.status_code == 404


async def test_saml_acs_reads_under_tenant_guc() -> None:
    factory = _Factory({"FROM saml_configs": _SAML_ROW[:5]})
    request = _request(factory)
    request.form = AsyncMock(return_value={"SAMLResponse": "PHNhbWw+"})
    try:
        await ent.saml_acs(request)
    except HTTPException:
        pass  # assertion validation itself is not under test here
    _assert_tenant_scoped(factory.sessions[0], "FROM saml_configs", TENANT)


async def test_provision_scim_token_inserts_under_tenant_guc() -> None:
    factory = _Factory()
    out = await ent.provision_scim_token(_request(factory))
    assert out["token"]
    (session,) = factory.sessions
    _assert_tenant_scoped(session, "INSERT INTO scim_tokens", TENANT)
    insert_params = next(p for sql, p, _ in session.statements if "INSERT INTO scim_tokens" in sql)
    assert insert_params["hash"] == hashlib.sha256(out["token"].encode()).hexdigest()


async def test_scim_handler_loads_config_under_the_token_tenants_guc() -> None:
    """Bearer lookup (pre-auth, hash GUC) → scim_configs read under the RESOLVED
    tenant's GUC → handler bound to that tenant with its real restrictions."""
    raw = "scim-raw-token"
    factory = _Factory(
        {
            "FROM scim_tokens": ("tenant-from-token",),
            "FROM scim_configs": (False, True, False, True, "viewer", {"Ops": "operator"}),
        }
    )
    # No API-key tenant on the request: /scim/v2 bypasses TenantMiddleware.
    request = _request(factory, tenant=None, headers={"Authorization": f"Bearer {raw}"})

    handler = await ent._get_scim_handler(request)

    auth_session, cfg_session = factory.sessions
    # 1. pre-auth lookup: hash GUC, no tenant GUC, no row_security games
    sql0, params0, in_tx0 = auth_session.statements[0]
    assert in_tx0 and "set_config('app.scim_token_hash'" in sql0
    assert params0 == {"h": hashlib.sha256(raw.encode()).hexdigest()}
    assert not any("app.tenant_id" in s for s, _, _ in auth_session.statements)
    # 2. config read scoped to the tenant the token resolved to
    _assert_tenant_scoped(cfg_session, "FROM scim_configs", "tenant-from-token")
    # 3. the tenant's restriction is honoured (not the permissive default)
    assert handler._tenant_id == "tenant-from-token"
    assert handler._config["allow_user_create"] is False
    assert handler._config["group_role_map"] == {"Ops": "operator"}
