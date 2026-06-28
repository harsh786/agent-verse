"""Agent Identity Service — cryptographic service-account credentials for agents.

Provides:
- RSA-2048 keypair generation (generate_agent_keypair)
- RS256 JWT issuance (issue_agent_token) and verification (verify_agent_token)
- NIST-compliant API key generation (generate_api_key)
- JWKS endpoint data builder (_build_jwks, Redis-cached 10 min)
- AgentIdentityService — full credential lifecycle in agent_credentials table
"""

from __future__ import annotations

import base64
import contextlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

JWT_ALGORITHM = "RS256"
JWT_EXPIRY_MINUTES = 15


# ---------------------------------------------------------------------------
# Pure crypto helpers
# ---------------------------------------------------------------------------


def generate_agent_keypair() -> tuple[str, str]:
    """Generate RSA-2048 keypair for agent JWT signing.

    Returns:
        (private_pem, public_pem) — PEM-encoded strings.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def generate_api_key() -> str:
    """Generate a NIST-compliant API key (256 bits from os.urandom).

    Returns a URL-safe base64 string prefixed with 'av_'.  Total length ≥ 44 chars.
    """
    return f"av_{secrets.token_urlsafe(32)}"


def issue_agent_token(
    agent_id: str,
    tenant_id: str,
    key_id: str,
    private_key_pem: str,
    scopes: list[str],
    autonomy_mode: str,
    domain_context: str = "general",
    parent_goal_id: str | None = None,
    delegated_by: str | None = None,
    expiry_minutes: int = JWT_EXPIRY_MINUTES,
) -> str:
    """Issue an RS256-signed JWT for an agent service account.

    The token is valid for *expiry_minutes* (default 15).  The 'kid' header is set
    to *key_id* so verifiers can fetch the right public key from the JWKS endpoint.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": f"agentverse:{tenant_id}",
        "sub": f"agent:{agent_id}",
        "aud": ["agentverse-api", "mcp-tools"],
        "exp": int((now + timedelta(minutes=expiry_minutes)).timestamp()),
        "iat": int(now.timestamp()),
        "jti": uuid.uuid4().hex,
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "autonomy_mode": autonomy_mode,
        "scopes": scopes,
        "domain_context": domain_context,
    }
    if parent_goal_id:
        payload["parent_goal_id"] = parent_goal_id
    if delegated_by:
        payload["delegated_by"] = delegated_by
    return jwt.encode(
        payload,
        private_key_pem,
        algorithm=JWT_ALGORITHM,
        headers={"kid": key_id},
    )


def verify_agent_token(token: str, public_key_pem: str, tenant_id: str) -> dict[str, Any]:
    """Verify an agent JWT.  Raises JWTError or ValueError on failure.

    Per Amendment 1.2: audience and issuer are validated manually after decode to
    handle list-audience tokens correctly (python-jose won't accept list aud by
    default when a single string is passed to the audience param).
    """
    payload: dict[str, Any] = jwt.decode(
        token,
        public_key_pem,
        algorithms=[JWT_ALGORITHM],
        options={"verify_aud": False},
    )
    aud = payload.get("aud") or []
    if isinstance(aud, str):
        aud = [aud]
    if "agentverse-api" not in aud:
        raise ValueError("Invalid audience")
    if payload.get("iss") != f"agentverse:{tenant_id}":
        raise ValueError("Invalid issuer")
    return payload


# ---------------------------------------------------------------------------
# JWKS builder (used by /.well-known/jwks.json endpoint)
# ---------------------------------------------------------------------------


