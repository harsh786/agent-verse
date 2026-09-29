"""OAuth connector tokens are read through the durable store, not per-process memory.

Tokens were only in the memory of the API process that completed the OAuth flow:
the Celery worker's MCP client never had the OAuth manager, and another API
replica had no token, so worker-run goals sent no Bearer token. Refreshed tokens
were never persisted, the refresh request was not SSRF-guarded, and a token
whose access token expired while the service was down was dropped even when a
refresh token could renew it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from app.mcp.oauth import OAuthFlowManager, OAuthToken
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="oauth-dur-t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _Store:
    """Stands in for the oauth_tokens table (the manager's DB seam)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], tuple[str, str, datetime | None, str, str]] = {}
        self.reads = 0
        self.writes: list[tuple[str, str, OAuthToken]] = []

    async def read(self, tenant_id: str, server_id: str) -> Any:
        self.reads += 1
        return self.rows.get((tenant_id, server_id))

    async def write(self, tenant_id: str, server_id: str, token: OAuthToken) -> None:
        self.writes.append((tenant_id, server_id, token))
        self.rows[(tenant_id, server_id)] = (
            token.access_token,
            token.refresh_token,
            datetime.now(UTC) + timedelta(seconds=token.expires_in),
            token.token_type,
            token.scope,
        )


def _mgr(store: _Store) -> OAuthFlowManager:
    mgr = OAuthFlowManager()
    mgr._db_session_factory = object()  # durable mode on; DB access goes via the seams
    mgr._fetch_token_row = store.read  # type: ignore[method-assign]
    mgr._persist_token_to_db = store.write  # type: ignore[method-assign]
    return mgr


async def test_token_persisted_by_another_process_is_found() -> None:
    store = _Store()
    store.rows[("oauth-dur-t1", "srv")] = (
        "at-from-api",
        "rt",
        datetime.now(UTC) + timedelta(hours=1),
        "Bearer",
        "",
    )
    worker = _mgr(store)  # a fresh process: nothing in memory
    tok = await worker.aget_token("oauth-dur-t1", "srv")
    assert tok is not None and tok.access_token == "at-from-api"
    assert not tok.is_expired()


async def test_read_through_is_cached() -> None:
    store = _Store()
    store.rows[("oauth-dur-t1", "srv")] = (
        "at", "rt", datetime.now(UTC) + timedelta(hours=1), "Bearer", ""
    )
    mgr = _mgr(store)
    await mgr.aget_token("oauth-dur-t1", "srv")
    await mgr.aget_token("oauth-dur-t1", "srv")
    assert store.reads == 1


async def test_revoked_durable_token_is_not_served_from_stale_cache() -> None:
    store = _Store()
    mgr = _mgr(store)
    mgr._tokens[("oauth-dur-t1", "srv")] = OAuthToken(access_token="stale")
    # Cache entry is older than the TTL (never stamped) and the row is gone.
    assert await mgr.aget_token("oauth-dur-t1", "srv") is None


async def test_expired_access_token_with_refresh_token_is_kept() -> None:
    store = _Store()
    store.rows[("oauth-dur-t1", "srv")] = (
        "at-old", "rt-1", datetime.now(UTC) - timedelta(hours=5), "Bearer", ""
    )
    tok = await _mgr(store).aget_token("oauth-dur-t1", "srv")
    assert tok is not None and tok.is_expired() and tok.refresh_token == "rt-1"


async def test_refreshed_token_is_persisted() -> None:
    store = _Store()
    mgr = _mgr(store)
    old = OAuthToken(access_token="old", refresh_token="rt-1", expires_in=0, scope="read")
    mgr._tokens[("oauth-dur-t1", "srv")] = old
    with respx.mock:
        respx.post("http://auth.test/token").mock(
            return_value=httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        )
        new = await mgr.refresh_token(
            server_id="srv", token_url="http://auth.test/token", tenant_ctx=T, token=old
        )
    assert new is not None and new.access_token == "new"
    assert new.refresh_token == "rt-1" and new.scope == "read"
    assert [(t, s, tok.access_token) for t, s, tok in store.writes] == [
        ("oauth-dur-t1", "srv", "new")
    ]


async def test_refresh_uses_token_another_replica_already_refreshed() -> None:
    store = _Store()
    store.rows[("oauth-dur-t1", "srv")] = (
        "fresh-elsewhere", "rt-2", datetime.now(UTC) + timedelta(hours=1), "Bearer", ""
    )
    mgr = _mgr(store)
    old = OAuthToken(access_token="old", refresh_token="rt-1", expires_in=0)
    with respx.mock(assert_all_called=False) as router:
        route = router.post("http://auth.test/token").mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        tok = await mgr.refresh_token(
            server_id="srv", token_url="http://auth.test/token", tenant_ctx=T, token=old
        )
    assert tok is not None and tok.access_token == "fresh-elsewhere"
    assert not route.called


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/latest/meta-data", "http://127.0.0.1:8080/token", "http://10.0.0.5/t"],
)
async def test_refresh_request_is_ssrf_guarded(url: str) -> None:
    mgr = OAuthFlowManager()
    old = OAuthToken(access_token="old", refresh_token="rt", expires_in=0)
    mgr._tokens[("oauth-dur-t1", "srv")] = old
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url).mock(return_value=httpx.Response(200, json={"access_token": "x"}))
        tok = await mgr.refresh_token(server_id="srv", token_url=url, tenant_ctx=T, token=old)
    assert tok is None
    assert not route.called


