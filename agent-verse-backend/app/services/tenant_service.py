"""TenantService — in-memory tenant and API key management.

In production this would be backed by PostgreSQL (models exist in app/db/models.py),
but for the current vertical slice we use in-memory storage so the rest of the stack
can be wired and tested without a running database.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ConflictError, NotFoundError
from app.tenancy.context import PlanTier, TenantContext

# ── utilities ─────────────────────────────────────────────────────────────────


def _hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of a raw API key.  The raw key is never stored."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _generate_raw_key(plan: str = "free") -> str:
    """Generate a cryptographically random API key with a recognisable plan prefix."""
    return f"av_{plan}_{secrets.token_urlsafe(32)}"


# ── service ───────────────────────────────────────────────────────────────────


class TenantService:
    """In-memory implementation of tenant and API key management.

    All state is scoped to a single instance — suitable for the running process
    and for unit tests.  Wire as ``app.state.tenant_service`` in the factory.
    """

    def __init__(self, db_session_factory: Any = None) -> None:
        # tenant_id → {tenant_id, name, email, plan, created_at}
        self._tenants: dict[str, dict[str, Any]] = {}
        # normalised email → tenant_id (fast duplicate-email detection)
        self._email_index: dict[str, str] = {}
        # key_id → full key record (raw key is never stored)
        self._keys: dict[str, dict[str, Any]] = {}
        # sha256_hash → key_id  (used by resolve_api_key)
        self._hash_to_key_id: dict[str, str] = {}
        # tenant_id → ordered list of key_ids
        self._tenant_keys: dict[str, list[str]] = {}
        # None in tests, real factory in production
        self._db: Any = db_session_factory
        # Optional Redis client for caching (set by tests or lifespan)
        self._redis: Any = None

    def set_redis(self, redis: Any) -> None:
        """Wire the shared Redis client used to cache resolved API-key contexts
        (``api_key:{hash}``, 300s TTL). The cache is shared across pods and cleared
        on revoke, so revocation propagates cluster-wide."""
        self._redis = redis

    # ── tenant CRUD ───────────────────────────────────────────────────────────

    async def create_tenant(self, name: str, email: str) -> dict[str, Any]:
        """Create a new tenant, generate an initial API key, and return both.

        The raw API key appears **once** in this response and is never stored.
        Raises :class:`~app.core.errors.ConflictError` if the e-mail is taken.

        Writes to DB first, then updates in-memory cache, then invalidates Redis.
        """
        normalised = email.lower()
        if normalised in self._email_index:
            raise ConflictError(f"Email already registered: {email}")

        tenant_id = uuid.uuid4().hex
        plan = PlanTier.FREE
        raw_key = _generate_raw_key(plan.value)
        key_id = uuid.uuid4().hex
        key_hash = _hash_key(raw_key)
        created_at = datetime.now(UTC).isoformat()

        # ── 1. DB-first write (transactional) ────────────────────────────────
        await self._db_create_tenant_with_api_key(
            tenant_id=tenant_id,
            name=name,
            email=email,
            plan=plan.value,
            key_id=key_id,
            key_hash=key_hash,
        )

        # ── 2. Update in-memory cache after successful DB write ───────────────
        self._tenants[tenant_id] = {
            "tenant_id": tenant_id,
            "name": name,
            "email": email,
            "plan": plan.value,
            "created_at": created_at,
        }
        self._email_index[normalised] = tenant_id
        self._keys[key_id] = {
            "key_id": key_id,
            "tenant_id": tenant_id,
            "name": "Default",
            "scopes": [],
            "expires_at": None,
            "key_hash": key_hash,
            "is_active": True,
            "created_at": created_at,
            "roles": ["admin"],  # Tenant-owner's initial key gets full admin access
        }
        self._hash_to_key_id[key_hash] = key_id
        self._tenant_keys.setdefault(tenant_id, []).append(key_id)

        # ── 3. Invalidate Redis cache so stale entries are evicted immediately ─
        await self.invalidate_tenant_cache(tenant_id, redis=self._redis)

        return {
            "tenant_id": tenant_id,
            "name": name,
            "email": email,
            "plan": plan.value,
            "api_key": raw_key,  # one-time; not stored
            "api_key_id": key_id,
        }

    async def get_tenant(self, tenant_id: str) -> dict[str, Any]:
        """Return tenant profile.  Raises :class:`~app.core.errors.NotFoundError` if missing."""
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError(f"Tenant not found: {tenant_id}")
        return dict(tenant)

    # ── API key management ────────────────────────────────────────────────────

    async def list_api_keys(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return all keys for *tenant_id* — raw keys and hashes are **never** included."""
        key_ids = self._tenant_keys.get(tenant_id, [])
        return [
            {
                "key_id": self._keys[kid]["key_id"],
                "name": self._keys[kid]["name"],
                "scopes": self._keys[kid]["scopes"],
                "expires_at": self._keys[kid]["expires_at"],
                "is_active": self._keys[kid]["is_active"],
                "created_at": self._keys[kid]["created_at"],
            }
            for kid in key_ids
            if kid in self._keys
        ]

    async def create_api_key(
        self,
        tenant_id: str,
        name: str,
        scopes: list[str],
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a new API key.  The raw key is returned once and never stored."""
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError(f"Tenant not found: {tenant_id}")

        plan: str = tenant["plan"]
        raw_key = _generate_raw_key(plan)
        key_id = uuid.uuid4().hex
        key_hash = _hash_key(raw_key)
        created_at = datetime.now(UTC).isoformat()

        self._keys[key_id] = {
            "key_id": key_id,
            "tenant_id": tenant_id,
            "name": name,
            "scopes": scopes,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "key_hash": key_hash,
            "is_active": True,
            "created_at": created_at,
        }
        self._hash_to_key_id[key_hash] = key_id
        self._tenant_keys.setdefault(tenant_id, []).append(key_id)

        # DB persistence (transactional — await to ensure key is durable before returning)
        await self._db_create_api_key(key_id, tenant_id, name, key_hash, scopes, expires_at)
        # Invalidate tenant cache since the key roster changed
        await self.invalidate_tenant_cache(tenant_id, redis=self._redis)

        return {
            "key_id": key_id,
            "name": name,
            "scopes": scopes,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "is_active": True,
            "created_at": created_at,
            "raw_key": raw_key,  # one-time; not stored
        }

    async def revoke_api_key(self, tenant_id: str, key_id: str) -> None:
        """Deactivate *key_id*.  Raises :class:`~app.core.errors.NotFoundError` if not found.

        Multi-pod: the DB write is the source of truth (resolve is DB-authoritative),
        and the shared ``api_key:{hash}`` Redis cache entry is deleted here — so the
        revocation is honoured on every pod on the next request, not just this one.
        """
        key = self._keys.get(key_id)
        # Best-effort in-memory flip on this pod; the DB is authoritative for others.
        key_hash = key.get("key_hash") if key else None
        if key is not None:
            if key["tenant_id"] != tenant_id:
                raise NotFoundError(f"API key not found: {key_id}")
            key["is_active"] = False
        # DB persistence (transactional — await to ensure revocation is durable).
        # Returns the key_hash even when the key isn't in this pod's memory.
        db_hash = await self._db_revoke_api_key(key_id, tenant_id)
        key_hash = key_hash or db_hash
        if key is None and db_hash is None:
            raise NotFoundError(f"API key not found: {key_id}")
        # Delete the SHARED per-key resolution cache so no pod serves the revoked key.
        if key_hash and self._redis is not None:
            with suppress(Exception):
                await self._redis.delete(f"api_key:{key_hash}")
        # Also invalidate the tenant-level cache.
        await self.invalidate_tenant_cache(tenant_id, redis=self._redis)

    async def resolve_api_key(self, raw_key: str) -> TenantContext | None:
        """Validate *raw_key* and return the :class:`TenantContext`, or ``None``.

        Used by :class:`~app.tenancy.middleware.TenantMiddleware` as the key
        resolver callback.

        Redis caching: on first lookup the resolved context is cached under
        ``api_key:{sha256(raw_key)}`` (TTL 300 s) so subsequent requests skip
        the in-memory dict walk entirely.
        """
        import json as _json

        key_hash = _hash_key(raw_key)
        cache_key = f"api_key:{key_hash}"

        # ── Redis cache hit (shared across pods; cleared on revoke) ─────────
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached is not None:
                    data = _json.loads(cached)
                    return TenantContext(
                        tenant_id=data["tenant_id"],
                        plan=PlanTier(data["plan"]),
                        api_key_id=data["api_key_id"],
                        roles=tuple(data.get("roles", ("operator",))),
                    )
            except Exception:
                pass  # fall through on Redis errors

        # ── DB-authoritative lookup (multi-pod correctness) ────────────────
        # When a DB is wired, the database is the single source of truth: resolve
        # each key against it (indexed by key_hash) rather than the per-pod
        # in-memory dict, so a key created/revoked on ANY pod takes effect on ALL
        # pods immediately (the Redis cache above is cleared on revoke). The
        # in-memory path below is only for the no-DB (test/dev) build.
        if self._db is not None:
            rec = await self._db_resolve_by_hash(key_hash)
            if rec is None:
                return None
            ctx = TenantContext(
                tenant_id=rec["tenant_id"],
                plan=PlanTier(rec["plan"]),
                api_key_id=rec["api_key_id"],
                roles=tuple(rec["roles"]),
            )
            if self._redis is not None:
                with suppress(Exception):
                    await self._redis.setex(
                        cache_key,
                        300,
                        _json.dumps(
                            {
                                "tenant_id": ctx.tenant_id,
                                "plan": ctx.plan.value,
                                "api_key_id": ctx.api_key_id,
                                "roles": list(ctx.roles),
                            }
                        ),
                    )
            return ctx

        # ── In-memory lookup (no DB configured: tests / dev only) ──────────
        key_id = self._hash_to_key_id.get(key_hash)
        if key_id is None:
            return None
        key = self._keys.get(key_id)
        if key is None or not key["is_active"]:
            return None

        # Fix 4: reject keys whose expiry has passed.
        expires_at_raw = key.get("expires_at")
        if expires_at_raw is not None:
            expiry = datetime.fromisoformat(expires_at_raw)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if datetime.now(UTC) > expiry:
                return None

        tenant = self._tenants.get(key["tenant_id"])
        if tenant is None:
            return None
        # Default role is "operator" (least-privilege non-admin default).
        # Explicit roles stored on the key record override this default.
        key_roles: tuple[str, ...] = tuple(key.get("roles", ("operator",))) or ("operator",)
        ctx = TenantContext(
            tenant_id=tenant["tenant_id"],
            plan=PlanTier(tenant["plan"]),
            api_key_id=key_id,
            roles=key_roles,
        )

        # ── Populate Redis cache ───────────────────────────────────────────
        if self._redis is not None:
            with suppress(Exception):  # caching is best-effort
                await self._redis.setex(
                    cache_key,
                    300,
                    _json.dumps(
                        {
                            "tenant_id": ctx.tenant_id,
                            "plan": ctx.plan.value,
                            "api_key_id": ctx.api_key_id,
                            "roles": list(ctx.roles),
                        }
                    ),
                )

        return ctx

    # ── DB persistence helpers ────────────────────────────────────────────────

    async def _db_create_tenant(self, tenant_id: str, name: str, email: str, plan: str) -> None:
        """Persist tenant to PostgreSQL. No-op if DB not configured."""
        if self._db is None:
            return
        try:
            from app.db.models.tenant import Tenant
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                t = Tenant(id=tenant_id, name=name, email=email, plan_tier=plan)
                session.add(t)
        except Exception as exc:
            logging.getLogger(__name__).warning("DB persist tenant failed: %s", exc)

    async def _db_create_tenant_with_api_key(
        self,
        *,
        tenant_id: str,
        name: str,
        email: str,
        plan: str,
        key_id: str,
        key_hash: str,
    ) -> None:
        """Persist a new tenant and its initial API key in one transaction."""
        if self._db is None:
            return
        try:
            from app.db.models.tenant import ApiKey, Tenant
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                tenant = Tenant(id=tenant_id, name=name, email=email, plan_tier=plan)
                session.add(tenant)
                session.add(
                    ApiKey(
                        id=key_id,
                        tenant_id=tenant_id,
                        name="Default",
                        key_hash=key_hash,
                        scopes=[],
                        roles=["admin"],  # initial owner key gets full admin access
                    )
                )
        except Exception as exc:
            logging.getLogger(__name__).warning("DB persist tenant/default api_key failed: %s", exc)

    async def _db_create_api_key(
        self,
        key_id: str,
        tenant_id: str,
        name: str,
        key_hash: str,
        scopes: list[str],
        expires_at: datetime | None = None,
    ) -> None:
        """Persist API key to PostgreSQL."""
        if self._db is None:
            return
        try:
            from app.db.models.tenant import ApiKey
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                k = ApiKey(
                    id=key_id,
                    tenant_id=tenant_id,
                    name=name,
                    key_hash=key_hash,
                    scopes=scopes,
                    expires_at=expires_at,
                )
                session.add(k)
        except Exception as exc:
            logging.getLogger(__name__).warning("DB persist api_key failed: %s", exc)

    async def _db_revoke_api_key(self, key_id: str, tenant_id: str) -> str | None:
        """Mark API key as inactive in PostgreSQL; return its key_hash (for cache
        invalidation) or None."""
        if self._db is None:
            return None
        try:
            from sqlalchemy import select, update

            from app.db.models.tenant import ApiKey
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                key_hash = (
                    await session.execute(select(ApiKey.key_hash).where(ApiKey.id == key_id))
                ).scalar_one_or_none()
                await session.execute(
                    update(ApiKey).where(ApiKey.id == key_id).values(is_active=False)
                )
            return key_hash
        except Exception as exc:
            logging.getLogger(__name__).warning("DB revoke api_key failed: %s", exc)
            return None

    async def _db_resolve_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Authoritative single-key lookup by hash, bypassing RLS (this runs BEFORE
        a tenant context exists — it is what establishes it). Returns the active,
        non-expired key + tenant plan, or None. This makes auth DB-authoritative so
        a key revoked on one pod is honoured cluster-wide (the DB is the source of
        truth; the Redis cache in front is cleared on revoke)."""
        if self._db is None:
            return None
        try:
            from sqlalchemy import select

            from app.db.models.tenant import ApiKey, Tenant
            from app.db.rls import system_session

            async with self._db() as session, session.begin(), system_session(session):
                row = (
                    await session.execute(
                        select(ApiKey, Tenant)
                        .join(Tenant, Tenant.id == ApiKey.tenant_id)
                        .where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)  # noqa: E712
                    )
                ).first()
            if row is None:
                return None
            key, tenant = row
            if key.expires_at is not None:
                expiry = key.expires_at
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                if datetime.now(UTC) > expiry:
                    return None
            if not tenant.is_active:
                return None
            return {
                "tenant_id": tenant.id,
                "plan": tenant.plan_tier,
                "api_key_id": key.id,
                "roles": list(key.roles or ["operator"]) or ["operator"],
            }
        except Exception as exc:
            logging.getLogger(__name__).warning("DB resolve api_key failed: %s", exc)
            return None

    # ── SSO JIT provisioning ──────────────────────────────────────────────────

    async def get_tenant_by_sso_sub(self, *, sso_sub: str) -> dict[str, Any] | None:
        """Find a tenant by their SSO subject identifier (Keycloak sub claim)."""
        # Check in-memory first
        for tenant in self._tenants.values():
            if tenant.get("sso_sub") == sso_sub:
                return tenant

        # Check DB
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with self._db() as session:
                    row = (
                        await session.execute(
                            text(
                                "SELECT id, name, email, plan, sso_sub "
                                "FROM tenants WHERE sso_sub = :sub LIMIT 1"
                            ),
                            {"sub": sso_sub},
                        )
                    ).fetchone()
                    if row:
                        return {
                            "tenant_id": str(row[0]),
                            "name": row[1],
                            "email": row[2],
                            "plan": row[3],
                            "sso_sub": row[4],
                        }
            except Exception as exc:
                logging.getLogger(__name__).warning("sso_lookup_failed: %s", exc)
        return None

    async def get_key_by_sso_sub(self, *, sso_sub: str) -> dict[str, Any] | None:
        """Return the primary API key record associated with an SSO subject.

        The tenant dict created by ``create_tenant_from_sso`` stores the initial
        ``api_key_id``. This method retrieves that key record so callers can use
        a real, persisted key_id instead of a ghost ``sso:{sub}`` string.
        """
        for tenant in self._tenants.values():
            if tenant.get("sso_sub") == sso_sub:
                key_id = tenant.get("api_key_id")
                if not key_id:
                    return None
                key_rec = self._keys.get(key_id)
                if key_rec:
                    return {"key_id": key_id, **key_rec}
                # Return minimal record using tenant data
                return {
                    "key_id": key_id,
                    "tenant_id": tenant.get("tenant_id", ""),
                    "sso_sub": sso_sub,
                }
        return None

    async def create_tenant_from_sso(
        self,
        *,
        sso_sub: str,
        email: str,
        name: str,
        plan: str = "starter",
    ) -> dict[str, Any]:
        """JIT-provision a new tenant from an SSO login."""
        plan_map = {
            "free": PlanTier.FREE,
            "starter": PlanTier.STARTER,
            "professional": PlanTier.PROFESSIONAL,
            "enterprise": PlanTier.ENTERPRISE,
        }
        plan_tier = plan_map.get(plan.lower(), PlanTier.STARTER)

        tenant_id = uuid.uuid4().hex
        api_key = f"av_{uuid.uuid4().hex}"
        api_key_id = "sso-jit"  # default; overwritten if DB persist succeeds

        # Persist to DB
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with self._db() as session, session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO tenants (id, name, email, plan, created_at, is_active) "
                            "VALUES (:id, :name, :email, :plan, NOW(), TRUE) "
                            "ON CONFLICT (id) DO NOTHING"
                        ),
                        {
                            "id": tenant_id,
                            "name": name,
                            "email": email,
                            "plan": plan_tier.value,
                        },
                    )
                    # Store sso_sub if the column exists
                    with suppress(Exception):  # sso_sub column may not exist yet
                        await session.execute(
                            text("UPDATE tenants SET sso_sub = :sub WHERE id = :id"),
                            {"sub": sso_sub, "id": tenant_id},
                        )

                    # Create initial API key
                    key_hash = _hash_key(api_key)
                    api_key_id = uuid.uuid4().hex
                    await session.execute(
                        text(
                            "INSERT INTO api_keys (id, tenant_id, key_hash, name, created_at) "
                            "VALUES (:id, :tid, :hash, :kname, NOW())"
                        ),
                        {
                            "id": api_key_id,
                            "tid": tenant_id,
                            "hash": key_hash,
                            "kname": "SSO auto-provisioned",
                        },
                    )
            except Exception as exc:
                logging.getLogger(__name__).warning("sso_tenant_create_failed: %s", exc)

        tenant: dict[str, Any] = {
            "tenant_id": tenant_id,
            "name": name,
            "email": email,
            "plan": plan_tier.value,
            "api_key": api_key,
            "api_key_id": api_key_id,
            "sso_sub": sso_sub,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._tenants[tenant_id] = tenant
        self._email_index[email.lower()] = tenant_id
        return tenant

    async def sync_from_db(self) -> int:
        """Load tenants and API keys from PostgreSQL into memory on startup.

        Returns number of tenants loaded.
        """
        if self._db is None:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models.tenant import ApiKey, Tenant
            from app.db.rls import sqlalchemy_rls_context

            loaded = 0
            async with self._db() as session:
                # Load all active tenants
                result = await session.execute(
                    select(Tenant).where(Tenant.is_active == True)  # noqa: E712
                )
                tenants = result.scalars().all()
                for t in tenants:
                    if t.id not in self._tenants:
                        self._tenants[t.id] = {
                            "tenant_id": t.id,
                            "name": t.name,
                            "email": t.email,
                            "plan": t.plan_tier,
                            "created_at": t.created_at.isoformat() if t.created_at else "",
                        }
                        self._email_index[t.email.lower()] = t.id
                        loaded += 1
                # api_keys has tenant RLS enabled, so load keys under each tenant context.
                for tenant_id in list(self._tenants):
                    async with sqlalchemy_rls_context(session, tenant_id):
                        key_result = await session.execute(
                            select(ApiKey).where(
                                ApiKey.tenant_id == tenant_id,
                                ApiKey.is_active == True,  # noqa: E712
                            )
                        )
                    keys = key_result.scalars().all()
                    for k in keys:
                        key_data = {
                            "key_id": k.id,
                            "tenant_id": k.tenant_id,
                            "name": k.name,
                            "scopes": list(k.scopes or []),
                            "roles": list(k.roles or ["admin"]),
                            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                            "key_hash": k.key_hash,
                            "is_active": True,
                            "created_at": k.created_at.isoformat() if k.created_at else "",
                        }
                        # Always update from DB (not just on first load) so role/scope
                        # changes made via DB or API are picked up on next sync.
                        self._keys[k.id] = key_data
                        self._hash_to_key_id[k.key_hash] = k.id
                        self._tenant_keys.setdefault(k.tenant_id, [])
                        if k.id not in self._tenant_keys[k.tenant_id]:
                            self._tenant_keys[k.tenant_id].append(k.id)
            logging.getLogger(__name__).info("Synced %d tenants from DB", loaded)
            return loaded
        except Exception as exc:
            logging.getLogger(__name__).warning("DB sync failed: %s", exc)
            return 0

    # ── Redis read-through cache helpers ──────────────────────────────────────

    async def _get_tenant_from_db(
        self,
        tenant_id: str,
        *,
        db: Any = None,
    ) -> TenantContext | None:
        """Fetch a single tenant from PostgreSQL and return a TenantContext.

        Returns ``None`` if no DB factory is available or the tenant is not found.
        """
        db_session = db or self._db
        if db_session is None:
            return None
        try:
            from sqlalchemy import select

            from app.db.models.tenant import Tenant

            async with db_session() as session:
                result = await session.execute(
                    select(Tenant).where(
                        Tenant.id == tenant_id,
                        Tenant.is_active == True,  # noqa: E712
                    )
                )
                t = result.scalar_one_or_none()
                if t is None:
                    return None
                return TenantContext(
                    tenant_id=t.id,
                    plan=PlanTier(t.plan_tier),
                    api_key_id="",
                    roles=(),
                )
        except Exception as exc:
            logging.getLogger(__name__).warning("_get_tenant_from_db failed: %s", exc)
            return None

    async def get_tenant_cached(
        self,
        tenant_id: str,
        redis: Any = None,
        db: Any = None,
    ) -> TenantContext | None:
        """Get tenant context with Redis read-through cache (TTL 5 minutes).

        Lookup order: Redis → PostgreSQL → in-memory dict.
        A successful DB fetch is written back to Redis automatically.
        """
        import json as _json

        cache_key = f"tenant:{tenant_id}"

        # ── L1: Redis cache hit ────────────────────────────────────────────
        if redis is not None:
            try:
                raw = await redis.get(cache_key)
                if raw:
                    data = _json.loads(raw)
                    return TenantContext(
                        tenant_id=data["tenant_id"],
                        plan=PlanTier(data.get("plan", "free")),
                        api_key_id=data.get("api_key_id", ""),
                        roles=tuple(data.get("roles", [])),
                    )
            except Exception:
                pass  # Redis errors are non-fatal — fall through

        # ── L2: DB fetch + populate cache ─────────────────────────────────
        if db is not None:
            try:
                tenant = await self._get_tenant_from_db(tenant_id, db=db)
                if tenant is not None and redis is not None:
                    with suppress(Exception):
                        await redis.set(
                            cache_key,
                            _json.dumps(
                                {
                                    "tenant_id": tenant.tenant_id,
                                    "plan": tenant.plan.value,
                                    "api_key_id": tenant.api_key_id,
                                    "roles": list(tenant.roles),
                                }
                            ),
                            ex=300,  # 5-minute TTL
                        )
                return tenant
            except Exception:
                pass  # DB errors fall through to in-memory

        # ── L3: In-memory fallback ─────────────────────────────────────────
        raw_dict = self._tenants.get(tenant_id)
        if raw_dict is None:
            return None
        try:
            return TenantContext(
                tenant_id=raw_dict["tenant_id"],
                plan=PlanTier(raw_dict.get("plan", "free")),
                api_key_id=raw_dict.get("api_key_id", ""),
                roles=tuple(raw_dict.get("roles", [])),
            )
        except Exception:
            return None

    async def invalidate_tenant_cache(self, tenant_id: str, redis: Any = None) -> None:
        """Invalidate cached tenant data after any mutation.

        Removes the ``tenant:{tenant_id}`` key from Redis only.
        The in-memory ``self._tenants`` dict is the source of truth when there
        is no DB — it must NOT be cleared here, otherwise key lookups return 401.
        Only clear from Redis (the read-through cache layer).
        """
        if redis is not None:
            with suppress(Exception):
                await redis.delete(f"tenant:{tenant_id}")
        # NOTE: Do NOT pop from self._tenants — that dict is the authoritative
        # in-memory store (used by resolve_api_key). Only Redis is a cache.
