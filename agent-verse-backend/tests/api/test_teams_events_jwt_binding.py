"""DEF-1: /channels/teams/events binds the tenant from the VERIFIED Bot Framework
token and activity — never from the shared regional ``serviceUrl``.

Real RS256 tokens are minted with a locally generated key and served through a
mocked Bot Framework JWKS, so the issuer / audience / signature / expiry /
``serviceurl`` / endorsement checks all run for real.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt
from jose.utils import long_to_base64

from app.api.channels.ingestion import router
from app.gateway.channels import teams as teams_module

_PLATFORM_APP = "platform-bot-app"
_TENANT_APP = "contoso-own-bot-app"
_SERVICE_URL = "https://smba.trafficmanager.net/amer/"
_ORG_A = "72f988bf-86f1-41af-91ab-2d7cd011db47"  # bound to tenant-a, platform bot
_ORG_B = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"  # bound to tenant-b, its own bot app
_ORG_UNBOUND = "11111111-2222-3333-4444-555555555555"


def _keypair(kid: str, endorsements: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    jwk: dict[str, Any] = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "n": long_to_base64(numbers.n).decode(),
        "e": long_to_base64(numbers.e).decode(),
    }
    if endorsements is not None:
        jwk["endorsements"] = endorsements
    return pem, jwk


_PEM, _JWK = _keypair("bf-key-1", endorsements=["msteams", "webchat"])
_ROTATED_PEM, _ROTATED_JWK = _keypair("bf-key-2", endorsements=["msteams"])
_ATTACKER_PEM, _ = _keypair("bf-key-1")


def _token(
    *,
    aud: str = _PLATFORM_APP,
    iss: str = teams_module._BOTFRAMEWORK_ISSUER,
    exp_delta: int = 3600,
    service_url: str | None = _SERVICE_URL,
    pem: str = _PEM,
    kid: str = "bf-key-1",
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"aud": aud, "iss": iss, "iat": now, "nbf": now,
                              "exp": now + exp_delta}
    if service_url is not None:
        claims["serviceurl"] = service_url
    return jose_jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def _activity(org: str | None, *, service_url: str = _SERVICE_URL,
              channel_id: str = "msteams") -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "message",
        "id": "1700000000000",
        "channelId": channel_id,
        "text": "deploy status?",
        "serviceUrl": service_url,
        "from": {"id": "29:user"},
        "conversation": {"id": "19:conv"},
    }
    if org is not None:
        body["channelData"] = {"tenant": {"id": org}}
    return body


def _mapping_db(fail: bool = False) -> Any:
    rows = {
        _ORG_A: ("tenant-a", {}),
        _ORG_B: ("tenant-b", {"gateway": True, "app_id": _TENANT_APP}),
    }
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=session)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    async def execute(query: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(query)
        if fail and "channel_tenant_mappings" in sql:
            raise RuntimeError("db down")
        result = MagicMock()
        row = rows.get((params or {}).get("ci", "")) if "channel_tenant_mappings" in sql else None
        result.fetchone = MagicMock(return_value=row)
        return result

    session.execute = AsyncMock(side_effect=execute)
    return lambda: session


@pytest.fixture
def jwks_fetch(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    teams_module._jwks_cache = {}
    teams_module._jwks_fetched_at = 0.0
    teams_module._jwks_forced_at = 0.0
    fetch = AsyncMock(return_value={"keys": [_JWK]})
    monkeypatch.setattr(teams_module, "_get_botframework_jwks", fetch)
    monkeypatch.setenv("TEAMS_APP_ID", _PLATFORM_APP)
    return fetch


def _client(*, fail_db: bool = False) -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    gateway = AsyncMock()
    app.state.channel_gateway = gateway
    app.state.trigger_event_redis = None
    app.state.system_db_session_factory = _mapping_db(fail=fail_db)
    return TestClient(app), gateway


def _post(client: TestClient, body: dict[str, Any], token: str | None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post("/channels/teams/events", json=body, headers=headers)


def test_platform_bot_activity_routes_to_the_orgs_bound_tenant(jwks_fetch: AsyncMock) -> None:
    client, gateway = _client()
    resp = _post(client, _activity(_ORG_A), _token())
    assert resp.status_code == 200, resp.text
    assert gateway.ingest.await_args.kwargs["tenant_id"] == "tenant-a"


def test_two_orgs_sharing_one_service_url_each_reach_only_their_tenant(
    jwks_fetch: AsyncMock,
) -> None:
    client, gateway = _client()
    assert _post(client, _activity(_ORG_A), _token()).status_code == 200
    assert _post(client, _activity(_ORG_B), _token()).status_code == 200
    assert [c.kwargs["tenant_id"] for c in gateway.ingest.await_args_list] == [
        "tenant-a",
        "tenant-b",
    ]


@pytest.mark.parametrize("org", [_ORG_UNBOUND, None])
def test_unknown_or_unbound_org_is_403(jwks_fetch: AsyncMock, org: str | None) -> None:
    client, gateway = _client()
    resp = _post(client, _activity(org), _token())
    assert resp.status_code == 403
    gateway.ingest.assert_not_called()


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(None, id="no-token"),
        pytest.param("not.a.jwt", id="garbage"),
        pytest.param(_token(iss="https://sts.windows.net/evil/"), id="wrong-issuer"),
        pytest.param(_token(aud="someone-elses-bot"), id="foreign-audience"),
        pytest.param(_token(exp_delta=-3600), id="expired"),
        pytest.param(_token(pem=_ATTACKER_PEM), id="forged-signature"),
        pytest.param(_token(service_url="https://smba.trafficmanager.net/emea/"),
                     id="serviceurl-claim-mismatch"),
        pytest.param(_token(service_url=None), id="serviceurl-claim-missing"),
    ],
)
def test_unverifiable_tokens_are_401(jwks_fetch: AsyncMock, token: str | None) -> None:
    client, gateway = _client()
    resp = _post(client, _activity(_ORG_A), token)
    assert resp.status_code == 401
    gateway.ingest.assert_not_called()


def test_signing_key_not_endorsed_for_the_channel_is_401(jwks_fetch: AsyncMock) -> None:
    client, gateway = _client()
    resp = _post(client, _activity(_ORG_A, channel_id="skype"), _token())
    assert resp.status_code == 401
    gateway.ingest.assert_not_called()


def test_tenants_own_bot_app_is_accepted_for_its_bound_org(jwks_fetch: AsyncMock) -> None:
    client, gateway = _client()
    resp = _post(client, _activity(_ORG_B), _token(aud=_TENANT_APP))
    assert resp.status_code == 200, resp.text
    assert gateway.ingest.await_args.kwargs["tenant_id"] == "tenant-b"


def test_tenants_own_bot_cannot_carry_another_orgs_activity(jwks_fetch: AsyncMock) -> None:
    """Org A is bound (to tenant-a) through the platform bot: an activity naming
    org A but signed for tenant-b's bot app is refused, not routed."""
    client, gateway = _client()
    resp = _post(client, _activity(_ORG_A), _token(aud=_TENANT_APP))
    assert resp.status_code == 401
    gateway.ingest.assert_not_called()


