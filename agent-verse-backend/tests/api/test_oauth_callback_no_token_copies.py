"""SECRET-03: the OAuth callback keeps tokens only in oauth_tokens.

It also wrote encrypted access/refresh tokens into the connector's auth_config
(Redis/Postgres config), where nothing read them: a second, unrotated copy of
every credential.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.mcp.oauth import OAuthToken
from app.mcp.registry import MCPServerConfig
from tests.api.test_connectors_comprehensive2 import _CTX, _VALID_KEY, _make_app, _make_registry


def test_callback_leaves_no_token_copies_in_auth_config() -> None:
    reg = _make_registry()
    sid = asyncio.run(
        reg.register(
            MCPServerConfig(
                name="oauth thing",
                url="https://api.example.com",
                auth_type="oauth_ac",
                auth_config={
                    "token_url": "https://auth.example.com/token",
                    "client_id": "c",
                    # A copy written by the old callback: removed on reconnect.
                    "_encrypted_access_token": "gAAAA-old",
                    "_encrypted_refresh_token": "gAAAA-old-r",
                    "_token_scope": "repo",
                    "_token_type": "Bearer",
                },
            ),
            tenant_ctx=_CTX,
        )
    )
    app = _make_app(reg)
    manager = AsyncMock()
    manager.exchange_code.return_value = OAuthToken(
        access_token="at", refresh_token="rt", scope="repo"
    )
    app.state.oauth_manager = manager
    client = TestClient(app)

    resp = client.get(
        "/connectors/oauth/callback",
        params={"code": "c", "state": "s", "server_id": sid},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "connected"
    cfg = asyncio.run(reg.get(sid, tenant_ctx=_CTX))
    assert cfg is not None
    assert not [k for k in cfg.auth_config if k.startswith(("_encrypted_", "_token_"))]
    assert cfg.auth_config["token_url"] == "https://auth.example.com/token"
