"""Regression tests: the SAML ACS is reachable by an IdP and never fakes a login.

1. ``POST /enterprise/saml/acs`` required a tenant API key (``_require_tenant``)
   that an IdP's HTTP-POST binding never carries — the ACS was unreachable.
2. The advertised ACS URL was ``{base}/api/enterprise/saml/acs`` while the
   route is ``/enterprise/saml/acs``.
3. A verified assertion answered 200 ``{"authenticated": true}`` although no
   session was issued (then an honest 501). It now JIT-provisions the person and
   redirects (303) with a one-time login code (SAML-01).
4. Replay protection read ``app.state.redis`` (never set), so it never ran.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import enterprise as ent
from app.auth.saml_provider import SAMLIdentity, SAMLProvider
from app.services.tenant_service import TenantService
from app.tenancy.middleware import TenantMiddleware

_ROW = ("idp-entity", "https://idp.example/sso", "CERT", "sp-entity", {}, "operator", True)


class _Session:
    def __init__(self) -> None:
        self.tids: list[str] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        if params and "tid" in params:
            self.tids.append(params["tid"])
        result = MagicMock()
        result.fetchone.return_value = None if "set_config" in str(stmt) else _ROW
        return result


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def provision_member(self, **kw: Any) -> str:
        self.calls.append(("provision", kw))
        return "user-1"

    async def issue_login_code(self, **kw: Any) -> str:
        self.calls.append(("code", kw))
        return "one-time-code-123456"


def _app() -> FastAPI:
    svc = TenantService()
    app = FastAPI()
    app.state.tenant_service = svc
    app.state._redis = MagicMock()
    app.state.user_session_store = _Store()
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)
    app.include_router(ent.router)
    return app


def test_acs_needs_no_api_key_and_starts_a_session_for_a_valid_assertion() -> None:
    session = _Session()
    identity = SAMLIdentity(email="u@corp.test", name_id="u@corp.test")
    seen: dict[str, Any] = {}

    async def _process(self: SAMLProvider, resp: str) -> SAMLIdentity:
        seen["acs_url"] = self._acs_url
        seen["redis"] = self._redis
        return identity

    app = _app()
    with (
        patch.object(ent, "_get_db", return_value=lambda: session),
        patch.object(SAMLProvider, "process_acs", _process),
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/enterprise/saml/acs/tenant-a",
            data={"SAMLResponse": "PHNhbWw+"},
            follow_redirects=False,
        )
    assert resp.status_code == 303, resp.text
    location = resp.headers["location"]
    assert location.endswith("/auth/sso/complete?code=one-time-code-123456")
    assert "avs_" not in location  # the bearer token never travels in a URL
    store = app.state.user_session_store
    assert store.calls == [
        (
            "provision",
            {
                "tenant_id": "tenant-a",
                "email": "u@corp.test",
                "name": None,
                "default_role": "operator",
                "jit": True,
            },
        ),
        ("code", {"tenant_id": "tenant-a", "user_id": "user-1", "auth_method": "saml"}),
    ]
    assert "tenant-a" in session.tids  # config read under the path tenant's RLS
    assert seen["acs_url"].endswith("/enterprise/saml/acs/tenant-a")
    assert "/api/" not in seen["acs_url"]
    assert seen["redis"] is app.state._redis  # replay cache actually wired


def test_invalid_assertion_is_401() -> None:
    with (
        patch.object(ent, "_get_db", return_value=_Session),
        patch.object(
            SAMLProvider, "process_acs", AsyncMock(side_effect=ValueError("bad signature"))
        ),
    ):
        resp = TestClient(_app(), raise_server_exceptions=False).post(
            "/enterprise/saml/acs/tenant-a", data={"SAMLResponse": "PHNhbWw+"}
        )
    assert resp.status_code == 401


def test_advertised_acs_url_is_a_routed_path() -> None:
    request = MagicMock()
    request.base_url = "https://sp.example/"
    url = ent._saml_acs_url(request, "tenant-a")
    assert url == "https://sp.example/enterprise/saml/acs/tenant-a"
    paths = {getattr(r, "path", "") for r in ent.router.routes}
    assert "/enterprise/saml/acs/{tenant_id}" in paths
