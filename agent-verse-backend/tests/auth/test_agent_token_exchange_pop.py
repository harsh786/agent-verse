"""Token exchange requires proof of possession of the credential's private key.

POST /agents/{id}/token used to mint a JWT for anyone holding the public
``key_id`` (the JWT ``kid``) plus a tenant key that owns the agent — the
private key returned at issuance was never needed. It now requires an RFC 7523
``private_key_jwt`` client assertion signed by that credential's private key,
bound to the agent and the token endpoint, short-lived and single-use.
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.api.agents import router as agents_router
from app.auth.agent_identity import (
    CLIENT_ASSERTION_TYPE,
    AgentIdentityService,
    build_client_assertion,
    generate_agent_keypair,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_KEY = "av_test_pop_key"
_H = {"X-API-Key": _KEY}
_AUD = "/agents/agent-1/token"


def _svc(public_pem: str, *, agent_id: str = "agent-1") -> AgentIdentityService:
    svc = AgentIdentityService(db=object(), vault=None, redis=fakeredis.aioredis.FakeRedis())
    rows = {("kid-1", "t1"): (agent_id, public_pem, ["goals:read"])}

    async def _fetch(key_id: str, tenant_id: str) -> Any:
        return rows.get((key_id, tenant_id))

    svc._fetch_verification_key = _fetch  # type: ignore[method-assign]
    svc.issue_agent_jwt = AsyncMock(return_value="minted.jwt.token")  # type: ignore[method-assign]
    return svc


def _raw_assertion(private_pem: str, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "agent:agent-1",
        "sub": "agent:agent-1",
        "aud": _AUD,
        "iat": now,
        "exp": now + 60,
        "jti": uuid.uuid4().hex,
    }
    headers = {"kid": overrides.pop("kid", "kid-1")}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_pem, algorithm="RS256", headers=headers)


# ── Service ──────────────────────────────────────────────────────────────────


async def test_valid_assertion_returns_the_key_id() -> None:
    priv, pub = generate_agent_keypair()
    svc = _svc(pub)
    assertion = build_client_assertion(
        agent_id="agent-1", key_id="kid-1", private_key_pem=priv, audience=_AUD
    )
    assert await svc.verify_client_assertion(assertion, "agent-1", "t1", _AUD) == "kid-1"


async def test_assertion_signed_by_another_key_is_rejected() -> None:
    _, pub = generate_agent_keypair()
    other_priv, _ = generate_agent_keypair()
    svc = _svc(pub)
    assert await svc.verify_client_assertion(_raw_assertion(other_priv), "agent-1", "t1", _AUD) is None


async def test_replayed_jti_is_rejected() -> None:
    priv, pub = generate_agent_keypair()
    svc = _svc(pub)
    assertion = _raw_assertion(priv)
    assert await svc.verify_client_assertion(assertion, "agent-1", "t1", _AUD) == "kid-1"
    assert await svc.verify_client_assertion(assertion, "agent-1", "t1", _AUD) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "/agents/agent-2/token"},  # another endpoint / agent
        {"sub": "agent:agent-2"},
        {"iss": "agent:agent-2"},
        {"exp": int(time.time()) - 5},  # expired
        {"exp": int(time.time()) + 3600},  # far too long-lived
        {"jti": None},  # no jti -> cannot be single-use
        {"kid": "kid-unknown"},
    ],
)
async def test_bad_claims_are_rejected(overrides: dict[str, Any]) -> None:
    priv, pub = generate_agent_keypair()
    svc = _svc(pub)
    assertion = _raw_assertion(priv, **overrides)
    assert await svc.verify_client_assertion(assertion, "agent-1", "t1", _AUD) is None


async def test_credential_of_another_agent_is_rejected() -> None:
    priv, pub = generate_agent_keypair()
    svc = _svc(pub, agent_id="agent-2")  # kid-1 belongs to agent-2
    assert await svc.verify_client_assertion(_raw_assertion(priv), "agent-1", "t1", _AUD) is None


async def test_no_replay_store_fails_closed() -> None:
    priv, pub = generate_agent_keypair()
    svc = _svc(pub)
    svc._redis = None
    with pytest.raises(RuntimeError, match="replay"):
        await svc.verify_client_assertion(_raw_assertion(priv), "agent-1", "t1", _AUD)


# ── Endpoint ─────────────────────────────────────────────────────────────────


def _app(svc: AgentIdentityService) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(agents_router)
    store = AsyncMock()

    async def _get(agent_id: str, *, tenant_ctx: Any = None) -> dict[str, Any] | None:
        return {"id": agent_id} if agent_id == "agent-1" else None

    store.get_async = AsyncMock(side_effect=_get)
    app.state.agent_store = store
    app.state.agent_identity_service = svc
    return app


def test_exchange_with_only_key_id_is_rejected() -> None:
    _, pub = generate_agent_keypair()
    svc = _svc(pub)
    client = TestClient(_app(svc))
    r = client.post("/agents/agent-1/token", json={"key_id": "kid-1"}, headers=_H)
    assert r.status_code == 401
    svc.issue_agent_jwt.assert_not_called()  # type: ignore[attr-defined]


def test_exchange_with_forged_assertion_is_rejected() -> None:
    _, pub = generate_agent_keypair()
    other_priv, _ = generate_agent_keypair()
    svc = _svc(pub)
    client = TestClient(_app(svc))
    body = {
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": _raw_assertion(other_priv),
    }
    r = client.post("/agents/agent-1/token", json=body, headers=_H)
    assert r.status_code == 401
    svc.issue_agent_jwt.assert_not_called()  # type: ignore[attr-defined]


def test_exchange_with_valid_assertion_mints_once() -> None:
    priv, pub = generate_agent_keypair()
    svc = _svc(pub)
    client = TestClient(_app(svc))
    body = {"client_assertion_type": CLIENT_ASSERTION_TYPE, "client_assertion": _raw_assertion(priv)}
    r = client.post("/agents/agent-1/token", json=body, headers=_H)
    assert r.status_code == 200, r.text
    assert r.json()["token"] == "minted.jwt.token"
    svc.issue_agent_jwt.assert_awaited_once_with(  # type: ignore[attr-defined]
        agent_id="agent-1", key_id="kid-1", tenant_id="t1"
    )
    # The same assertion cannot be replayed.
    assert client.post("/agents/agent-1/token", json=body, headers=_H).status_code == 401


def test_exchange_rejects_key_id_header_that_disagrees_with_the_assertion() -> None:
    priv, pub = generate_agent_keypair()
    svc = _svc(pub)
    client = TestClient(_app(svc))
    body = {"client_assertion_type": CLIENT_ASSERTION_TYPE, "client_assertion": _raw_assertion(priv)}
    r = client.post(
        "/agents/agent-1/token", json=body, headers={**_H, "X-Agent-Key-Id": "kid-other"}
    )
    assert r.status_code == 401
