"""User sessions: the credential a person holds after an SSO login (SAML, Google).

Flow
----
1. An SSO callback (SAML ACS, Google callback) has verified an identity. It
   JIT-provisions the person (``users`` + ``tenant_memberships``) through
   :meth:`UserSessionStore.provision_member` and records a session row with a
   one-time **login code** (:meth:`issue_login_code`, 60 s).
2. The callback redirects the browser to the frontend with ``?code=``; the
   frontend exchanges it (``POST /auth/session/exchange``) for an opaque bearer
   token ``avs_…`` (:meth:`exchange_code`). The token never travels in a URL.
3. ``TenantMiddleware`` resolves ``avs_`` tokens with :meth:`resolve` into a
   ``TenantContext`` carrying ``user_id`` and the membership's roles.

Storage and scale
-----------------
Postgres is the source of truth (``user_sessions``, FORCE RLS). Resolution is one
indexed lookup by token digest; the resolved context is cached in the shared
Redis for at most :data:`CACHE_TTL_SECONDS` and the cache entry is deleted on
every revocation (logout, SCIM deprovisioning), so no replica holds session
state in process memory and a revocation is honoured cluster-wide at once.
Everything fails closed: an unreadable store is a 503, never "authenticated".
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import PlatformError
from app.observability.logging import get_logger
from app.tenancy.context import PlanTier, TenantContext

logger = get_logger(__name__)

SESSION_TOKEN_PREFIX = "avs_"
LOGIN_CODE_TTL_SECONDS = 60
CACHE_TTL_SECONDS = 60
DEFAULT_SESSION_TTL_SECONDS = 8 * 3600
_CACHE_PREFIX = "user_session:"

# Membership status that may hold a session (SCIM deactivation sets "deactivated").
MEMBERSHIP_ACTIVE = "active"


class SessionStoreUnavailableError(PlatformError):
    """The session store (Postgres / Redis) could not complete the operation (→ 503)."""

    code = "SESSION_STORE_UNAVAILABLE"
    http_status = 503
    retryable = True


class LoginRefusedError(PlatformError):
    """A verified identity may not log in to this tenant (→ 403)."""

    code = "LOGIN_REFUSED"
    http_status = 403


def is_session_token(raw: str) -> bool:
    return raw.startswith(SESSION_TOKEN_PREFIX)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def membership_roles(role: str | None) -> tuple[str, ...]:
    """RBAC roles of a membership role (least privilege: unknown → viewer)."""
    from app.tenancy.rbac import VALID_ROLES

    if role == "owner":
        return ("admin",)
    if role in VALID_ROLES:
        return (str(role),)
    return ("viewer",)


class UserSessionStore:
    """Postgres-authoritative user sessions with a short shared Redis cache."""

    def __init__(
        self,
        db_factory: Any = None,
        redis: Any = None,
        *,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        self._db = db_factory
        self._redis = redis
        self._ttl = int(session_ttl_seconds)

    def set_db(self, db_factory: Any) -> None:
        self._db = db_factory

    def set_redis(self, redis: Any) -> None:
        self._redis = redis

    def _require_db(self) -> Any:
        if self._db is None:
            raise SessionStoreUnavailableError("User sessions require the database.")
        return self._db

    # ── provisioning ─────────────────────────────────────────────────────────

    async def provision_member(
        self,
        *,
        tenant_id: str,
        email: str,
        name: str | None,
        default_role: str = "viewer",
        jit: bool = True,
    ) -> str:
        """Return the ``users.id`` of *email*'s ACTIVE membership in *tenant_id*.

        JIT-creates the global user and the membership (with *default_role*)
        when *jit* is on; refuses (:class:`LoginRefusedError`) an unknown person
        when it is off, a deactivated membership (SCIM offboarding) always, and
        an inactive / unknown tenant.
        """
        from sqlalchemy import func, select

        from app.db.models.tenant import Tenant
        from app.db.models.user import TenantMembership, User
        from app.db.rls import sqlalchemy_rls_context
        from app.tenancy.rbac import VALID_ROLES

        email = email.strip()
        if not email:
            raise LoginRefusedError("The identity provider sent no e-mail / NameID.")
        role = default_role if default_role in VALID_ROLES else "viewer"
        db = self._require_db()
        refused: str | None = None
        user_id = ""
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                tenant_active = (
                    await session.execute(
                        select(Tenant.id).where(
                            Tenant.id == tenant_id,
                            Tenant.is_active == True,  # noqa: E712
                        )
                    )
                ).scalar_one_or_none()
                user = (
                    await session.execute(
                        select(User).where(func.lower(User.email) == email.lower())
                    )
                ).scalar_one_or_none()
                membership = None
                if user is not None:
                    membership = (
                        await session.execute(
                            select(TenantMembership).where(
                                TenantMembership.user_id == user.id,
                                TenantMembership.tenant_id == tenant_id,
                            )
                        )
                    ).scalar_one_or_none()
                if tenant_active is None:
                    refused = "unknown or inactive tenant"
                elif membership is not None and membership.status != MEMBERSHIP_ACTIVE:
                    refused = "membership deactivated"
                elif membership is None and not jit:
                    refused = "not provisioned for this tenant (JIT provisioning is off)"
                else:
                    if user is None:
                        user = User(email=email, name=name or None)
                        session.add(user)
                        await session.flush()
                    elif not user.name and name:
                        user.name = name
                    if membership is None:
                        session.add(
                            TenantMembership(
                                user_id=user.id,
                                tenant_id=tenant_id,
                                role=role,
                                status=MEMBERSHIP_ACTIVE,
                            )
                        )
                    user.last_login_at = datetime.now(UTC)
                    user_id = str(user.id)
        except Exception as exc:
            logger.warning("user_session_provision_failed", error=str(exc)[:200])
            raise SessionStoreUnavailableError(
                "Could not record the signed-in user; retry.", cause=exc
            ) from exc
        if refused is not None:
            logger.info("sso_login_refused", tenant_id=tenant_id, reason=refused)
            raise LoginRefusedError(f"Login refused: {refused}.")
        return user_id

    # ── login code → token ───────────────────────────────────────────────────

    async def issue_login_code(self, *, tenant_id: str, user_id: str, auth_method: str) -> str:
        """Record a new session and return its one-time login code (60 s)."""
        from app.db.models.user_session import UserSession
        from app.db.rls import sqlalchemy_rls_context

        code = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        db = self._require_db()
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                session.add(
                    UserSession(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        login_code_hash=_digest(code),
                        login_code_expires_at=now + timedelta(seconds=LOGIN_CODE_TTL_SECONDS),
                        auth_method=auth_method,
                        expires_at=now + timedelta(seconds=self._ttl),
                    )
                )
        except Exception as exc:
            raise SessionStoreUnavailableError(
                "Could not start the session; retry the login.", cause=exc
            ) from exc
        return code

    async def exchange_code(self, code: str) -> dict[str, Any] | None:
        """Single-use exchange of a login code for the bearer token.

        ``None`` for an unknown, used, expired or revoked code. The UPDATE is
        conditional on the code digest, so two concurrent exchanges of one code
        cannot both succeed.
        """
        from sqlalchemy import select, text, update

        from app.db.models.tenant import Tenant
        from app.db.models.user_session import UserSession
        from app.db.rls import sqlalchemy_rls_context

        code_hash = _digest(code)
        token = SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)
        db = self._require_db()
        try:
            async with db() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.session_code_hash', :h, true)"), {"h": code_hash}
                )
                row = (
                    await session.execute(
                        select(UserSession.id, UserSession.tenant_id, UserSession.user_id).where(
                            UserSession.login_code_hash == code_hash
                        )
                    )
                ).first()
                await session.execute(text("SELECT set_config('app.session_code_hash', '', true)"))
                if row is None:
                    return None
                session_id, tenant_id, user_id = str(row[0]), str(row[1]), str(row[2])
                async with sqlalchemy_rls_context(session, tenant_id):
                    expires_at = (
                        await session.execute(
                            update(UserSession)
                            .where(
                                UserSession.id == session_id,
                                UserSession.tenant_id == tenant_id,
                                UserSession.login_code_hash == code_hash,
                                UserSession.login_code_expires_at > datetime.now(UTC),
                                UserSession.revoked_at.is_(None),
                            )
                            .values(token_hash=_digest(token), login_code_hash=None)
                            .returning(UserSession.expires_at)
                        )
                    ).scalar_one_or_none()
                    plan_tier = None
                    if expires_at is not None:
                        plan_tier = (
                            await session.execute(
                                select(Tenant.plan_tier).where(
                                    Tenant.id == tenant_id,
                                    Tenant.is_active == True,  # noqa: E712
                                )
                            )
                        ).scalar_one_or_none()
        except Exception as exc:
            raise SessionStoreUnavailableError(
                "Could not complete the login; retry.", cause=exc
            ) from exc
        if expires_at is None or plan_tier is None:
            # Unknown / used / expired / revoked code, or the tenant was
            # deactivated since the login: no session (resolve would refuse it).
            return None
        expires_in = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "plan": str(plan_tier),
        }

    # ── resolution ───────────────────────────────────────────────────────────

    async def resolve(self, token: str) -> TenantContext | None:
        """``TenantContext`` for a live session token, ``None`` when it is not one.

        Raises :class:`SessionStoreUnavailableError` when the store cannot be
        read (the middleware answers 503 — never a silent pass or fail).
        """
        if not is_session_token(token):
            return None
        token_hash = _digest(token)
        cached = await self._cache_get(token_hash)
        if cached is not None:
            return cached
        rec = await self._db_resolve(token_hash)
        if rec is None:
            return None
        ctx = TenantContext(
            tenant_id=rec["tenant_id"],
            plan=rec["plan"],
            api_key_id=f"user:{rec['user_id']}",
            roles=rec["roles"],
            user_id=rec["user_id"],
        )
        await self._cache_set(token_hash, ctx, rec["expires_at"])
        return ctx

    async def _db_resolve(self, token_hash: str) -> dict[str, Any] | None:
        from sqlalchemy import select, text

        from app.db.models.tenant import Tenant
        from app.db.models.user import TenantMembership
        from app.db.models.user_session import UserSession
        from app.db.rls import sqlalchemy_rls_context

        db = self._require_db()
        now = datetime.now(UTC)
        try:
            async with db() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.session_token_hash', :h, true)"),
                    {"h": token_hash},
                )
                row = (
                    await session.execute(
                        select(
                            UserSession.tenant_id,
                            UserSession.user_id,
                            UserSession.expires_at,
                            UserSession.revoked_at,
                        ).where(UserSession.token_hash == token_hash)
                    )
                ).first()
                await session.execute(text("SELECT set_config('app.session_token_hash', '', true)"))
                if row is None or row[3] is not None or row[2] <= now:
                    return None
                tenant_id, user_id, expires_at = str(row[0]), str(row[1]), row[2]
                async with sqlalchemy_rls_context(session, tenant_id):
                    member = (
                        await session.execute(
                            select(TenantMembership.role, TenantMembership.status).where(
                                TenantMembership.tenant_id == tenant_id,
                                TenantMembership.user_id == user_id,
                            )
                        )
                    ).first()
                    tenant = (
                        await session.execute(
                            select(Tenant.plan_tier).where(
                                Tenant.id == tenant_id,
                                Tenant.is_active == True,  # noqa: E712
                            )
                        )
                    ).first()
        except Exception as exc:
            logger.warning("user_session_resolve_failed", error=str(exc)[:200])
            raise SessionStoreUnavailableError(
                "Could not verify the session; retry shortly.", cause=exc
            ) from exc
        if member is None or member[1] != MEMBERSHIP_ACTIVE or tenant is None:
            return None
        try:
            plan = PlanTier(str(tenant[0]))
        except ValueError:
            plan = PlanTier.FREE
        return {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "plan": plan,
            "roles": membership_roles(member[0]),
            "expires_at": expires_at,
        }

    async def _cache_get(self, token_hash: str) -> TenantContext | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(_CACHE_PREFIX + token_hash)
        except Exception:
            return None  # cache miss: Postgres decides
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if float(data["exp"]) <= datetime.now(UTC).timestamp():
                return None
            return TenantContext(
                tenant_id=str(data["tenant_id"]),
                plan=PlanTier(str(data["plan"])),
                api_key_id=f"user:{data['user_id']}",
                roles=tuple(str(r) for r in data["roles"]),
                user_id=str(data["user_id"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    async def _cache_set(self, token_hash: str, ctx: TenantContext, expires_at: datetime) -> None:
        if self._redis is None:
            return
        remaining = int((expires_at - datetime.now(UTC)).total_seconds())
        ttl = min(CACHE_TTL_SECONDS, remaining)
        if ttl <= 0:
            return
        payload = {
            "tenant_id": ctx.tenant_id,
            "plan": ctx.plan.value,
            "user_id": ctx.user_id,
            "roles": list(ctx.roles),
            "exp": expires_at.timestamp(),
        }
        try:
            await self._redis.set(_CACHE_PREFIX + token_hash, json.dumps(payload), ex=ttl)
        except Exception as exc:  # caching is an optimisation; Postgres stays authoritative
            logger.debug("user_session_cache_write_failed", error=str(exc)[:200])

    async def purge_cache(self, token_hashes: list[str]) -> None:
        """Delete cached contexts of revoked sessions so every pod refuses them now.

        A failure is a :class:`SessionStoreUnavailableError` (503): the
        revocation is already in Postgres, but a cached context would keep
        authenticating for up to :data:`CACHE_TTL_SECONDS`, so the caller retries.
        """
        if self._redis is None or not token_hashes:
            return
        try:
            await self._redis.delete(*(_CACHE_PREFIX + h for h in token_hashes))
        except Exception as exc:
            raise SessionStoreUnavailableError(
                "Session revoked in the database but the shared cache could not be purged; retry.",
                cause=exc,
            ) from exc

    # ── revocation ───────────────────────────────────────────────────────────

    async def revoke_token(self, token: str) -> bool:
        """Revoke the session holding *token* (logout). True when one was live."""
        from sqlalchemy import select, text, update

        from app.db.models.user_session import UserSession
        from app.db.rls import sqlalchemy_rls_context

        if not is_session_token(token):
            return False
        token_hash = _digest(token)
        db = self._require_db()
        try:
            async with db() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.session_token_hash', :h, true)"),
                    {"h": token_hash},
                )
                tenant_id = (
                    await session.execute(
                        select(UserSession.tenant_id).where(UserSession.token_hash == token_hash)
                    )
                ).scalar_one_or_none()
                await session.execute(text("SELECT set_config('app.session_token_hash', '', true)"))
                revoked = None
                if tenant_id is not None:
                    async with sqlalchemy_rls_context(session, str(tenant_id)):
                        revoked = (
                            await session.execute(
                                update(UserSession)
                                .where(
                                    UserSession.token_hash == token_hash,
                                    UserSession.tenant_id == str(tenant_id),
                                    UserSession.revoked_at.is_(None),
                                )
                                .values(revoked_at=datetime.now(UTC))
                                .returning(UserSession.id)
                            )
                        ).scalar_one_or_none()
        except Exception as exc:
            raise SessionStoreUnavailableError(
                "Could not revoke the session; retry.", cause=exc
            ) from exc
        await self.purge_cache([token_hash])
        return revoked is not None

    async def revoke_user_sessions(self, tenant_id: str, user_id: str) -> int:
        """Revoke every live session of *user_id* in *tenant_id* (offboarding)."""
        from app.db.rls import sqlalchemy_rls_context

        db = self._require_db()
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                hashes = await revoke_member_sessions_in_tx(session, tenant_id, user_id)
        except Exception as exc:
            raise SessionStoreUnavailableError(
                "Could not revoke the user's sessions; retry.", cause=exc
            ) from exc
        await self.purge_cache(hashes)
        return len(hashes)


async def revoke_member_sessions_in_tx(session: Any, tenant_id: str, user_id: str) -> list[str]:
    """Revoke *user_id*'s sessions in *tenant_id* inside the caller's transaction.

    The caller holds the tenant GUC (``sqlalchemy_rls_context``) — SCIM runs it
    in the same transaction that deactivates the membership, so the two commit
    or roll back together. Returns the token digests whose cached contexts must
    then be purged (:meth:`UserSessionStore.purge_cache`) after the commit.

    Sessions revoked within the last :data:`CACHE_TTL_SECONDS` are returned
    again (their ``revoked_at`` is kept), so a retry after a failed cache purge
    purges them too. Bounded by the person's unexpired sessions
    (``ix_user_sessions_tenant_user``).
    """
    from sqlalchemy import func, or_, update

    from app.db.models.user_session import UserSession

    now = datetime.now(UTC)
    rows = (
        (
            await session.execute(
                update(UserSession)
                .where(
                    UserSession.tenant_id == tenant_id,
                    UserSession.user_id == user_id,
                    UserSession.expires_at > now,
                    or_(
                        UserSession.revoked_at.is_(None),
                        UserSession.revoked_at > now - timedelta(seconds=CACHE_TTL_SECONDS),
                    ),
                )
                .values(
                    revoked_at=func.coalesce(UserSession.revoked_at, now),
                    login_code_hash=None,
                )
                .returning(UserSession.token_hash)
            )
        )
        .scalars()
        .all()
    )
    return [str(h) for h in rows if h]


def get_user_session_store(app: Any) -> UserSessionStore:
    """The app's store (created in ``create_app``, DB/Redis wired in the lifespan)."""
    store = getattr(getattr(app, "state", None), "user_session_store", None)
    if store is None:
        raise SessionStoreUnavailableError("User sessions are not configured.")
    return store  # type: ignore[no-any-return]
