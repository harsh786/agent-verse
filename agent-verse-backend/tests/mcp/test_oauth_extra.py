"""Extra coverage for app/mcp/oauth.py.

Targets uncovered lines: 77-78, 108, 139-142, 240-278, 287-328.
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.mcp.oauth import OAuthFlowManager, OAuthState, OAuthToken
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="t-oauth-extra", plan=PlanTier.PROFESSIONAL, api_key_id="ok1")


# ── OAuthState helpers ────────────────────────────────────────────────────────

class TestOAuthState:
    def test_code_challenge_is_base64url_sha256(self):
        """OAuthState.code_challenge produces correct S256 value."""
        import base64
        import hashlib
        state = OAuthState(server_id="srv1")
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(state.code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert state.code_challenge == expected

    def test_state_token_is_unique(self):
        s1 = OAuthState(server_id="s")
        s2 = OAuthState(server_id="s")
        assert s1.state_token != s2.state_token

    def test_code_verifier_is_unique(self):
        s1 = OAuthState(server_id="s")
        s2 = OAuthState(server_id="s")
        assert s1.code_verifier != s2.code_verifier


# ── OAuthToken.is_expired ─────────────────────────────────────────────────────

class TestOAuthToken:
    def test_not_expired_fresh_token(self):
        token = OAuthToken(access_token="tok", expires_in=3600)
        assert not token.is_expired()

    def test_expired_old_token(self):
        token = OAuthToken(access_token="tok", expires_in=0, obtained_at=0)
        assert token.is_expired()


# ── get_pending_flow — expired branch ────────────────────────────────────────

class TestGetPendingFlowExpired:
    def test_expired_flow_removed_on_cleanup(self):
        """Lines 66-68: cleanup removes flows older than 10 minutes."""
        manager = OAuthFlowManager()
        state = OAuthState(server_id="s1")
        state.created_at = time.time() - 700  # > 600 seconds old
        manager._pending_flows[state.state_token] = state

        # Trigger cleanup by calling get_pending_flow on any key
        result = manager.get_pending_flow("nonexistent")
        assert result is None
        # The expired flow should have been swept
        assert state.state_token not in manager._pending_flows

    def test_expired_flow_returns_none_directly(self):
        """Lines 77-78: flow present but expired → deleted and None returned."""
        manager = OAuthFlowManager()
        state = OAuthState(server_id="s2")
        state.created_at = time.time() - 700
        manager._pending_flows[state.state_token] = state

        result = manager.get_pending_flow(state.state_token)
        assert result is None
        assert state.state_token not in manager._pending_flows

    def test_valid_flow_returned(self):
        """Fresh flow is returned without deletion."""
        manager = OAuthFlowManager()
        state = OAuthState(server_id="s3")
        manager._pending_flows[state.state_token] = state

        result = manager.get_pending_flow(state.state_token)
        assert result is not None
        assert result.server_id == "s3"


# ── exchange_code — error paths ───────────────────────────────────────────────

class TestExchangeCodeErrors:
    @pytest.mark.asyncio
    async def test_exchange_code_state_expired_returns_none(self):
        """Lines 104-105: get_pending_flow returns None → None returned."""
        manager = OAuthFlowManager()
        # Don't start any flow; state won't be found
        result = await manager.exchange_code(
            code="code123",
            state="bogus-state",
            token_url="http://auth.example.com/token",
            client_id="cid",
            redirect_uri="http://localhost/cb",
            tenant_ctx=TENANT,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exchange_code_http_status_error(self):
        """Lines 125-132: HTTPStatusError → None."""
        manager = OAuthFlowManager()
        params = manager.start_flow(server_id="github", tenant_ctx=TENANT)
        state = params["state"]

        with respx.mock:
            respx.post("http://auth.example.com/token").mock(
                return_value=httpx.Response(401, text="Unauthorized")
            )
            result = await manager.exchange_code(
                code="bad-code",
                state=state,
                token_url="http://auth.example.com/token",
                client_id="cid",
                redirect_uri="http://localhost/cb",
                tenant_ctx=TENANT,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_exchange_code_connect_error(self):
        """Lines 133-138: ConnectError → None."""
        manager = OAuthFlowManager()
        params = manager.start_flow(server_id="github", tenant_ctx=TENANT)
        state = params["state"]

        with respx.mock:
            respx.post("http://unreachable.local/token").mock(
                side_effect=httpx.ConnectError("unreachable")
            )
            result = await manager.exchange_code(
                code="any",
                state=state,
                token_url="http://unreachable.local/token",
                client_id="cid",
                redirect_uri="http://localhost/cb",
                tenant_ctx=TENANT,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_exchange_code_empty_access_token_returns_none(self):
        """Lines 152-154: empty access_token → None."""
        manager = OAuthFlowManager()
        params = manager.start_flow(server_id="github", tenant_ctx=TENANT)
        state = params["state"]

        with respx.mock:
            respx.post("http://auth.example.com/token").mock(
                return_value=httpx.Response(200, json={"access_token": ""})
            )
            result = await manager.exchange_code(
                code="code",
                state=state,
                token_url="http://auth.example.com/token",
                client_id="cid",
                redirect_uri="http://localhost/cb",
                tenant_ctx=TENANT,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_exchange_code_unexpected_exception(self):
        """Lines 139-142: unexpected exception → None."""
        manager = OAuthFlowManager()
        params = manager.start_flow(server_id="svc", tenant_ctx=TENANT)
        state = params["state"]

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=ValueError("weird error"))
            mock_cls.return_value = mock_client

            result = await manager.exchange_code(
                code="c", state=state,
                token_url="http://auth.example.com/token",
                client_id="cid", redirect_uri="http://localhost/cb",
                tenant_ctx=TENANT,
            )
        assert result is None


# ── get_token — positional call ───────────────────────────────────────────────

class TestGetTokenPositional:
    def test_get_token_positional_args(self):
        """Lines 168-172: positional call style."""
        manager = OAuthFlowManager()
        token = OAuthToken(access_token="tok-pos", expires_in=3600)
        manager._tokens[("t1", "github")] = token

        result = manager.get_token("t1", "github")
        assert result is not None
        assert result.access_token == "tok-pos"

    def test_get_token_keyword_args(self):
        """Lines 173-177: keyword call style."""
        manager = OAuthFlowManager()
        token = OAuthToken(access_token="tok-kw", expires_in=3600)
        manager._tokens[("t-oauth-extra", "gitlab")] = token

        result = manager.get_token(server_id="gitlab", tenant_ctx=TENANT)
        assert result is not None
        assert result.access_token == "tok-kw"

    def test_get_token_missing_returns_none(self):
        manager = OAuthFlowManager()
        result = manager.get_token("unknown-tenant", "github")
        assert result is None


# ── refresh_token ─────────────────────────────────────────────────────────────

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_refresh_no_existing_token_returns_none(self):
        """Lines 197-198: no existing token → None."""
        manager = OAuthFlowManager()
        result = await manager.refresh_token(
            server_id="github",
            token_url="http://auth.example.com/token",
            client_id="cid",
            tenant_id="t1",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_no_token_url_returns_none(self):
        """Lines 205-206: no resolved token_url → None."""
        manager = OAuthFlowManager()
        token = OAuthToken(access_token="old", refresh_token="refresh-tok")
        manager._tokens[("t1", "github")] = token
        result = await manager.refresh_token(
            server_id="github",
            token_url="",
            client_id="",
            tenant_id="t1",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_with_auth_config(self):
        """auth_config used when token_url/client_id not passed directly."""
        manager = OAuthFlowManager()
        token = OAuthToken(access_token="old", refresh_token="refresh-tok")
        manager._tokens[("t-oauth-extra", "github")] = token

        with respx.mock:
            respx.post("http://auth.example.com/token").mock(
                return_value=httpx.Response(200, json={
                    "access_token": "new-tok",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                })
            )
            result = await manager.refresh_token(
                server_id="github",
                tenant_ctx=TENANT,
                auth_config={
                    "token_url": "http://auth.example.com/token",
                    "client_id": "cid",
                },
            )
        assert result is not None
        assert result.access_token == "new-tok"

    @pytest.mark.asyncio
    async def test_refresh_with_explicit_token(self):
        """token kwarg bypasses lookup."""
        manager = OAuthFlowManager()
        token = OAuthToken(access_token="old", refresh_token="rt")

        with respx.mock:
            respx.post("http://auth.example.com/token").mock(
                return_value=httpx.Response(200, json={
                    "access_token": "refreshed",
                    "expires_in": 3600,
                })
            )
            result = await manager.refresh_token(
                server_id="svc",
                token_url="http://auth.example.com/token",
                client_id="cid",
                tenant_id="t-oauth-extra",
                token=token,
            )
        assert result is not None
        assert result.access_token == "refreshed"

    @pytest.mark.asyncio
    async def test_refresh_http_failure_returns_none(self):
        """Lines 220-223: HTTP failure → None."""
        manager = OAuthFlowManager()
        token = OAuthToken(access_token="old", refresh_token="rt")
        manager._tokens[("t1", "svc")] = token

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=RuntimeError("connection failed"))
            mock_cls.return_value = mock_client

            result = await manager.refresh_token(
                server_id="svc",
                token_url="http://auth.example.com/token",
                client_id="cid",
                tenant_id="t1",
            )
        assert result is None


# ── _persist_token_to_db ──────────────────────────────────────────────────────

class TestPersistTokenToDb:
    @pytest.mark.asyncio
    async def test_persist_noop_when_no_db(self):
        """Lines 238-239: no db → return immediately."""
        manager = OAuthFlowManager()
        token = OAuthToken(access_token="tok")
        await manager._persist_token_to_db("t1", "github", token)  # no error

    @pytest.mark.asyncio
    async def test_persist_with_db(self):
        """Lines 240-278: inserts/upserts into oauth_tokens table."""
        manager = OAuthFlowManager()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock()
        mock_db = MagicMock(return_value=mock_session)
        manager._db_session_factory = mock_db

        token = OAuthToken(access_token="tok-db", refresh_token="rt", expires_in=3600)
        await manager._persist_token_to_db("t1", "github", token)
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_with_vault_encryption(self):
        """Lines 249-254: vault available → encrypt called."""
        manager = OAuthFlowManager()

        mock_vault = MagicMock()
        mock_vault.encrypt = MagicMock(side_effect=lambda x: f"enc({x})")
        manager._vault = mock_vault

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM2", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock()
        manager._db_session_factory = MagicMock(return_value=mock_session)

        token = OAuthToken(access_token="tok", refresh_token="rt")
        await manager._persist_token_to_db("t1", "github", token)
        mock_vault.encrypt.assert_called()

    @pytest.mark.asyncio
    async def test_persist_db_exception_logged(self):
        """Lines 275-278: DB exception → logs warning, no raise."""
        manager = OAuthFlowManager()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM3", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("table missing"))
        manager._db_session_factory = MagicMock(return_value=mock_session)

        token = OAuthToken(access_token="tok")
        await manager._persist_token_to_db("t1", "github", token)  # no raise


# ── load_tokens_from_db ───────────────────────────────────────────────────────

class TestLoadTokensFromDb:
    @pytest.mark.asyncio
    async def test_load_noop_when_no_db(self):
        """Lines 285-286: no db → return 0."""
        manager = OAuthFlowManager()
        result = await manager.load_tokens_from_db()
        assert result == 0

    @pytest.mark.asyncio
    async def test_load_with_db_returns_count(self):
        """Lines 287-323: loads rows and builds OAuthToken objects."""
        manager = OAuthFlowManager()

        from datetime import UTC, datetime, timedelta
        future = datetime.now(UTC) + timedelta(hours=1)
        mock_rows = [
            ("tenant-x", "github", "access-tok-x", "refresh-tok-x", future),
        ]
        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=mock_rows)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        manager._db_session_factory = MagicMock(return_value=mock_session)

        count = await manager.load_tokens_from_db()
        assert count == 1
        token = manager._tokens.get(("tenant-x", "github"))
        assert token is not None
        assert token.access_token == "access-tok-x"

    @pytest.mark.asyncio
    async def test_load_with_vault_decryption(self):
        """Lines 304-310: vault decrypt called on loaded tokens."""
        manager = OAuthFlowManager()

        mock_vault = MagicMock()
        mock_vault.decrypt = MagicMock(side_effect=lambda x: f"dec({x})")
        manager._vault = mock_vault

        from datetime import UTC, datetime, timedelta
        future = datetime.now(UTC) + timedelta(hours=1)
        mock_rows = [("t1", "svc", "enc-access", "enc-refresh", future)]
        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=mock_rows)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        manager._db_session_factory = MagicMock(return_value=mock_session)

        count = await manager.load_tokens_from_db()
        assert count == 1
        mock_vault.decrypt.assert_called()

    @pytest.mark.asyncio
    async def test_load_db_exception_returns_zero(self):
        """Lines 325-328: DB error → logs warning, returns 0."""
        manager = OAuthFlowManager()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db offline"))
        manager._db_session_factory = MagicMock(return_value=mock_session)

        count = await manager.load_tokens_from_db()
        assert count == 0
