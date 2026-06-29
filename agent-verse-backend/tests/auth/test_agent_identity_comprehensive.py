"""Comprehensive tests for app/auth/agent_identity.py."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import jwt as jose_jwt

from app.auth.agent_identity import (
    JWT_ALGORITHM,
    JWT_EXPIRY_MINUTES,
    AgentIdentityService,
    _build_jwks,
    generate_agent_keypair,
    generate_api_key,
    issue_agent_token,
    verify_agent_token,
)


# ---------------------------------------------------------------------------
# generate_agent_keypair
# ---------------------------------------------------------------------------


def test_generate_agent_keypair_returns_pem_strings():
    private_pem, public_pem = generate_agent_keypair()
    assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert public_pem.startswith("-----BEGIN PUBLIC KEY-----")


def test_generate_agent_keypair_keys_are_2048_bits():
    private_pem, _ = generate_agent_keypair()
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key = load_pem_private_key(private_pem.encode(), password=None)
    assert key.key_size == 2048


def test_generate_agent_keypair_each_call_different():
    priv1, _ = generate_agent_keypair()
    priv2, _ = generate_agent_keypair()
    assert priv1 != priv2


# ---------------------------------------------------------------------------
# generate_api_key
# ---------------------------------------------------------------------------


def test_generate_api_key_prefix():
    key = generate_api_key()
    assert key.startswith("av_")


def test_generate_api_key_minimum_length():
    key = generate_api_key()
    # "av_" + 32 bytes URL-safe base64 ≈ 44 chars
    assert len(key) >= 44


def test_generate_api_key_unique():
    keys = {generate_api_key() for _ in range(10)}
    assert len(keys) == 10


def test_generate_api_key_url_safe():
    key = generate_api_key()
    # URL-safe base64: no + or /
    suffix = key[3:]
    assert "+" not in suffix
    assert "/" not in suffix


# ---------------------------------------------------------------------------
# issue_agent_token
# ---------------------------------------------------------------------------


def _gen_keypair():
    return generate_agent_keypair()


def test_issue_agent_token_valid_jwt():
    private_pem, public_pem = _gen_keypair()
    token = issue_agent_token(
        agent_id="agent-1",
        tenant_id="t1",
        key_id="kid-1",
        private_key_pem=private_pem,
        scopes=["goals:read", "goals:execute"],
        autonomy_mode="bounded-autonomous",
    )
    assert isinstance(token, str)
    assert len(token) > 0


def test_issue_agent_token_contains_expected_claims():
    private_pem, public_pem = _gen_keypair()
    token = issue_agent_token(
        agent_id="agent-42",
        tenant_id="t99",
        key_id="kid-x",
        private_key_pem=private_pem,
        scopes=["goals:read"],
        autonomy_mode="full-auto",
        domain_context="legal",
        parent_goal_id="g-parent",
        delegated_by="user-1",
    )
    # Use verify_aud=False; python-jose requires audience as string not list
    payload = jose_jwt.decode(
        token,
        public_pem,
        algorithms=[JWT_ALGORITHM],
        options={"verify_aud": False},
    )
    assert payload["agent_id"] == "agent-42"
    assert payload["tenant_id"] == "t99"
    assert payload["autonomy_mode"] == "full-auto"
    assert payload["domain_context"] == "legal"
    assert payload["parent_goal_id"] == "g-parent"
    assert payload["delegated_by"] == "user-1"
    assert "goals:read" in payload["scopes"]


def test_issue_agent_token_kid_header():
    private_pem, _ = _gen_keypair()
    token = issue_agent_token(
        agent_id="a1", tenant_id="t1", key_id="my-kid",
        private_key_pem=private_pem, scopes=[], autonomy_mode="manual",
    )
    headers = jose_jwt.get_unverified_header(token)
    assert headers["kid"] == "my-kid"


def test_issue_agent_token_expiry():
    private_pem, public_pem = _gen_keypair()
    token = issue_agent_token(
        agent_id="a1", tenant_id="t1", key_id="k1",
        private_key_pem=private_pem, scopes=[], autonomy_mode="manual",
        expiry_minutes=30,
    )
    payload = jose_jwt.decode(
        token, public_pem, algorithms=[JWT_ALGORITHM],
        options={"verify_aud": False},
    )
    now = int(datetime.now(UTC).timestamp())
    # Expiry should be approximately 30 minutes from now
    assert payload["exp"] > now + 29 * 60


def test_issue_agent_token_no_parent_no_delegated_by():
    private_pem, _ = _gen_keypair()
    token = issue_agent_token(
        agent_id="a1", tenant_id="t1", key_id="k1",
        private_key_pem=private_pem, scopes=[], autonomy_mode="manual",
    )
    headers = jose_jwt.get_unverified_header(token)
    payload = jose_jwt.get_unverified_claims(token)
    assert "parent_goal_id" not in payload
    assert "delegated_by" not in payload


# ---------------------------------------------------------------------------
# verify_agent_token
# ---------------------------------------------------------------------------


def test_verify_agent_token_success():
    private_pem, public_pem = _gen_keypair()
    token = issue_agent_token(
        agent_id="a1", tenant_id="t1", key_id="k1",
        private_key_pem=private_pem, scopes=["goals:read"],
        autonomy_mode="bounded-autonomous",
    )
    payload = verify_agent_token(token, public_pem, tenant_id="t1")
    assert payload["agent_id"] == "a1"
    assert payload["tenant_id"] == "t1"


def test_verify_agent_token_wrong_tenant_raises():
    private_pem, public_pem = _gen_keypair()
    token = issue_agent_token(
        agent_id="a1", tenant_id="t1", key_id="k1",
        private_key_pem=private_pem, scopes=[], autonomy_mode="manual",
    )
    with pytest.raises(ValueError, match="Invalid issuer"):
        verify_agent_token(token, public_pem, tenant_id="wrong-tenant")


def test_verify_agent_token_wrong_audience_raises():
    """Token with non-agentverse-api audience should be rejected."""
    from jose import jwt as _jwt
    private_pem, public_pem = _gen_keypair()
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "iss": "agentverse:t1",
        "sub": "agent:a1",
        "aud": ["other-service"],  # Wrong audience
        "exp": now + 900,
        "iat": now,
        "jti": "abc",
        "agent_id": "a1",
        "tenant_id": "t1",
        "autonomy_mode": "manual",
        "scopes": [],
    }
    token = _jwt.encode(payload, private_pem, algorithm=JWT_ALGORITHM)
    with pytest.raises(ValueError, match="Invalid audience"):
        verify_agent_token(token, public_pem, tenant_id="t1")


def test_verify_agent_token_string_audience_accepted():
    """Token with 'agentverse-api' as string aud should be accepted."""
    from jose import jwt as _jwt
    private_pem, public_pem = _gen_keypair()
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "iss": "agentverse:t1",
        "sub": "agent:a1",
        "aud": "agentverse-api",  # Single string
        "exp": now + 900,
        "iat": now,
        "jti": "abc",
        "agent_id": "a1",
        "tenant_id": "t1",
        "autonomy_mode": "manual",
        "scopes": [],
    }
    token = _jwt.encode(payload, private_pem, algorithm=JWT_ALGORITHM)
    result = verify_agent_token(token, public_pem, tenant_id="t1")
    assert result["agent_id"] == "a1"


# ---------------------------------------------------------------------------
# _build_jwks
# ---------------------------------------------------------------------------


async def test_build_jwks_returns_rsa_keys():
    private_pem, public_pem = _gen_keypair()

    row = ("kid-abc", public_pem)
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    result_mock = MagicMock()
    result_mock.fetchall.return_value = [row]
    session_mock.execute = AsyncMock(return_value=result_mock)
    # Use MagicMock so db_factory() returns session_mock directly (not a coroutine)
    db_factory = MagicMock(return_value=session_mock)

    keys = await _build_jwks(db_factory)

    assert len(keys) == 1
    assert keys[0]["kty"] == "RSA"
    assert keys[0]["kid"] == "kid-abc"
    assert keys[0]["alg"] == "RS256"
    assert "n" in keys[0]
    assert "e" in keys[0]


async def test_build_jwks_empty_when_db_fails():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(side_effect=Exception("DB error"))
    db_factory = MagicMock(return_value=session_mock)

    keys = await _build_jwks(db_factory)
    assert keys == []


# ---------------------------------------------------------------------------
# AgentIdentityService
# ---------------------------------------------------------------------------


def test_agent_identity_service_set_db():
    svc = AgentIdentityService()
    assert svc._db is None
    db = MagicMock()
    svc.set_db(db)
    assert svc._db is db


def test_agent_identity_service_set_redis():
    svc = AgentIdentityService()
    assert svc._redis is None
    redis = MagicMock()
    svc.set_redis(redis)
    assert svc._redis is redis


async def test_issue_credential_no_db_returns_result_without_db():
    svc = AgentIdentityService(db=None)
    result = await svc.issue_credential(
        agent_id="a1",
        tenant_id="t1",
        created_by="user-1",
        scopes=["goals:read"],
    )
    # Even without DB, keypair is generated and returned
    assert "key_id" in result
    assert result["private_key_pem"].startswith("-----BEGIN PRIVATE KEY-----")
    assert result["public_key_pem"].startswith("-----BEGIN PUBLIC KEY-----")
    assert result["scopes"] == ["goals:read"]
    assert "warning" in result


async def test_issue_credential_with_db_stores_public_key():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    svc = AgentIdentityService(db=db_factory)
    result = await svc.issue_credential(
        agent_id="a1",
        tenant_id="t1",
        created_by="admin",
        scopes=["goals:read"],
        expires_in_days=90,
    )

    assert result["expires_at"] is not None
    session_mock.commit.assert_awaited_once()


async def test_issue_credential_stores_in_vault_if_configured():
    vault_mock = AsyncMock()
    vault_mock.store = AsyncMock(return_value="vault://key-ref-123")

    svc = AgentIdentityService(db=None, vault=vault_mock)
    result = await svc.issue_credential(
        agent_id="a1", tenant_id="t1", created_by="user",
        scopes=[], expires_in_days=None,
    )

    vault_mock.store.assert_awaited_once()
    assert result["expires_at"] is None


async def test_issue_credential_clears_jwks_cache_on_redis():
    redis_mock = AsyncMock()
    redis_mock.delete = AsyncMock()

    svc = AgentIdentityService(db=None, redis=redis_mock)
    await svc.issue_credential(
        agent_id="a1", tenant_id="t1", created_by="user", scopes=[],
    )

    redis_mock.delete.assert_awaited_with("jwks:cache")


async def test_revoke_credential_no_db_returns_false():
    svc = AgentIdentityService(db=None)
    result = await svc.revoke_credential("kid-1", "t1")
    assert result is False


async def test_revoke_credential_with_db_returns_true_on_success():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    result_mock = MagicMock()
    result_mock.rowcount = 1
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    svc = AgentIdentityService(db=db_factory)
    result = await svc.revoke_credential("kid-1", "t1")
    assert result is True


async def test_revoke_credential_returns_false_when_not_found():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    result_mock = MagicMock()
    result_mock.rowcount = 0
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    svc = AgentIdentityService(db=db_factory)
    result = await svc.revoke_credential("nonexistent-kid", "t1")
    assert result is False


async def test_revoke_credential_notifies_redis_on_success():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    result_mock = MagicMock()
    result_mock.rowcount = 1
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    redis_mock = AsyncMock()
    redis_mock.delete = AsyncMock()
    redis_mock.publish = AsyncMock()

    svc = AgentIdentityService(db=db_factory, redis=redis_mock)
    await svc.revoke_credential("kid-1", "t1")

    redis_mock.delete.assert_awaited_with("jwks:cache")
    redis_mock.publish.assert_awaited_with("jwks_invalidated", "kid-1")


async def test_issue_agent_jwt_no_db_returns_none():
    svc = AgentIdentityService(db=None)
    result = await svc.issue_agent_jwt("a1", "kid-1", "t1")
    assert result is None


async def test_issue_agent_jwt_key_not_found_returns_none():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    result_mock = MagicMock()
    result_mock.fetchone.return_value = None
    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)

    svc = AgentIdentityService(db=db_factory)
    result = await svc.issue_agent_jwt("a1", "nonexistent-kid", "t1")
    assert result is None


async def test_issue_agent_jwt_no_vault_returns_none():
    """Even with a valid key in DB, without vault we can't retrieve private key."""
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    row = (["goals:read"], "bounded-autonomous", "general", "vault://ref-1")
    result_mock = MagicMock()
    result_mock.fetchone.return_value = row
    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)

    svc = AgentIdentityService(db=db_factory, vault=None)
    result = await svc.issue_agent_jwt("a1", "kid-1", "t1")
    assert result is None


