"""Tests for TenantMiddleware and SecurityHeadersMiddleware."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware


def _make_app(resolver: dict[str, TenantContext] | None = None) -> FastAPI:
    """Build a minimal FastAPI app with TenantMiddleware wired up."""
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return (resolver or {}).get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)

    from fastapi import Request

    @app.get("/protected")
    async def protected(request: Request) -> dict[str, str]:
        ctx: TenantContext = request.state.tenant
        return {"tenant_id": ctx.tenant_id}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


_CTX = TenantContext(tenant_id="tid-abc", plan=PlanTier.STARTER, api_key_id="kid-1")
_VALID_KEY = "ak_test_validkey123"
_APP = _make_app(resolver={_VALID_KEY: _CTX})


def test_missing_api_key_returns_401() -> None:
    client = TestClient(_APP, raise_server_exceptions=False)
    resp = client.get("/protected")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "AUTHENTICATION_ERROR"


def test_bearer_token_auth_works() -> None:
    client = TestClient(_APP, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"Authorization": f"Bearer {_VALID_KEY}"})
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == "tid-abc"


def test_x_api_key_header_auth_works() -> None:
    client = TestClient(_APP, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == "tid-abc"


def test_invalid_api_key_returns_401() -> None:
    client = TestClient(_APP, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401


def test_health_endpoint_bypasses_auth() -> None:
    client = TestClient(_APP, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200


def test_security_headers_present_on_response() -> None:
    client = TestClient(_APP, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert "strict-origin-when-cross-origin" in resp.headers.get("referrer-policy", "")


def test_docs_endpoint_bypasses_auth() -> None:
    client = TestClient(_APP, raise_server_exceptions=False)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200


def test_cors_preflight_bypasses_auth() -> None:
    app = FastAPI()

    async def _resolve(_: str) -> TenantContext | None:
        return None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)

    @app.get("/protected")
    async def protected() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.options(
        "/protected",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_headers_present_on_scope_403() -> None:
    """CORS headers must be on 403 responses from ScopeEnforcementMiddleware.

    Regression test: CORSMiddleware was previously added innermost (first), so
    403s from ScopeEnforcement bypassed it and the browser saw a CORS error
    instead of an actionable 403.  CORSMiddleware must be outermost (added last)
    to wrap ALL responses.
    """
    from app.auth.scope_enforcement import ScopeEnforcementMiddleware
    from app.tenancy.context import TenantContext

    scope_app = FastAPI()

    tenant_ctx = TenantContext(
        tenant_id="t1",
        plan=PlanTier.FREE,
        api_key_id="k1",
        roles=("operator",),  # operator doesn't have governance:read
    )

    async def _resolve(_: str) -> TenantContext | None:
        return tenant_ctx

    # Correct order: CORS outermost (added last), scope enforcement inner
    scope_app.add_middleware(SecurityHeadersMiddleware)
    scope_app.add_middleware(ScopeEnforcementMiddleware)
    scope_app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    scope_app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from fastapi import Request

    @scope_app.get("/governance/approvals")
    async def approvals(request: Request) -> dict:
        return {"approvals": []}

    client = TestClient(scope_app, raise_server_exceptions=False)
    resp = client.get(
        "/governance/approvals",
        headers={"X-Api-Key": "any-key", "Origin": "http://localhost:5173"},
    )

    assert resp.status_code == 403
    assert "access-control-allow-origin" in resp.headers, (
        "CORS header missing on 403 — CORSMiddleware must be outermost"
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
