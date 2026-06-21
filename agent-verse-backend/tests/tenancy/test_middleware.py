"""Tests for TenantMiddleware and SecurityHeadersMiddleware."""

from fastapi import FastAPI
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
