"""Tests for app/gateway/channels/teams.py — MicrosoftTeamsAdapter.verify_auth.

Regression coverage for a real, severe bug: the previous implementation
accepted ANY Authorization header shaped like "Bearer <20+ chars>" with no
signature, issuer, or audience validation at all — an attacker who knew (or
guessed) an org_id could forge a Teams webhook activity with a fake token.
The fix does real RS256 JWT validation against Bot Framework's published
JWKS (mocked here via a locally generated RSA keypair), matching the
issuer/audience the real Bot Framework connector uses.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from jose import jwt as jose_jwt
from jose.utils import long_to_base64

from app.gateway.channels import teams as teams_module
from app.gateway.channels.teams import (
    _BOTFRAMEWORK_ISSUER,
    MicrosoftTeamsAdapter,
)

_APP_ID = "test-teams-app-id"
_KID = "test-key-1"


def _generate_rsa_keypair() -> tuple[str, dict]:
    """Generate an RSA keypair and return (private_pem, jwks_dict)."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    from cryptography.hazmat.primitives import serialization

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": _KID,
                "use": "sig",
                "alg": "RS256",
                "n": long_to_base64(public_numbers.n).decode(),
                "e": long_to_base64(public_numbers.e).decode(),
            }
        ]
    }
    return private_pem, jwks


def _sign_token(private_pem: str, *, audience: str, issuer: str, exp_delta: int = 3600) -> str:
    now = int(time.time())
    claims = {
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + exp_delta,
        "serviceurl": "https://smba.trafficmanager.net/amer/",
    }
    return jose_jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": _KID})


@pytest.fixture()
def keypair():
    return _generate_rsa_keypair()


@pytest.fixture(autouse=True)
def _reset_jwks_cache():
    """Each test controls its own mocked JWKS fetch; don't let one test's
    cached keys leak into another's assertions."""
    teams_module._jwks_cache = {}
    teams_module._jwks_fetched_at = 0.0
    yield
    teams_module._jwks_cache = {}
    teams_module._jwks_fetched_at = 0.0


def _adapter() -> MicrosoftTeamsAdapter:
    adapter = MicrosoftTeamsAdapter()
    adapter._app_id = _APP_ID
    return adapter


@pytest.mark.asyncio
async def test_rejects_fake_token_shaped_like_a_bearer_token(keypair):
    """The exact old vulnerability: a string that merely looks like a bearer
    token, with no real signature at all, must be rejected."""
    adapter = _adapter()
    ok = await adapter.verify_auth(
        {"authorization": "Bearer a-fairly-long-fake-jwt-token-that-is-not-real"},
        {},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_rejects_missing_authorization_header():
    adapter = _adapter()
    ok = await adapter.verify_auth({}, {})
    assert ok is False


@pytest.mark.asyncio
async def test_rejects_non_bearer_authorization_header():
    adapter = _adapter()
    ok = await adapter.verify_auth({"authorization": "Basic dXNlcjpwYXNz"}, {})
    assert ok is False


@pytest.mark.asyncio
async def test_accepts_genuinely_valid_token(keypair):
    private_pem, jwks = keypair
    token = _sign_token(private_pem, audience=_APP_ID, issuer=_BOTFRAMEWORK_ISSUER)
    adapter = _adapter()
    with patch.object(teams_module, "_get_botframework_jwks", AsyncMock(return_value=jwks)):
        ok = await adapter.verify_auth({"authorization": f"Bearer {token}"}, {})
    assert ok is True


@pytest.mark.asyncio
async def test_rejects_token_with_wrong_audience(keypair):
    """A token genuinely signed by Bot Framework, but for a DIFFERENT app
    (wrong audience) — must not be accepted for this bot."""
    private_pem, jwks = keypair
    token = _sign_token(private_pem, audience="someone-elses-app-id", issuer=_BOTFRAMEWORK_ISSUER)
    adapter = _adapter()
    with patch.object(teams_module, "_get_botframework_jwks", AsyncMock(return_value=jwks)):
        ok = await adapter.verify_auth({"authorization": f"Bearer {token}"}, {})
    assert ok is False


@pytest.mark.asyncio
async def test_rejects_token_with_wrong_issuer(keypair):
    private_pem, jwks = keypair
    token = _sign_token(private_pem, audience=_APP_ID, issuer="https://not-bot-framework.example.com")
    adapter = _adapter()
    with patch.object(teams_module, "_get_botframework_jwks", AsyncMock(return_value=jwks)):
        ok = await adapter.verify_auth({"authorization": f"Bearer {token}"}, {})
    assert ok is False


@pytest.mark.asyncio
async def test_rejects_expired_token(keypair):
    private_pem, jwks = keypair
    token = _sign_token(
        private_pem, audience=_APP_ID, issuer=_BOTFRAMEWORK_ISSUER, exp_delta=-3600
    )
    adapter = _adapter()
    with patch.object(teams_module, "_get_botframework_jwks", AsyncMock(return_value=jwks)):
        ok = await adapter.verify_auth({"authorization": f"Bearer {token}"}, {})
    assert ok is False


@pytest.mark.asyncio
async def test_rejects_token_signed_by_a_different_key(keypair):
    """A structurally valid, correctly-claimed JWT, but signed with a key
    that ISN'T in Bot Framework's published JWKS -- the actual signature
    check, not just claim inspection, must be exercised."""
    private_pem, _real_jwks = keypair
    _other_private_pem, attacker_jwks = _generate_rsa_keypair()
    token = _sign_token(private_pem, audience=_APP_ID, issuer=_BOTFRAMEWORK_ISSUER)
    adapter = _adapter()
    # Server only knows about the attacker's (wrong) key -- signature verification must fail.
    with patch.object(
        teams_module, "_get_botframework_jwks", AsyncMock(return_value=attacker_jwks)
    ):
        ok = await adapter.verify_auth({"authorization": f"Bearer {token}"}, {})
    assert ok is False


@pytest.mark.asyncio
async def test_fails_closed_when_app_id_not_configured(keypair):
    """No TEAMS_APP_ID configured -- must fail closed (reject), not silently
    skip audience validation."""
    private_pem, jwks = keypair
    token = _sign_token(private_pem, audience=_APP_ID, issuer=_BOTFRAMEWORK_ISSUER)
    adapter = MicrosoftTeamsAdapter()
    adapter._app_id = ""
    with patch.object(teams_module, "_get_botframework_jwks", AsyncMock(return_value=jwks)):
        ok = await adapter.verify_auth({"authorization": f"Bearer {token}"}, {})
    assert ok is False


@pytest.mark.asyncio
async def test_fails_closed_on_jwks_fetch_failure(keypair):
    private_pem, _jwks = keypair
    token = _sign_token(private_pem, audience=_APP_ID, issuer=_BOTFRAMEWORK_ISSUER)
    adapter = _adapter()
    with patch.object(
        teams_module, "_get_botframework_jwks", AsyncMock(side_effect=RuntimeError("network down"))
    ):
        ok = await adapter.verify_auth({"authorization": f"Bearer {token}"}, {})
    assert ok is False