async def test_issue_agent_jwt_with_vault_returns_token():
    private_pem, _ = _gen_keypair()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    row = (["goals:read", "goals:execute"], "bounded-autonomous", "general", "vault://ref")
    result_mock = MagicMock()
    result_mock.fetchone.return_value = row
    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)

    vault_mock = AsyncMock()
    vault_mock.retrieve = AsyncMock(return_value=private_pem)

    svc = AgentIdentityService(db=db_factory, vault=vault_mock)
    token = await svc.issue_agent_jwt("agent-1", "kid-1", "t1")

    assert token is not None
    assert isinstance(token, str)


async def test_list_credentials_no_db_returns_empty():
    svc = AgentIdentityService(db=None)
    result = await svc.list_credentials("a1", "t1")
    assert result == []


async def test_list_credentials_with_db_returns_list():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    now = datetime.now(UTC)
    rows = [
        ("cred-id-1", "kid-1", "service_account", ["goals:read"],
         now + timedelta(days=90), None, None, "admin", {"description": "test"}, now),
    ]
    result_mock = MagicMock()
    result_mock.fetchall.return_value = rows
    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)

    svc = AgentIdentityService(db=db_factory)
    result = await svc.list_credentials("a1", "t1")

    assert len(result) == 1
    assert result[0]["key_id"] == "kid-1"
    assert result[0]["key_type"] == "service_account"
    assert result[0]["description"] == "test"
