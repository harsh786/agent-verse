"""Agent identity: private keys sealed with the real vault; agent JWTs authenticate.

``CredentialVault`` has only encrypt/decrypt, so ``vault.store`` raised (and was
suppressed) and ``vault.retrieve`` never existed: every credential was saved
without a private-key ref and the token exchange always 404'd. And no auth code
ever verified an agent JWT, so an issued token authenticated nothing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.auth.agent_identity import (
    AgentIdentityService,
    AgentPrincipal,
    generate_agent_keypair,
    issue_agent_token,
)
from app.providers.vault import CredentialVault
from app.tenancy.context import PlanTier, TenantContext


def _vault() -> CredentialVault:
    return CredentialVault("unit-test-master-key-0123456789abcdef")


def _capturing_db() -> tuple[MagicMock, list[dict[str, Any]]]:
    params: list[dict[str, Any]] = []
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    async def _exec(stmt: Any, p: dict[str, Any] | None = None) -> Any:
        params.append(dict(p or {}))
        return MagicMock()

    session.execute = AsyncMock(side_effect=_exec)
    session.commit = AsyncMock()
    return MagicMock(return_value=session), params


async def test_issue_credential_seals_private_key_with_vault() -> None:
    db, params = _capturing_db()
    vault = _vault()
    svc = AgentIdentityService(db=db, vault=vault)
    result = await svc.issue_credential(
        agent_id="a1", tenant_id="t1", created_by="k", scopes=["goals:read"]
    )
    insert = next(p for p in params if "vault" in p)
    ref = insert["vault"]
    assert ref and result["private_key_pem"] not in ref  # never stored in the clear
    assert svc._unseal_private_key(ref) == result["private_key_pem"]


async def test_issue_credential_without_vault_fails_closed() -> None:
    db, _ = _capturing_db()
    svc = AgentIdentityService(db=db, vault=None)
    with pytest.raises(RuntimeError, match="vault"):
        await svc.issue_credential(agent_id="a1", tenant_id="t1", created_by="k", scopes=[])


async def test_issue_credential_without_db_fails_closed() -> None:
    svc = AgentIdentityService(db=None, vault=_vault())
    with pytest.raises(RuntimeError, match="store"):
        await svc.issue_credential(agent_id="a1", tenant_id="t1", created_by="k", scopes=[])


async def test_token_exchange_round_trip_with_real_vault() -> None:
    vault = _vault()
    private_pem, public_pem = generate_agent_keypair()
    svc = AgentIdentityService(db=None, vault=vault)
    ref = svc._seal_private_key(private_pem)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    seen: list[dict[str, Any]] = []

    async def _exec(stmt: Any, p: dict[str, Any] | None = None) -> Any:
        seen.append(dict(p or {}))
        r = MagicMock()
        r.fetchone.return_value = (["goals:read"], "bounded-autonomous", "general", ref)
        return r

    session.execute = AsyncMock(side_effect=_exec)
    svc.set_db(MagicMock(return_value=session))
    token = await svc.issue_agent_jwt("agent-1", "kid-1", "t1")
    assert token is not None
    # The credential lookup is bound to the requested agent, not just the key id.
    assert any(p.get("aid") == "agent-1" for p in seen)

    from app.auth.agent_identity import verify_agent_token

    assert verify_agent_token(token, public_pem, "t1")["agent_id"] == "agent-1"


async def test_undecryptable_ref_yields_no_token() -> None:
    svc = AgentIdentityService(db=None, vault=_vault())
    assert svc._unseal_private_key("vault://legacy-ref") is None
    other = CredentialVault("a-different-master-key-9876543210fedcba")
    foreign = AgentIdentityService(vault=other)._seal_private_key("pem")
    assert svc._unseal_private_key(foreign) is None


# ── Agent JWT verification ────────────────────────────────────────────────────


def _svc_with_key(
    public_pem: str, *, agent_id: str = "agent-1", scopes: list[str] | None = None
) -> AgentIdentityService:
    svc = AgentIdentityService(db=object(), vault=_vault())
    rows = {("kid-1", "t1"): (agent_id, public_pem, scopes or ["goals:read", "goals:write"])}

    async def _fetch(key_id: str, tenant_id: str) -> Any:
        return rows.get((key_id, tenant_id))

    svc._fetch_verification_key = _fetch  # type: ignore[method-assign]
    return svc


def _token(private_pem: str, **overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "agent_id": "agent-1",
        "tenant_id": "t1",
        "key_id": "kid-1",
        "private_key_pem": private_pem,
        "scopes": ["goals:read"],
        "autonomy_mode": "bounded-autonomous",
    }
    kwargs.update(overrides)
    return issue_agent_token(**kwargs)


async def test_authenticate_valid_agent_jwt() -> None:
    priv, pub = generate_agent_keypair()
    p = await _svc_with_key(pub).authenticate_agent_jwt(_token(priv))
    assert p == AgentPrincipal(
        agent_id="agent-1", tenant_id="t1", key_id="kid-1", scopes=("goals:read",)
    )


async def test_token_scopes_are_capped_by_the_credential() -> None:
    priv, pub = generate_agent_keypair()
    tok = _token(priv, scopes=["goals:read", "tenancy:write"])
    p = await _svc_with_key(pub, scopes=["goals:read"]).authenticate_agent_jwt(tok)
    assert p is not None and p.scopes == ("goals:read",)


async def test_jwt_signed_by_another_key_is_rejected() -> None:
    priv_other, _ = generate_agent_keypair()
    _, pub = generate_agent_keypair()
    assert await _svc_with_key(pub).authenticate_agent_jwt(_token(priv_other)) is None


async def test_revoked_or_unknown_kid_is_rejected() -> None:
    priv, pub = generate_agent_keypair()
    assert await _svc_with_key(pub).authenticate_agent_jwt(_token(priv, key_id="kid-x")) is None


async def test_jwt_for_another_tenant_cannot_use_this_tenants_key() -> None:
    priv, pub = generate_agent_keypair()
    assert await _svc_with_key(pub).authenticate_agent_jwt(_token(priv, tenant_id="t2")) is None


async def test_jwt_naming_a_different_agent_is_rejected() -> None:
    priv, pub = generate_agent_keypair()
    tok = _token(priv, agent_id="agent-2")
    assert await _svc_with_key(pub, agent_id="agent-1").authenticate_agent_jwt(tok) is None


async def test_expired_jwt_is_rejected() -> None:
    priv, pub = generate_agent_keypair()
    tok = _token(priv, expiry_minutes=-1)
    assert await _svc_with_key(pub).authenticate_agent_jwt(tok) is None


# ── TenantMiddleware integration ──────────────────────────────────────────────


def _app(svc: AgentIdentityService) -> FastAPI:
    from app.tenancy.middleware import TenantMiddleware

    async def _no_keys(_raw: str) -> TenantContext | None:
        return None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_no_keys)
    app.state.agent_identity_service = svc
    tenant_service = MagicMock()
    tenant_service.get_tenant = AsyncMock(return_value={"tenant_id": "t1", "plan": "enterprise"})
    app.state.tenant_service = tenant_service

    @app.get("/goals")
    async def whoami(request: Request) -> dict[str, Any]:
        t = request.state.tenant
        return {
            "tenant": t.tenant_id,
            "key": t.api_key_id,
            "roles": list(t.roles),
            "scopes": list(t.scopes),
            "plan": str(t.plan),
        }

    return app


def test_middleware_authenticates_agent_jwt_with_agent_scopes() -> None:
    priv, pub = generate_agent_keypair()
    client = TestClient(_app(_svc_with_key(pub)))
    r = client.get("/goals", headers={"Authorization": f"Bearer {_token(priv)}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant"] == "t1"
    assert body["key"] == "agent:kid-1"
    assert body["roles"] == ["agent"]
    assert body["scopes"] == ["goals:read"]
    assert body["plan"] == PlanTier.ENTERPRISE.value


def test_middleware_rejects_forged_agent_jwt() -> None:
    priv_other, _ = generate_agent_keypair()
    _, pub = generate_agent_keypair()
    client = TestClient(_app(_svc_with_key(pub)))
    r = client.get("/goals", headers={"Authorization": f"Bearer {_token(priv_other)}"})
    assert r.status_code == 401


def test_agent_role_scopes_exclude_tenant_administration() -> None:
    from app.auth.scope_enforcement import ROLE_SCOPES

    agent = ROLE_SCOPES["agent"]
    assert "goals:execute" in agent
    assert not {"tenancy:write", "costs:admin", "governance:approve", "agents:write"} & agent
