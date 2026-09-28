"""Public / third-party-called routes must reach their own auth, not a generic 401.

Regression: ``/.well-known/*`` (JWKS, A2A agent card), ``/auth/google/*`` (OAuth
login start + callback), ``/triggers/webhooks/*`` (GitHub/Stripe/... typed
webhook delivery) and the ``/channels/*`` inbound webhooks were missing from
``_BYPASS_PREFIXES``. None of those callers can send an AgentVerse API key, so
TenantMiddleware rejected every request with 401 before the handler's own
signature / token / PKCE check ever ran — the routes were unreachable.

The bypass must stay narrow: the tenant-authenticated routes that share those
prefixes (``/channels/mappings``, ``/triggers`` CRUD) still require an API key.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.tenancy.context import TenantContext
from app.tenancy.middleware import TenantMiddleware


async def _reject_all(_key: str) -> TenantContext | None:
    return None


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_reject_all)

    @app.api_route("/{full_path:path}", methods=["GET", "POST"])
    async def _echo(full_path: str, request: Request) -> dict[str, str]:
        return {"reached": full_path}

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/.well-known/jwks.json"),
        ("GET", "/.well-known/agent.json"),
        ("GET", "/.well-known/agents"),
        ("GET", "/auth/google/login"),
        ("GET", "/auth/google/callback"),
        ("POST", "/triggers/webhooks/github/tok123"),
        ("POST", "/channels/slack/events"),
        ("POST", "/channels/teams/events"),
        ("POST", "/channels/discord/events"),
        ("POST", "/channels/email/inbound"),
        ("POST", "/channels/sms/inbound"),
        ("POST", "/channels/voice/transcript"),
        ("POST", "/channels/forms/form-1"),
        ("POST", "/channels/meeting/ended"),
    ],
)
def test_public_route_reaches_its_handler(method: str, path: str) -> None:
    resp = _client().request(method, path)
    assert resp.status_code == 200, f"{path} was blocked by TenantMiddleware: {resp.text}"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/channels/mappings"),
        ("POST", "/channels/mappings"),
        ("GET", "/triggers"),
        ("POST", "/triggers"),
        ("GET", "/triggers/abc123"),
        ("GET", "/auth/me"),
        ("GET", "/goals"),
    ],
)
def test_tenant_routes_sharing_a_prefix_still_require_auth(method: str, path: str) -> None:
    resp = _client().request(method, path)
    assert resp.status_code == 401, f"{path} must still require an API key"


def test_api_key_in_the_query_string_is_not_accepted() -> None:
    """Regression: ?api_key= authenticated requests, putting the permanent key
    into access logs, proxy logs and browser history."""
    from starlette.requests import Request

    from app.tenancy.middleware import _extract_key

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/goals",
        "headers": [],
        "query_string": b"api_key=ak_live_secret",
    }
    assert _extract_key(Request(scope)) is None
    scope["headers"] = [(b"x-api-key", b"ak_header")]
    assert _extract_key(Request(scope)) == "ak_header"