async def _build_jwks(db_factory: Any) -> list[dict[str, Any]]:
    """Build JWKS payload from all active agent credentials with RSA public keys.

    Queries up to 500 active (non-revoked, non-expired) credentials and converts
    each RSA public key to JWK (RFC 7517) format.
    """
    from sqlalchemy import text as _t

    keys: list[dict[str, Any]] = []
    try:
        async with db_factory() as session:
            rows = (
                await session.execute(
                    _t("""
                        SELECT key_id, public_key FROM agent_credentials
                        WHERE revoked_at IS NULL
                          AND (expires_at IS NULL OR expires_at > NOW())
                          AND public_key IS NOT NULL
                        LIMIT 500
                    """)
                )
            ).fetchall()

            for key_id, public_pem in rows:
                from cryptography.hazmat.primitives.asymmetric.rsa import (
                    RSAPublicKey as _RSAPublicKey,
                )
                from cryptography.hazmat.primitives.serialization import load_pem_public_key

                pub_key = load_pem_public_key(public_pem.encode())
                if not isinstance(pub_key, _RSAPublicKey):
                    continue
                pub_numbers = pub_key.public_numbers()

                def _to_base64url(n: int) -> str:
                    length = (n.bit_length() + 7) // 8
                    return base64.urlsafe_b64encode(
                        n.to_bytes(length, "big")
                    ).rstrip(b"=").decode()

                keys.append(
                    {
                        "kty": "RSA",
                        "use": "sig",
                        "alg": "RS256",
                        "kid": key_id,
                        "n": _to_base64url(pub_numbers.n),
                        "e": _to_base64url(pub_numbers.e),
                    }
                )
    except Exception:
        pass
    return keys


# ---------------------------------------------------------------------------
# AgentIdentityService
# ---------------------------------------------------------------------------