async def test_load_tokens_keeps_expired_tokens_that_can_refresh() -> None:
    mgr = OAuthFlowManager()
    mgr._db_session_factory = object()
    past = datetime.now(UTC) - timedelta(hours=2)
    future = datetime.now(UTC) + timedelta(hours=2)

    async def _rows() -> list[tuple[Any, ...]]:
        return [
            ("t1", "refreshable", "at", "rt", past, "Bearer", ""),
            ("t1", "dead", "at", "", past, "Bearer", ""),
            ("t1", "live", "at", "", future, "Bearer", ""),
        ]

    mgr._fetch_all_token_rows = _rows  # type: ignore[method-assign]
    assert await mgr.load_tokens_from_db() == 2
    assert ("t1", "refreshable") in mgr._tokens
    assert mgr._tokens[("t1", "refreshable")].is_expired()
    assert ("t1", "dead") not in mgr._tokens
    assert ("t1", "live") in mgr._tokens


async def test_mcp_client_uses_durable_token_for_bearer_header() -> None:
    from unittest.mock import MagicMock

    from app.mcp.client import MCPClient
    from app.mcp.registry import MCPServerConfig

    store = _Store()
    store.rows[("oauth-dur-t1", "srv")] = (
        "durable-at", "rt", datetime.now(UTC) + timedelta(hours=1), "Bearer", ""
    )
    client = MCPClient(registry=MagicMock())
    client._oauth_manager = _mgr(store)
    cfg = MCPServerConfig(name="Jira", url="https://jira.example.com", auth_type="oauth_ac")
    headers = await client._build_auth_headers(cfg, tenant_ctx=T, server_id="srv")
    assert headers["Authorization"] == "Bearer durable-at"


def test_worker_mcp_context_wires_oauth_manager() -> None:
    """The worker builds its own MCPClient; it must carry a DB-backed OAuth manager."""
    import inspect

    from app.scaling import tasks

    src = inspect.getsource(tasks)
    start = src.index("async def _build_worker_mcp_context")
    body = src[start : start + 6000]
    assert "build_worker_oauth_manager(" in body
    assert "_oauth_manager =" in body


def test_build_worker_oauth_manager_is_db_backed() -> None:
    from app.mcp.oauth import build_worker_oauth_manager

    factory = object()
    mgr = build_worker_oauth_manager(factory)
    assert isinstance(mgr, OAuthFlowManager)
    assert mgr._db_session_factory is factory
    assert build_worker_oauth_manager(None) is None