def test_rotated_signing_key_is_fetched_once(jwks_fetch: AsyncMock) -> None:
    jwks_fetch.side_effect = [{"keys": [_JWK]}, {"keys": [_JWK, _ROTATED_JWK]}]
    client, gateway = _client()
    resp = _post(client, _activity(_ORG_A), _token(pem=_ROTATED_PEM, kid="bf-key-2"))
    assert resp.status_code == 200, resp.text
    assert jwks_fetch.await_args_list[-1].kwargs == {"force": True}


def test_mapping_store_outage_is_503_not_unbound(jwks_fetch: AsyncMock) -> None:
    client, gateway = _client(fail_db=True)
    resp = _post(client, _activity(_ORG_A), _token())
    assert resp.status_code == 503
    gateway.ingest.assert_not_called()


@pytest.mark.asyncio
async def test_forced_jwks_refresh_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown key ids cannot turn every request into an outbound JWKS fetch."""
    import httpx

    calls: list[str] = []

    class _Resp:
        def __init__(self, data: dict[str, Any]) -> None:
            self._data = data

        def json(self) -> dict[str, Any]:
            return self._data

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            calls.append(url)
            if url == teams_module._BOTFRAMEWORK_OPENID_CONFIG:
                return _Resp({"jwks_uri": "https://login.botframework.com/keys"})
            return _Resp({"keys": [_JWK]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client())
    teams_module._jwks_cache = {}
    teams_module._jwks_fetched_at = 0.0
    teams_module._jwks_forced_at = 0.0
    await teams_module._get_botframework_jwks()
    for _ in range(5):
        await teams_module._get_botframework_jwks(force=True)
    # initial fetch + ONE forced refetch (2 GETs each)
    assert len(calls) == 4