class AgentIdentityService:
    """Service for managing agent cryptographic credentials (service-account keys + JWTs).

    Lifecycle:
        __init__(db=None, vault=None, redis=None)   # wired with in-memory stubs in create_app
        set_db(db_factory)                           # upgraded in lifespan with real pool
        set_redis(redis_client)                      # upgraded in lifespan with real Redis
    """

    def __init__(
        self,
        db: Any = None,
        vault: Any = None,
        redis: Any = None,
    ) -> None:
        self._db = db
        self._vault = vault
        self._redis = redis

    def set_db(self, db_factory: Any) -> None:
        """Upgrade the DB session factory (called during lifespan startup)."""
        self._db = db_factory

    def set_redis(self, redis_client: Any) -> None:
        """Upgrade the Redis client (called during lifespan startup)."""
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Credential lifecycle
    # ------------------------------------------------------------------

    async def issue_credential(
        self,
        agent_id: str,
        tenant_id: str,
        created_by: str,
        scopes: list[str],
        key_type: str = "service_account",
        expires_in_days: int | None = 90,
        description: str = "",
    ) -> dict[str, Any]:
        """Generate an RSA keypair and store the public key in DB.

        The private key is returned ONCE in the response and is never stored in DB.
        If a vault is configured the private key is stored there instead.

        Returns a dict with keys: key_id, private_key_pem, public_key_pem, scopes,
        expires_at, warning.
        """
        from sqlalchemy import text as _t

        private_pem, public_pem = generate_agent_keypair()
        key_id = f"kid_{secrets.token_hex(12)}"
        credential_id = str(uuid.uuid4())
        expires_at: datetime | None = None
        if expires_in_days is not None:
            expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

        vault_ref: str | None = None
        if self._vault is not None:
            with contextlib.suppress(Exception):
                vault_ref = await self._vault.store(
                    f"agent_key:{credential_id}", private_pem
                )

        if self._db is not None:
            async with self._db() as session:
                await session.execute(
                    _t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
                )
                await session.execute(
                    _t("""
                        INSERT INTO agent_credentials
                        (id, agent_id, tenant_id, key_type, key_id, public_key,
                         private_key_ref, scopes, expires_at, created_by, metadata)
                        VALUES (:id, :agent, :tenant, :ktype, :kid, :pub,
                                :vault, :scopes, :exp, :by, :meta::jsonb)
                    """),
                    {
                        "id": credential_id,
                        "agent": agent_id,
                        "tenant": tenant_id,
                        "ktype": key_type,
                        "kid": key_id,
                        "pub": public_pem,
                        "vault": vault_ref,
                        "scopes": scopes,
                        "exp": expires_at,
                        "by": created_by,
                        "meta": json.dumps({"description": description}),
                    },
                )
                await session.commit()

        if self._redis is not None:
            await self._redis.delete("jwks:cache")

        return {
            "key_id": key_id,
            "private_key_pem": private_pem,
            "public_key_pem": public_pem,
            "scopes": scopes,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "warning": "Private key shown ONCE — save it immediately and securely.",
        }

    async def revoke_credential(self, key_id: str, tenant_id: str) -> bool:
        """Revoke a credential by key_id.

        Returns True if the credential was found and revoked, False otherwise.
        Publishes to Redis channel 'jwks_invalidated' so JWKS caches are cleared.
        """
        if self._db is None:
            return False

        from sqlalchemy import text as _t

        async with self._db() as session:
            await session.execute(
                _t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            result = await session.execute(
                _t("""
                    UPDATE agent_credentials SET revoked_at = NOW()
                    WHERE key_id = :kid AND tenant_id = :tid AND revoked_at IS NULL
                """),
                {"kid": key_id, "tid": tenant_id},
            )
            await session.commit()
            revoked = result.rowcount > 0

        if revoked and self._redis is not None:
            await self._redis.delete("jwks:cache")
            await self._redis.publish("jwks_invalidated", key_id)

        return revoked

    async def issue_agent_jwt(
        self, agent_id: str, key_id: str, tenant_id: str
    ) -> str | None:
        """Exchange a service key for a short-lived RS256 JWT.

        Returns the signed JWT string, or None if the key is not found / revoked /
        expired or if the private key cannot be retrieved from the vault.
        """
        if self._db is None:
            return None

        from sqlalchemy import text as _t

        async with self._db() as session:
            await session.execute(
                _t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            row = (
                await session.execute(
                    _t("""
                        SELECT ac.scopes, a.autonomy_mode, a.domain_context,
                               ac.private_key_ref
                        FROM agent_credentials ac
                        JOIN agents a ON a.id = ac.agent_id
                        WHERE ac.key_id = :kid AND ac.tenant_id = :tid
                          AND ac.revoked_at IS NULL
                          AND (ac.expires_at IS NULL OR ac.expires_at > NOW())
                    """),
                    {"kid": key_id, "tid": tenant_id},
                )
            ).fetchone()

        if not row:
            return None

        scopes, autonomy_mode, domain_context, vault_ref = row

        private_pem: str | None = None
        if self._vault is not None and vault_ref is not None:
            private_pem = await self._vault.retrieve(vault_ref)
        if not private_pem:
            return None

        return issue_agent_token(
            agent_id=agent_id,
            tenant_id=tenant_id,
            key_id=key_id,
            private_key_pem=private_pem,
            scopes=list(scopes or []),
            autonomy_mode=autonomy_mode or "bounded-autonomous",
            domain_context=domain_context or "general",
        )

    async def list_credentials(
        self, agent_id: str, tenant_id: str
    ) -> list[dict[str, Any]]:
        """List all credentials for an agent. Private keys are never returned."""
        if self._db is None:
            return []

        from sqlalchemy import text as _t

        async with self._db() as session:
            await session.execute(
                _t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            rows = (
                await session.execute(
                    _t("""
                        SELECT id, key_id, key_type, scopes, expires_at,
                               revoked_at, last_used_at, created_by, metadata, created_at
                        FROM agent_credentials
                        WHERE agent_id = :aid AND tenant_id = :tid
                        ORDER BY created_at DESC
                    """),
                    {"aid": agent_id, "tid": tenant_id},
                )
            ).fetchall()

        return [
            {
                "id": r[0],
                "agent_id": agent_id,
                "key_id": r[1],
                "key_type": r[2],
                "scopes": list(r[3] or []),
                "expires_at": r[4].isoformat() if r[4] else None,
                "revoked_at": r[5].isoformat() if r[5] else None,
                "last_used_at": r[6].isoformat() if r[6] else None,
                "created_by": r[7],
                "created_at": r[9].isoformat() if r[9] else "",
                "description": (r[8] or {}).get("description", "") if r[8] else "",
            }
            for r in rows
        ]
