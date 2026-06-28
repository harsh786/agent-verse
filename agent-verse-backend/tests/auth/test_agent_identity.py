"""Unit tests for Agent Identity — cryptographic credentials + SSO fixes.

Tests:
1. test_issue_credential_generates_valid_jwt
2. test_revoked_credential_cannot_issue_tokens
3. test_sso_creates_real_db_key_record
4. test_domain_metadata_validates_for_legal
5. test_redis_cache_avoids_db_on_second_lookup
6. test_default_role_is_operator_not_admin
7. test_cryptographic_key_passes_entropy_check
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import router as agents_router
from app.auth.agent_identity import (
    AgentIdentityService,
    generate_agent_keypair,
    generate_api_key,
    issue_agent_token,
    verify_agent_token,
)
from app.services.tenant_service import TenantService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CTX = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_identity_key"


def _make_agents_app(agent_store: Any | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(agents_router)
    from app.api.agents import AgentStore
    app.state.agent_store = agent_store or AgentStore()
    app.state.meta_agent = AsyncMock()
    app.state.agent_identity_service = None  # overridden per test
    return app


# ---------------------------------------------------------------------------
# Test 1 — Issue credential generates a valid JWT
# ---------------------------------------------------------------------------


def test_issue_credential_generates_valid_jwt() -> None:
    """generate_agent_keypair + issue_agent_token + verify_agent_token round-trip."""
    private_pem, public_pem = generate_agent_keypair()

    token = issue_agent_token(
        agent_id="agent-123",
        tenant_id="t1",
        key_id="kid1",
        private_key_pem=private_pem,
        scopes=["goals:execute", "knowledge:read"],
        autonomy_mode="bounded-autonomous",
    )

    claims = verify_agent_token(token, public_pem, tenant_id="t1")

    assert claims["sub"] == "agent:agent-123"
    assert "goals:execute" in claims["scopes"]
    assert claims["tenant_id"] == "t1"
    assert claims["domain_context"] == "general"
    assert "agentverse-api" in claims["aud"]


# ---------------------------------------------------------------------------
# Test 2 — Revoked credential cannot issue tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_credential_cannot_issue_tokens() -> None:
    """Revoking a credential must prevent further JWT issuance."""
    # Build a mock DB session factory that simulates revoked_at IS NOT NULL
    # by returning no rows after revocation.
    issued_credentials: dict[str, dict[str, Any]] = {}

    class _MockSession:
        async def __aenter__(self) -> _MockSession:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        async def execute(self, stmt: Any, params: Any = None) -> Any:
            sql = str(stmt)
            result = MagicMock()
            result.rowcount = 0

            if "INSERT INTO agent_credentials" in sql:
                kid = (params or {}).get("kid", "kid_test")
                issued_credentials[kid] = {
                    "revoked": False,
                    "vault_ref": (params or {}).get("vault"),
                }
                result.rowcount = 1
            elif "UPDATE agent_credentials SET revoked_at" in sql:
                kid = (params or {}).get("kid", "")
                if kid in issued_credentials:
                    issued_credentials[kid]["revoked"] = True
                    result.rowcount = 1
            elif "SELECT ac.scopes" in sql:
                kid = (params or {}).get("kid", "")
                cred = issued_credentials.get(kid)
                if cred and not cred["revoked"]:
                    mock_row = MagicMock()
                    mock_row.__iter__ = lambda s: iter(
                        [["goals:execute"], "bounded-autonomous", "general", None]
                    )
                    result.fetchone = lambda: mock_row
                else:
                    result.fetchone = lambda: None
            return result

        async def commit(self) -> None:
            pass

    def _mock_db_factory() -> _MockSession:
        """Sync factory — returns async context manager directly."""
        return _MockSession()

    mock_vault = AsyncMock()
    private_pem, public_pem = generate_agent_keypair()
    mock_vault.store = AsyncMock(return_value="vault://agent_key/test")
    mock_vault.retrieve = AsyncMock(return_value=private_pem)

    svc = AgentIdentityService(db=_mock_db_factory, vault=mock_vault, redis=None)

    # Issue the credential
    result = await svc.issue_credential(
        agent_id="agent-123",
        tenant_id="t1",
        created_by="user-1",
        scopes=["goals:execute"],
    )
    key_id = result["key_id"]

    # Revoke it
    revoked = await svc.revoke_credential(key_id=key_id, tenant_id="t1")
    assert revoked is True

    # Attempt to issue a JWT with the revoked key → should return None
    token = await svc.issue_agent_jwt(
        agent_id="agent-123", key_id=key_id, tenant_id="t1"
    )
    assert token is None


# ---------------------------------------------------------------------------
# Test 3 — SSO creates a real DB key record (not a ghost key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_creates_real_db_key_record() -> None:
    """resolve_tenant_from_jwt must return a real key_id, not 'sso:{sub[:16]}'."""
    from app.auth.keycloak import resolve_tenant_from_jwt

    sub = "keycloak-sub-abc123"
    email = "user@example.com"
    real_key_id = "real_key_from_db_456"

    mock_tenant_svc = AsyncMock()
    mock_tenant_svc.get_tenant_by_sso_sub.return_value = {
        "tenant_id": "tid-sso",
        "name": "SSO User",
        "email": email,
        "plan": "starter",
        "sso_sub": sub,
    }
    mock_tenant_svc.get_key_by_sso_sub.return_value = {
        "key_id": real_key_id,
        "name": f"SSO:{email}",
        "tenant_id": "tid-sso",
        "sso_sub": sub,
    }

    mock_payload = {
        "sub": sub,
        "email": email,
        "name": "SSO User",
        "realm_access": {"roles": ["viewer"]},
    }

    with patch("app.auth.keycloak.validate_jwt", return_value=mock_payload):
        ctx = await resolve_tenant_from_jwt("fake-token", mock_tenant_svc)

    assert ctx is not None
    # Must NOT be the ghost key format
    assert not ctx.api_key_id.startswith("sso:")
    # Must be the real DB key
    assert ctx.api_key_id == real_key_id


# ---------------------------------------------------------------------------
# Test 4 — Domain metadata validates for legal agents
# ---------------------------------------------------------------------------


def test_domain_metadata_validates_for_legal() -> None:
    """Legal agents without bar_number must fail with 422."""
    app = _make_agents_app()
    client = TestClient(app, raise_server_exceptions=False)

    # Without bar_number → 422
    resp = client.post(
        "/agents",
        json={
            "name": "Legal Bot",
            "domain_context": "legal",
            "domain_metadata": {"jurisdiction": "CA"},  # missing bar_number
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422, resp.text

    # With bar_number → 201
    resp = client.post(
        "/agents",
        json={
            "name": "Legal Bot 2",
            "domain_context": "legal",
            "domain_metadata": {"bar_number": "CA12345", "jurisdiction": "CA"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["domain_context"] == "legal"


# ---------------------------------------------------------------------------
# Test 5 — Redis cache avoids DB on second lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_cache_avoids_db_on_second_lookup() -> None:
    """Second resolve_api_key call must return from Redis without hitting in-memory."""
    import hashlib

    raw_key = "av_test_redis_cache_key"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    cache_key = f"api_key:{key_hash}"

    # Populate a TenantService with in-memory data
    svc = TenantService()
    svc._tenants["t1"] = {"tenant_id": "t1", "name": "Test", "plan": "starter"}
    svc._keys["kid1"] = {
        "key_id": "kid1",
        "tenant_id": "t1",
        "name": "Test Key",
        "scopes": [],
        "expires_at": None,
        "key_hash": key_hash,
        "is_active": True,
        "created_at": "2024-01-01T00:00:00",
    }
    svc._hash_to_key_id[key_hash] = "kid1"
    svc._tenant_keys["t1"] = ["kid1"]

    # Wire a mock Redis
    redis_store: dict[str, str] = {}

    class _MockRedis:
        async def get(self, key: str) -> str | None:
            return redis_store.get(key)

        async def setex(self, key: str, _ttl: int, value: str) -> None:
            redis_store[key] = value

    svc._redis = _MockRedis()

    # First call: Redis miss → in-memory hit → store in Redis
    ctx1 = await svc.resolve_api_key(raw_key)
    assert ctx1 is not None
    assert ctx1.tenant_id == "t1"
    assert cache_key in redis_store  # written to Redis

    # Corrupt the in-memory store to prove the second call comes from Redis
    svc._hash_to_key_id.clear()

    # Second call: Redis hit → returns TenantContext without touching in-memory
    ctx2 = await svc.resolve_api_key(raw_key)
    assert ctx2 is not None
    assert ctx2.tenant_id == "t1"


# ---------------------------------------------------------------------------
# Test 6 — Default role is operator, not admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_role_is_operator_not_admin() -> None:
    """API keys without explicit roles must default to 'operator', not 'admin'."""
    import hashlib

    raw_key = "av_test_operator_role_key"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    svc = TenantService()
    svc._tenants["t1"] = {"tenant_id": "t1", "name": "Test", "plan": "free"}
    svc._keys["kid1"] = {
        "key_id": "kid1",
        "tenant_id": "t1",
        "name": "Default Key",
        "scopes": [],
        "expires_at": None,
        "key_hash": key_hash,
        "is_active": True,
        "created_at": "2024-01-01T00:00:00",
        # No 'roles' key → should fall back to ("operator",)
    }
    svc._hash_to_key_id[key_hash] = "kid1"
    svc._tenant_keys["t1"] = ["kid1"]

    ctx = await svc.resolve_api_key(raw_key)
    assert ctx is not None
    assert "operator" in ctx.roles
    assert "admin" not in ctx.roles


# ---------------------------------------------------------------------------
# Test 7 — Cryptographic key passes entropy check
# ---------------------------------------------------------------------------


def test_cryptographic_key_passes_entropy_check() -> None:
    """generate_api_key() must produce high-entropy keys (≥ 44 chars, av_ prefix)."""
    key = generate_api_key()

    assert key.startswith("av_"), f"Key should start with 'av_', got: {key[:10]}"
    # 'av_' (3) + base64url(32 bytes) = 3 + 43 = 46 chars minimum
    assert len(key) >= 44, f"Key too short: {len(key)} chars"

    # Generate 5 keys and verify all are unique (collision would indicate low entropy)
    keys = {generate_api_key() for _ in range(5)}
    assert len(keys) == 5, "Generated duplicate keys — entropy is too low"
