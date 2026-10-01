"""OAuth flow manager — handles authorization code + PKCE flows for MCP connectors."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
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
    # Optional alias fields for external/test construction
    state: str = ""
    tenant_id: str = ""
    redirect_uri: str = ""
    pkce_verifier: str = ""

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


class OAuthReauthorizationRequiredError(RuntimeError):
    """A connection's stored OAuth token cannot be used (it cannot be decrypted, or
    the connection was already marked): the tenant must authorize it again.

    Never a fallback: the stored value used to be returned as the token when the
    vault could not decrypt it, so the *ciphertext* went out as a Bearer token.
    """

    def __init__(self, message: str, *, server_id: str = "", tenant_id: str = "") -> None:
        super().__init__(message)
        self.server_id = server_id
        self.tenant_id = tenant_id


_OAUTH_STATE_TTL = 600  # 10 minutes
# How long a token read from the durable store is served from process memory
# before it is re-read (another replica/worker may have refreshed or revoked it).
_TOKEN_CACHE_TTL_SECONDS = 30.0

_log = logging.getLogger(__name__)


class OAuthFlowManager:
    """Manages PKCE OAuth 2.0 authorization code flows."""

    def __init__(self, vault: Any = None) -> None:
        # vault may be injected from app.state; None means plaintext storage (dev mode)
        self._vault = vault
        # state_token → OAuthState
        self._pending_flows: dict[str, OAuthState] = {}
        # (tenant_id, server_id) → OAuthToken. With a DB factory this is only a
        # short-lived read-through cache of the oauth_tokens table (the durable,
        # RLS-scoped, vault-encrypted source of truth shared by every API replica
        # and the Celery worker); without one (dev/tests) it is the store.
        self._tokens: dict[tuple[str, str], OAuthToken] = {}
        # (tenant_id, server_id) → monotonic time the cached token was read/written.
        self._token_cached_at: dict[tuple[str, str], float] = {}
        # Set externally to enable DB persistence
        self._db_session_factory: Any = None
        # (tenant_id, server_id) → lock serialising concurrent refresh_token()
        # calls for that connector, so a burst of concurrent tool calls that
        # all see the same expired token (e.g. a goal's parallel execution
        # wave) doesn't send duplicate refresh requests. Many OAuth providers
        # rotate the refresh token on each use, so a second concurrent
        # request with the now-superseded refresh_token would fail with
        # invalid_grant instead of just reusing the token the first request
        # already obtained.
        self._refresh_locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Shared store for pending PKCE flows (wired in the app lifespan). The
        # flows lived only in this process: on a multi-replica deployment the
        # provider's callback usually lands on another pod, which rejected it
        # as an invalid/expired state.
        self._redis: Any = None

    def set_redis(self, redis: Any) -> None:
        self._redis = redis

    @staticmethod
    def _flow_key(state: str) -> str:
        return f"oauth_pkce:{state}"

    def _encrypt_token(self, value: str) -> str:
        """Encrypt *value* using the vault if available, else return as-is.

        An encryption failure raises: it used to be swallowed and the token
        silently stored/persisted in plaintext.
        """
        if self._vault is not None and value:
            return str(self._vault.encrypt(value))
        return value

    def _decrypt_token(self, value: str) -> str:
        """Decrypt *value* with the vault; without a vault (dev) it is plaintext.

        A value the vault cannot decrypt raises :class:`OAuthReauthorizationRequiredError`
        (fail closed). It used to be returned unchanged — the ciphertext was then
        sent to the provider as the access/refresh token.
        """
        if self._vault is None or not value:
            return value
        try:
            return str(self._vault.decrypt(value))
        except Exception as exc:
            raise OAuthReauthorizationRequiredError(
                f"stored OAuth token cannot be decrypted ({type(exc).__name__}); "
                "re-authorize the connector"
            ) from exc

    async def _tenant_vault(self, tenant_id: str) -> Any:
        """The tenant's envelope vault (``None`` = no key / no DB). Raises when unreadable."""
        if self._vault is None or self._db_session_factory is None or not tenant_id:
            return None
        from app.providers.tenant_vault import ensure_tenant_vault

        return await ensure_tenant_vault(self._db_session_factory, tenant_id)

    def _seal_token(self, value: str, tenant_vault: Any) -> str:
        """Encrypt for storage: the tenant key (``tv1:``) when it has one (TENANT-ENVELOPE-ALL)."""
        if tenant_vault is None or not value:
            return self._encrypt_token(value)
        from app.providers.tenant_vault import TENANT_CIPHER_PREFIX

        return TENANT_CIPHER_PREFIX + str(tenant_vault.encrypt(value))

    def _open_token(self, value: str, tenant_vault: Any) -> str:
        """Decrypt a stored token; a ``tv1:`` value without the tenant key raises (fail closed)."""
        from app.providers.tenant_vault import is_tenant_encrypted, open_for_tenant

        if value and is_tenant_encrypted(value):
            return open_for_tenant(tenant_vault, value)
        return self._decrypt_token(value)

    def _cleanup_expired_flows(self) -> None:
        """Remove OAuth state tokens older than 10 minutes."""
        now = time.time()
        expired = [
            k for k, v in self._pending_flows.items() if now - v.created_at > _OAUTH_STATE_TTL
        ]
        for k in expired:
            del self._pending_flows[k]

    def get_pending_flow(self, state: str) -> OAuthState | None:
        """Get and validate a pending OAuth flow by state token."""
        self._cleanup_expired_flows()  # cleanup on every access
        flow = self._pending_flows.get(state)
        if flow is None:
            return None
        if time.time() - flow.created_at > _OAUTH_STATE_TTL:
            del self._pending_flows[state]
            return None
        return flow

    def start_flow(
        self, *, server_id: str, tenant_ctx: TenantContext, redirect_uri: str = ""
    ) -> dict[str, str]:
        """Initiate a PKCE OAuth flow. Returns the PKCE parameters and state token.

        ``redirect_uri`` is the exact value sent in the authorize request. It is
        stored with the PKCE state so :meth:`exchange_code` can send the identical
        value (RFC 6749 §4.1.3) — previously the callback route re-derived a
        different URI and every real provider rejected the exchange (invalid_grant).
        """
        flow = OAuthState(
            server_id=server_id,
            tenant_id=getattr(tenant_ctx, "tenant_id", "") or "",
            redirect_uri=redirect_uri,
        )
        self._pending_flows[flow.state_token] = flow
        return {
            "state": flow.state_token,
            "code_challenge": flow.code_challenge,
            "code_challenge_method": "S256",
            "server_id": server_id,
        }

    async def astart_flow(
        self, *, server_id: str, tenant_ctx: TenantContext, redirect_uri: str = ""
    ) -> dict[str, str]:
        """:meth:`start_flow`, with the pending flow stored in Redis when wired."""
        params = self.start_flow(
            server_id=server_id, tenant_ctx=tenant_ctx, redirect_uri=redirect_uri
        )
        if self._redis is not None:
            import json as _json

            flow = self._pending_flows.pop(params["state"])
            await self._redis.set(
                self._flow_key(flow.state_token),
                _json.dumps(
                    {
                        "server_id": flow.server_id,
                        "state_token": flow.state_token,
                        "code_verifier": flow.code_verifier,
                        "created_at": flow.created_at,
                        "tenant_id": flow.tenant_id,
                        "redirect_uri": flow.redirect_uri,
                    }
                ),
                ex=_OAUTH_STATE_TTL,
            )
        return params

    async def _take_shared_flow(self, state: str) -> OAuthState | None:
        """Atomically consume a pending flow from Redis (one callback wins)."""
        if self._redis is None:
            return None
        import json as _json

        key = self._flow_key(state)
        try:
            raw = await self._redis.getdel(key)
        except AttributeError:  # client without GETDEL
            raw = await self._redis.get(key)
            await self._redis.delete(key)
        if not raw:
            return None
        data = _json.loads(raw)
        return OAuthState(
            server_id=data["server_id"],
            state_token=data["state_token"],
            code_verifier=data["code_verifier"],
            created_at=float(data["created_at"]),
            tenant_id=data.get("tenant_id", ""),
            redirect_uri=data.get("redirect_uri", ""),
        )

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
        """Exchange authorization code for tokens (PKCE flow).

        The redirect_uri stored by :meth:`start_flow` takes precedence over the
        ``redirect_uri`` argument, which is only a fallback for flows started
        without one. The token request must repeat the authorize request's value.
        """
        # Validate expiry before consuming the flow
        pending = self.get_pending_flow(state)
        if pending is None:
            shared = await self._take_shared_flow(state)
            if shared is None or time.time() - shared.created_at > _OAUTH_STATE_TTL:
                return None
            pending = shared
            self._pending_flows[state] = shared
        # A flow is bound to the tenant that started it: another tenant holding
        # the state must not complete it into its own token store.
        if pending.tenant_id and pending.tenant_id != getattr(tenant_ctx, "tenant_id", ""):
            self._pending_flows.pop(state, None)
            return None
        flow = self._pending_flows.pop(state, None)
        if flow is None:
            return None
        effective_redirect_uri = flow.redirect_uri or redirect_uri
        # token_url comes from the tenant's connector config: never POST the
        # authorization code (and client credentials) to an internal host.
        from app.net.ssrf_guard import assert_public_url_async, public_async_client

        try:
            await assert_public_url_async(token_url, context="oauth_token_url")
        except ValueError:
            import logging

            logging.getLogger(__name__).warning("OAuth token_url blocked: %s", token_url)
            return None

        data: dict[str, Any]
        try:
            # Pinned to the address validated at connect time: a plain client
            # re-resolved token_url (DNS rebinding past the check above).
            async with public_async_client(timeout=15.0) as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": effective_redirect_uri,
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

            logging.getLogger(__name__).error("OAuth token endpoint unreachable: %s", token_url)
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

        self._cache_token((tenant_ctx.tenant_id, flow.server_id), token)
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

    def _cache_token(self, key: tuple[str, str], token: OAuthToken) -> None:
        self._tokens[key] = token
        self._token_cached_at[key] = time.monotonic()

    def _drop_cached(self, key: tuple[str, str]) -> None:
        self._tokens.pop(key, None)
        self._token_cached_at.pop(key, None)

    def _token_from_row(
        self,
        access_enc: str,
        refresh_enc: str | None,
        expires_at: Any,
        token_type: str | None,
        scope: str | None,
        tenant_vault: Any = None,
    ) -> OAuthToken:
        """Build an OAuthToken from a stored oauth_tokens row (decrypting it)."""
        from datetime import UTC, datetime

        if expires_at is None:
            expires_in = 3600
        else:
            if getattr(expires_at, "tzinfo", None) is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            expires_in = int((expires_at - datetime.now(UTC)).total_seconds())
        return OAuthToken(
            access_token=self._open_token(access_enc, tenant_vault),
            token_type=token_type or "Bearer",
            refresh_token=self._open_token(refresh_enc, tenant_vault) if refresh_enc else "",
            # An access token that expired while we were down keeps expires_in=0
            # (is_expired) so the first use refreshes it with the refresh token.
            expires_in=max(0, expires_in),
            scope=scope or "",
        )

    async def _fetch_token_row(self, tenant_id: str, server_id: str) -> tuple[Any, ...] | None:
        """Read one connector's stored token under the tenant's RLS context."""
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db_session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        "SELECT access_token, refresh_token, expires_at, token_type, scope, "
                        "needs_reauth "
                        "FROM oauth_tokens WHERE tenant_id = :tid AND server_id = :sid"
                    ),
                    {"tid": tenant_id, "sid": server_id},
                )
            ).first()
        return tuple(row) if row is not None else None

    async def aget_token(self, tenant_id: str, server_id: str) -> OAuthToken | None:
        """Token for a connector, read through the durable store.

        Serves the in-process copy while it is fresh (< _TOKEN_CACHE_TTL_SECONDS
        old and not expired); otherwise re-reads oauth_tokens so a token obtained
        or refreshed by another API replica — or needed by the Celery worker,
        which never ran the OAuth flow — is found. A row that no longer exists
        (connector disconnected / token revoked) is not served from stale memory.
        If the store cannot be read, the in-process copy is used.

        A stored token that cannot be decrypted marks the connection as needing
        re-authorization (``oauth_tokens.needs_reauth``) and raises
        :class:`OAuthReauthorizationRequiredError`; so does a connection already
        marked, until a new token is stored (OAuth callback / refresh).
        """
        key = (tenant_id, server_id)
        cached = self._tokens.get(key)
        if self._db_session_factory is None:
            return cached
        cached_at = self._token_cached_at.get(key)
        if (
            cached is not None
            and cached_at is not None
            and time.monotonic() - cached_at < _TOKEN_CACHE_TTL_SECONDS
            and not cached.is_expired()
        ):
            return cached
        try:
            row = await self._fetch_token_row(tenant_id, server_id)
            tenant_vault = await self._tenant_vault(tenant_id) if row is not None else None
        except Exception as exc:
            _log.warning("oauth_token_read_failed server_id=%s error=%s", server_id, exc)
            return cached
        if row is None:
            self._drop_cached(key)
            return None
        if len(row) > 5 and row[5]:
            self._drop_cached(key)
            raise OAuthReauthorizationRequiredError(
                "the connector's OAuth authorization is no longer usable; re-authorize it",
                server_id=server_id,
                tenant_id=tenant_id,
            )
        # A tv1 token whose tenant key is gone raises TenantVaultError (never
        # served as ciphertext or opened with another key).
        try:
            token = self._token_from_row(*row[:5], tenant_vault=tenant_vault)
        except OAuthReauthorizationRequiredError as exc:
            self._drop_cached(key)
            await self._mark_needs_reauth(tenant_id, server_id, str(row[0] or ""))
            raise OAuthReauthorizationRequiredError(
                str(exc), server_id=server_id, tenant_id=tenant_id
            ) from exc
        self._cache_token(key, token)
        await self._rewrap_row(tenant_id, server_id, row, token, tenant_vault)
        return token

    async def _mark_needs_reauth(self, tenant_id: str, server_id: str, access_enc: str) -> None:
        """Durably flag the connection (every replica and the worker then refuse it
        without retrying the decrypt). Compare-and-swap on the ciphertext that
        failed, so a token stored concurrently is not flagged. Best effort: the
        caller raises either way."""
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(
                        "UPDATE oauth_tokens SET needs_reauth = true "
                        "WHERE tenant_id = :tid AND server_id = :sid AND access_token = :at"
                    ),
                    {"tid": tenant_id, "sid": server_id, "at": access_enc},
                )
        except Exception as exc:
            _log.warning("oauth_mark_reauth_failed server_id=%s error=%s", server_id, exc)
        _log.error(
            "oauth_reauthorization_required tenant_id=%s server_id=%s", tenant_id, server_id
        )

    async def _rewrap_row(
        self,
        tenant_id: str,
        server_id: str,
        row: tuple[Any, ...],
        token: OAuthToken,
        tenant_vault: Any,
    ) -> None:
        """Lazy re-wrap: re-seal a platform-vault (or old-tenant-key) token row with the
        tenant's current key. Compare-and-swap on the old ciphertext so a concurrent
        refresh is never overwritten; best effort (the token was already read)."""
        from app.providers.tenant_vault import needs_rewrap

        access_enc, refresh_enc = str(row[0] or ""), str(row[1] or "")
        if not (needs_rewrap(tenant_vault, access_enc) or needs_rewrap(tenant_vault, refresh_enc)):
            return
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(
                        "UPDATE oauth_tokens SET access_token = :at, refresh_token = :rt "
                        "WHERE tenant_id = :tid AND server_id = :sid "
                        "AND access_token = :old_at"
                    ),
                    {
                        "at": self._seal_token(token.access_token, tenant_vault),
                        "rt": self._seal_token(token.refresh_token or "", tenant_vault),
                        "tid": tenant_id,
                        "sid": server_id,
                        "old_at": access_enc,
                    },
                )
        except Exception as exc:
            _log.warning("oauth_token_rewrap_failed server_id=%s error=%s", server_id, exc)

    def _get_refresh_lock(self, tenant_id: str, server_id: str) -> asyncio.Lock:
        key = (tenant_id, server_id)
        lock = self._refresh_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._refresh_locks[key] = lock
        return lock

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
        """Refresh an expired access token.

        Concurrent callers for the same (tenant, server) are serialised on a
        lock. Without it, a burst of tool calls that all observe the same
        expired token (e.g. a goal's parallel execution wave) each fire their
        own refresh request; a provider that rotates refresh tokens on use
        accepts only the first and rejects the rest with invalid_grant, so
        every other caller in the wave would fail outright — and previously
        did so ungracefully, sending its request with NO Authorization
        header at all rather than falling back to the token the winning
        caller just obtained.
        """
        # Resolve tenant_id from either tenant_ctx or the explicit keyword
        resolved_tenant_id = getattr(tenant_ctx, "tenant_id", "") if tenant_ctx else tenant_id
        # Use the provided token, or look it up from internal store
        key = (resolved_tenant_id, server_id)
        existing = token or await self.aget_token(resolved_tenant_id, server_id)
        if existing is None or not existing.refresh_token:
            return None

        async with self._get_refresh_lock(resolved_tenant_id, server_id):
            # Another concurrent caller — in this process, another replica or the
            # worker — may have already refreshed this token. Reuse it instead of
            # sending a second refresh request with our (possibly now superseded)
            # refresh_token; rotating providers reject that with invalid_grant.
            current = self._tokens.get(key)
            if self._db_session_factory is not None:
                try:
                    row = await self._fetch_token_row(resolved_tenant_id, server_id)
                    if row is not None and not (len(row) > 5 and row[5]):
                        current = self._token_from_row(
                            *row[:5], tenant_vault=await self._tenant_vault(resolved_tenant_id)
                        )
                except Exception as exc:
                    _log.warning("oauth_token_read_failed server_id=%s error=%s", server_id, exc)
            if current is not None and current.access_token != existing.access_token:
                if not current.is_expired():
                    self._cache_token(key, current)
                    return current
                if current.refresh_token:
                    # Newer (rotated) refresh token written elsewhere.
                    existing = current

            # Resolve token_url / client_id from auth_config if not given directly
            cfg = auth_config or {}
            resolved_token_url = token_url or cfg.get("token_url", "")
            resolved_client_id = client_id or cfg.get("client_id", "")

            if not resolved_token_url:
                return None

            # token_url comes from the tenant's connector config: the refresh
            # token (and client id) must never be POSTed to an internal host.
            # Every hop is re-validated and the socket is pinned to the checked
            # address (no DNS-rebinding window).
            from app.net.ssrf_guard import public_async_client, request_public

            try:
                async with public_async_client(timeout=15.0) as client:
                    resp = await request_public(
                        client,
                        "POST",
                        resolved_token_url,
                        context="oauth_token_url",
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": existing.refresh_token,
                            "client_id": resolved_client_id,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as exc:
                _log.error("Token refresh failed: %s", exc)
                return None  # Don't silently return stale token

            new_token = OAuthToken(
                access_token=data.get("access_token", existing.access_token),
                token_type=data.get("token_type", "Bearer"),
                refresh_token=data.get("refresh_token", existing.refresh_token),
                expires_in=int(data.get("expires_in", 3600)),
                scope=data.get("scope", existing.scope),
            )
            self._cache_token(key, new_token)
            # Durable, so other replicas and the worker use the refreshed token
            # (and a rotated refresh token is not lost on restart).
            await self._persist_token_to_db(resolved_tenant_id, server_id, new_token)
            return new_token

    async def _persist_token_to_db(self, tenant_id: str, server_id: str, token: OAuthToken) -> None:
        """Persist an OAuth token to the database for cross-restart recovery."""
        if self._db_session_factory is None:
            return
        try:
            from datetime import UTC, datetime, timedelta

            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            expires_at = datetime.now(UTC) + timedelta(seconds=max(token.expires_in, 60))
            # Sealed with the tenant's own key when it has one; an unreadable
            # tenant key aborts the write (never a fallback to the platform key).
            tenant_vault = await self._tenant_vault(tenant_id)
            access_enc = self._seal_token(token.access_token, tenant_vault)
            refresh_enc = self._seal_token(token.refresh_token or "", tenant_vault)
            # The write needs the tenant's RLS context (FORCE RLS), and the upsert
            # key (tenant_id, server_id) only exists since migration f1a2b3c4d5e7:
            # before it every insert was rejected, so no token was ever persisted.
            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(
                        """INSERT INTO oauth_tokens
                            (id, tenant_id, server_id, access_token, refresh_token,
                             token_type, scope, expires_at)
                            VALUES (:id, :tid, :sid, :at, :rt, :tt, :sc, :exp)
                            ON CONFLICT (tenant_id, server_id)
                            DO UPDATE SET access_token=EXCLUDED.access_token,
                                refresh_token=EXCLUDED.refresh_token,
                                needs_reauth=false,
                                token_type=EXCLUDED.token_type,
                                scope=EXCLUDED.scope,
                                expires_at=EXCLUDED.expires_at"""
                    ),
                    {
                        "id": __import__("uuid").uuid4().hex,
                        "tid": tenant_id,
                        "sid": server_id,
                        "at": access_enc,
                        "rt": refresh_enc,
                        "tt": token.token_type or "Bearer",
                        "sc": token.scope or "",
                        "exp": expires_at,
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("oauth_token_persist_failed", error=str(exc))

    async def _fetch_all_token_rows(self) -> list[tuple[Any, ...]]:
        """Every tenant's stored tokens that are still usable (startup warm-up)."""
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        # Cross-tenant startup restore: the maintenance role (under the
        # NOBYPASSRLS app role a GUC-less read sees nothing).
        factory = getattr(self, "_system_session_factory", None) or get_system_session_factory()
        async with factory() as session, session.begin(), system_session(session):
            result = await session.execute(
                text(
                    "SELECT tenant_id, server_id, access_token, refresh_token, "
                    "expires_at, token_type, scope FROM oauth_tokens "
                    "WHERE expires_at IS NULL OR expires_at > NOW() "
                    "OR (refresh_token IS NOT NULL AND refresh_token <> '')"
                )
            )
            return [tuple(r) for r in result.fetchall()]

    async def load_tokens_from_db(self) -> int:
        """Warm the token cache on process startup.

        A token whose access token expired while the service was down is kept
        when it has a refresh token (it is refreshed on first use); it used to be
        dropped, disconnecting the connector. Returns the number of tokens loaded.
        """
        if self._db_session_factory is None:
            return 0
        try:
            rows = await self._fetch_all_token_rows()
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("oauth_load_from_db_failed", error=str(exc))
            return 0
        loaded = 0
        from app.providers.tenant_vault import is_tenant_encrypted

        for row in rows:
            try:
                # tv1 rows need that tenant's key (loaded under its RLS context).
                tenant_vault = (
                    await self._tenant_vault(str(row[0]))
                    if is_tenant_encrypted(str(row[2] or ""))
                    or is_tenant_encrypted(str(row[3] or ""))
                    else None
                )
                token = self._token_from_row(
                    row[2], row[3], row[4], row[5] if len(row) > 5 else None,
                    row[6] if len(row) > 6 else None,
                    tenant_vault=tenant_vault,
                )
            except Exception as exc:
                _log.warning("oauth_token_restore_failed server_id=%s error=%s", row[1], exc)
                continue
            if token.is_expired() and not token.refresh_token:
                continue
            self._cache_token((row[0], row[1]), token)
            loaded += 1
        return loaded


def build_worker_oauth_manager(db_session_factory: Any) -> OAuthFlowManager | None:
    """An OAuth manager for a process that never ran the OAuth flow (Celery worker).

    Reads connector tokens through the durable oauth_tokens store (RLS-scoped,
    vault-decrypted) and persists refreshes back, so worker-run goals send the
    same Bearer token the API obtained. ``None`` without a DB (nothing to read).
    """
    if db_session_factory is None:
        return None
    from app.providers.vault import get_vault

    mgr = OAuthFlowManager(vault=get_vault())
    mgr._db_session_factory = db_session_factory
    return mgr
