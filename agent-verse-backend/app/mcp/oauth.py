"""OAuth flow manager — handles authorization code + PKCE flows for MCP connectors."""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.tenancy.context import TenantContext


@dataclass
class OAuthState:
    """Ephemeral state for an in-progress OAuth flow."""

    server_id: str
    state_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    code_verifier: str = field(default_factory=lambda: secrets.token_urlsafe(64))
    created_at: float = field(default_factory=time.time)

    @property
    def code_challenge(self) -> str:
        digest = hashlib.sha256(self.code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass
class OAuthToken:
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str = ""
    expires_in: int = 3600
    scope: str = ""
    obtained_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() > self.obtained_at + self.expires_in - 60


class OAuthFlowManager:
    """Manages PKCE OAuth 2.0 authorization code flows."""

    def __init__(self) -> None:
        # state_token → OAuthState
        self._pending_flows: dict[str, OAuthState] = {}
        # (tenant_id, server_id) → OAuthToken
        self._tokens: dict[tuple[str, str], OAuthToken] = {}
        # Set externally to enable DB persistence
        self._db_session_factory: Any = None

    def start_flow(self, *, server_id: str, tenant_ctx: TenantContext) -> dict[str, str]:
        """Initiate a PKCE OAuth flow. Returns the PKCE parameters and state token."""
        flow = OAuthState(server_id=server_id)
        self._pending_flows[flow.state_token] = flow
        return {
            "state": flow.state_token,
            "code_challenge": flow.code_challenge,
            "code_challenge_method": "S256",
            "server_id": server_id,
        }

    async def exchange_code(
        self,
        *,
        code: str,
        state: str,
        token_url: str,
        client_id: str,
        redirect_uri: str,
        tenant_ctx: TenantContext,
    ) -> OAuthToken | None:
        """Exchange authorization code for tokens (PKCE flow)."""
        flow = self._pending_flows.pop(state, None)
        if flow is None:
            return None

        data: dict[str, Any]
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": client_id,
                        "code_verifier": flow.code_verifier,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            import logging
            logging.getLogger(__name__).warning(
                "OAuth token exchange failed: %s %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            return None
        except (httpx.ConnectError, httpx.TimeoutException):
            import logging
            logging.getLogger(__name__).error(
                "OAuth token endpoint unreachable: %s", token_url
            )
            return None
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("OAuth exchange unexpected error: %s", exc)
            return None

        token = OAuthToken(
            access_token=data.get("access_token", ""),
            token_type=data.get("token_type", "Bearer"),
            refresh_token=data.get("refresh_token", ""),
            expires_in=int(data.get("expires_in", 3600)),
            scope=data.get("scope", ""),
        )

        # Only store if we got a real token
        if not token.access_token:
            return None

        self._tokens[(tenant_ctx.tenant_id, flow.server_id)] = token
        # Persist to DB if factory is configured
        await self._persist_token_to_db(tenant_ctx.tenant_id, flow.server_id, token)
        return token

    def get_token(self, *args: Any, **kwargs: Any) -> OAuthToken | None:
        """Flexible token lookup.

        Supports two call styles:
        - Keyword: get_token(server_id=..., tenant_ctx=...)
        - Positional: get_token(tenant_id, server_id)
        """
        if args:
            # Positional call: get_token(tenant_id, server_id)
            tenant_id = str(args[0]) if args else ""
            server_id = str(args[1]) if len(args) > 1 else ""
        else:
            # Keyword call: get_token(server_id=..., tenant_ctx=...)
            tenant_ctx = kwargs.get("tenant_ctx")
            server_id = kwargs.get("server_id", "")
            tenant_id = getattr(tenant_ctx, "tenant_id", "") if tenant_ctx else ""
        return self._tokens.get((tenant_id, server_id))

    async def refresh_token(
        self,
        *,
        server_id: str,
        token_url: str = "",
        client_id: str = "",
        tenant_ctx: TenantContext | None = None,
        tenant_id: str = "",
        token: OAuthToken | None = None,
        auth_config: dict[str, Any] | None = None,
    ) -> OAuthToken | None:
        """Refresh an expired access token."""
        # Resolve tenant_id from either tenant_ctx or the explicit keyword
        resolved_tenant_id = (
            getattr(tenant_ctx, "tenant_id", "") if tenant_ctx else tenant_id
        )
        # Use the provided token, or look it up from internal store
        existing = token or self._tokens.get((resolved_tenant_id, server_id))
        if existing is None or not existing.refresh_token:
            return None

        # Resolve token_url / client_id from auth_config if not given directly
        cfg = auth_config or {}
        resolved_token_url = token_url or cfg.get("token_url", "")
        resolved_client_id = client_id or cfg.get("client_id", "")

        if not resolved_token_url:
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    resolved_token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": existing.refresh_token,
                        "client_id": resolved_client_id,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Token refresh failed: %s", exc)
            return None  # Don't silently return stale token

        new_token = OAuthToken(
            access_token=data.get("access_token", existing.access_token),
            token_type=data.get("token_type", "Bearer"),
            refresh_token=data.get("refresh_token", existing.refresh_token),
            expires_in=int(data.get("expires_in", 3600)),
        )
        self._tokens[(resolved_tenant_id, server_id)] = new_token
        return new_token

    async def _persist_token_to_db(
        self, tenant_id: str, server_id: str, token: OAuthToken
    ) -> None:
        """Persist an OAuth token to the database for cross-restart recovery."""
        if self._db_session_factory is None:
            return
        try:
            from datetime import UTC, datetime, timedelta

            from sqlalchemy import text

            expires_at = datetime.now(UTC) + timedelta(seconds=max(token.expires_in, 60))
            # Encrypt tokens before storage if vault is available
            access_enc = token.access_token
            refresh_enc = token.refresh_token or ""
            if hasattr(self, "_vault") and self._vault:
                try:
                    access_enc = self._vault.encrypt(token.access_token)
                    refresh_enc = self._vault.encrypt(token.refresh_token or "")
                except Exception:
                    pass
            async with self._db_session_factory() as session, session.begin():
                await session.execute(
                    text(
                        """INSERT INTO oauth_tokens
                            (id, tenant_id, server_id, access_token, refresh_token, expires_at)
                            VALUES (:id, :tid, :sid, :at, :rt, :exp)
                            ON CONFLICT (tenant_id, server_id)
                            DO UPDATE SET access_token=EXCLUDED.access_token,
                                refresh_token=EXCLUDED.refresh_token,
                                expires_at=EXCLUDED.expires_at"""
                    ),
                    {
                        "id": __import__("uuid").uuid4().hex,
                        "tid": tenant_id,
                        "sid": server_id,
                        "at": access_enc,
                        "rt": refresh_enc,
                        "exp": expires_at,
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("oauth_token_persist_failed", error=str(exc))

    async def load_tokens_from_db(self) -> int:
        """Restore OAuth token state on process startup.

        Returns the number of tokens loaded.
        """
        if self._db_session_factory is None:
            return 0
        try:
            from datetime import UTC, datetime

            from sqlalchemy import text

            async with self._db_session_factory() as session:
                result = await session.execute(
                    text(
                        "SELECT tenant_id, server_id, access_token, refresh_token, expires_at "
                        "FROM oauth_tokens WHERE expires_at > NOW()"
                    )
                )
                rows = result.fetchall()
            for row in rows:
                access = row[2]
                refresh = row[3]
                # Decrypt if vault available
                if hasattr(self, "_vault") and self._vault:
                    try:
                        access = self._vault.decrypt(access)
                        if refresh:
                            refresh = self._vault.decrypt(refresh)
                    except Exception:
                        pass
                expires_in = int(
                    (
                        row[4].replace(tzinfo=UTC) - datetime.now(UTC)
                    ).total_seconds()
                )
                token = OAuthToken(
                    access_token=access,
                    refresh_token=refresh or None,  # type: ignore[arg-type]
                    expires_in=max(0, expires_in),
                    obtained_at=0,
                )
                self._tokens[(row[0], row[1])] = token
            return len(rows)
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("oauth_load_from_db_failed", error=str(exc))
            return 0
