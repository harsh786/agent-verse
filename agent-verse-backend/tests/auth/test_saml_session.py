"""SAML-01: a valid SAML assertion logs the person in (it used to end in 501).

The ACS validates the signed assertion (python3-saml strict mode, local fixture
IdP), JIT-provisions the person through the user-session store, records a
session and redirects (303) the browser to the frontend with a one-time login
code; ``POST /auth/session/exchange`` turns the code into an ``avs_`` token
that TenantMiddleware accepts. Postgres behaviour of the store is covered in
tests/integration/test_user_sessions_pg.py.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.auth.user_sessions import LoginRefusedError, SessionStoreUnavailableError
from app.tenancy.context import PlanTier, TenantContext
from tests.auth._saml_idp import IDP_ENTITY, IDP_SSO, make_idp, signed_response

TENANT = "t-saml"
SP = "https://sp.test/saml/metadata"
ACS = f"http://sp.test/enterprise/saml/acs/{TENANT}"
TOKEN = "avs_" + "x" * 43


class FakeSessionStore:
    def __init__(self) -> None:
        self.provisioned: list[dict[str, Any]] = []
        self.refuse: str | None = None
        self.unavailable = False
        self.revoked: list[str] = []

    async def provision_member(self, **kw: Any) -> str:
        if self.unavailable:
            raise SessionStoreUnavailableError("down")
        if self.refuse:
            raise LoginRefusedError(self.refuse)
        self.provisioned.append(kw)
        return "user-1"

    async def issue_login_code(self, *, tenant_id: str, user_id: str, auth_method: str) -> str:
        assert (tenant_id, user_id, auth_method) == (TENANT, "user-1", "saml")
        return "login-code-1234567890"

    async def exchange_code(self, code: str) -> dict[str, Any] | None:
        if code != "login-code-1234567890":
            return None
        return {
            "access_token": TOKEN,
            "token_type": "Bearer",
            "expires_in": 3600,
            "tenant_id": TENANT,
            "user_id": "user-1",
            "plan": "starter",
        }

    async def revoke_token(self, token: str) -> bool:
        if self.unavailable:
            raise SessionStoreUnavailableError("down")
        self.revoked.append(token)
        return True

    async def resolve(self, token: str) -> TenantContext | None:
        if self.unavailable:
            raise SessionStoreUnavailableError("down")
        if token != TOKEN or token in self.revoked:
            return None
        return TenantContext(
            tenant_id=TENANT,
            plan=PlanTier.FREE,
            api_key_id="user:user-1",
            roles=("viewer",),
            user_id="user-1",
        )


@pytest.fixture
def app_and_store(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FakeSessionStore]:
    from app.api import enterprise
    from app.main import create_app

    app = create_app()
    store = FakeSessionStore()
    app.state.user_session_store = store
    app.state._redis = fakeredis.aioredis.FakeRedis()
    app.state.db_session_factory = object()  # never opened: config loader is patched

    async def _cfg(_db: Any, tenant_id: str) -> dict[str, Any] | None:
        if tenant_id != TENANT:
            return None
        return {
            "idp_entity_id": IDP_ENTITY,
            "idp_sso_url": IDP_SSO,
            "idp_cert": make_idp().cert_b64,
            "sp_entity_id": SP,
            "attribute_mapping": {},
            "default_role": "operator",
            "jit_provisioning": True,
        }

    monkeypatch.setattr(enterprise, "_load_saml_config", _cfg)
    return app, store


def _post_acs(client: TestClient, resp: str, tenant: str = TENANT) -> Any:
    return client.post(
        f"/enterprise/saml/acs/{tenant}",
        data={"SAMLResponse": resp},
        follow_redirects=False,
    )


def test_valid_assertion_provisions_and_redirects_with_login_code(
    app_and_store: tuple[Any, FakeSessionStore],
) -> None:
    app, store = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)
    resp = signed_response(
        acs_url=ACS,
        sp_entity=SP,
        name_id="alice@corp.test",
        attributes={"email": ["alice@corp.test"], "firstName": ["Alice"], "lastName": ["Liddell"]},
    )
    r = _post_acs(client, resp)
    assert r.status_code == 303, r.text
    loc = urlparse(r.headers["location"])
    assert loc.path == "/auth/sso/complete"
    assert parse_qs(loc.query)["code"] == ["login-code-1234567890"]
    assert store.provisioned == [
        {
            "tenant_id": TENANT,
            "email": "alice@corp.test",
            "name": "Alice Liddell",
            "default_role": "operator",
            "jit": True,
        }
    ]

    # The code becomes a session token TenantMiddleware accepts.
    r = client.post("/auth/session/exchange", json={"code": "login-code-1234567890"})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] == TOKEN
    assert r.json()["plan"] == "starter"  # the tenant's real plan, never a guess
    me = client.get("/tenants/me", headers={"Authorization": f"Bearer {TOKEN}"})
    assert me.status_code != 401, me.text


def test_replayed_assertion_is_rejected(app_and_store: tuple[Any, FakeSessionStore]) -> None:
    app, _ = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)
    resp = signed_response(acs_url=ACS, sp_entity=SP, name_id="bob@corp.test")
    assert _post_acs(client, resp).status_code == 303
    assert _post_acs(client, resp).status_code == 401


def test_wrong_recipient_or_audience_is_401(app_and_store: tuple[Any, FakeSessionStore]) -> None:
    app, store = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)
    other_acs = "http://sp.test/enterprise/saml/acs/other-tenant"
    assert (
        _post_acs(client, signed_response(acs_url=other_acs, sp_entity=SP, name_id="c@x.t"))
    ).status_code == 401
    assert (
        _post_acs(client, signed_response(acs_url=ACS, sp_entity="https://evil", name_id="c@x.t"))
    ).status_code == 401
    assert store.provisioned == []


def test_refused_login_is_403_and_store_outage_is_503(
    app_and_store: tuple[Any, FakeSessionStore],
) -> None:
    app, store = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)
    store.refuse = "membership deactivated"
    r = _post_acs(client, signed_response(acs_url=ACS, sp_entity=SP, name_id="d@corp.test"))
    assert r.status_code == 403
    store.refuse = None
    store.unavailable = True
    r = _post_acs(client, signed_response(acs_url=ACS, sp_entity=SP, name_id="e@corp.test"))
    assert r.status_code == 503


def test_session_token_auth_fails_closed(app_and_store: tuple[Any, FakeSessionStore]) -> None:
    app, store = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)
    bad = {"Authorization": "Bearer avs_" + "y" * 43}
    assert client.get("/tenants/me", headers=bad).status_code == 401
    store.unavailable = True
    r = client.get("/tenants/me", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 503


def test_exchange_rejects_unknown_code(app_and_store: tuple[Any, FakeSessionStore]) -> None:
    app, _ = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)
    r = client.post("/auth/session/exchange", json={"code": "not-a-real-code-at-all"})
    assert r.status_code == 401


def test_sessions_need_a_database() -> None:
    from app.main import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    r = client.post("/auth/session/exchange", json={"code": "whatever-code-123456"})
    assert r.status_code == 503


def test_logout_revokes_the_session_token(app_and_store: tuple[Any, FakeSessionStore]) -> None:
    app, store = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    r = client.post("/auth/session/logout", headers=auth)
    assert r.status_code == 204, r.text
    assert store.revoked == [TOKEN]
    # The revoked token no longer authenticates anything — logout included.
    assert client.get("/tenants/me", headers=auth).status_code == 401
    assert client.post("/auth/session/logout", headers=auth).status_code == 401


def test_logout_needs_a_session_token(app_and_store: tuple[Any, FakeSessionStore]) -> None:
    app, store = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)
    # No credential: the middleware refuses it.
    assert client.post("/auth/session/logout").status_code == 401
    assert store.revoked == []


def test_logout_store_outage_is_503_not_a_silent_success(
    app_and_store: tuple[Any, FakeSessionStore],
) -> None:
    app, store = app_and_store
    client = TestClient(app, base_url="http://sp.test", raise_server_exceptions=False)

    async def _down(self: Any, token: str) -> bool:
        raise SessionStoreUnavailableError("down")

    store.revoke_token = _down.__get__(store)  # type: ignore[method-assign]
    r = client.post("/auth/session/logout", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 503
