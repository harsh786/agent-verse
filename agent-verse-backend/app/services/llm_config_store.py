"""Per-tenant LLM provider configuration (BYOK): Postgres + Redis cache.

Postgres (``tenant_llm_configs``, migration e8f9a0b1c2d3) is the source of
truth; Redis is a read-through cache so the hot path (every goal resolves its
provider) and Celery workers (no FastAPI ``app.state``) avoid a DB round trip.

It used to be Redis-only, with the API replica also keeping a copy in its own
``app.state._llm_configs``: the goal path read that per-replica dict, so a
tenant's configured provider applied only on the replica that handled the PUT,
and a Redis flush or eviction silently dropped every tenant's key.

Key format in Redis: ``llm_config:{tenant_id}`` → JSON with provider,
encrypted_key, model, base_url, masked_key. *encrypted_key* is the vault
ciphertext — the raw key is never written anywhere.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300


class LLMConfigPersistError(RuntimeError):
    """The durable write failed; the configuration was NOT saved."""


class LLMConfigReadError(RuntimeError):
    """The durable read failed: whether the tenant configured BYOK is unknown."""


class LLMConfigStore:
    """Reads and writes per-tenant LLM provider config.

    Args:
        redis_client: ``redis.asyncio.Redis``-compatible async client (cache), or None.
        db_factory: async session factory (application role, tenant RLS), or None.
    """

    def __init__(self, redis_client: Any = None, db_factory: Any = None) -> None:
        self._redis = redis_client
        self._db = db_factory

    def set_db(self, db_factory: Any) -> None:
        self._db = db_factory

    def _key(self, tenant_id: str) -> str:
        return f"llm_config:{tenant_id}"

    async def set_config(
        self,
        tenant_id: str,
        provider: str,
        encrypted_key: str,
        model: str,
        base_url: str | None = None,
        masked_key: str | None = None,
    ) -> None:
        """Store the config for *tenant_id*. Raises LLMConfigPersistError if the
        durable write fails (never report a key as saved that was not)."""
        config = {
            "provider": provider,
            "encrypted_key": encrypted_key,
            "model": model,
            "base_url": base_url,
            "masked_key": masked_key,
        }
        if self._db is not None:
            try:
                await self._db_upsert(tenant_id, config)
            except Exception as exc:
                logger.warning("llm_config_db_write_failed tenant=%s: %s", tenant_id, exc)
                raise LLMConfigPersistError(str(exc)) from exc
        await self._cache_set(tenant_id, config)

    async def get_config(self, tenant_id: str, *, strict: bool = False) -> dict[str, Any] | None:
        """Return the config for *tenant_id*, or None if not configured.

        ``strict=True`` raises LLMConfigReadError when the durable read fails, so a
        caller (the goal path) can tell "no BYOK" from "unknown" — treating a DB
        error as "not configured" silently ran the tenant's goal on the platform
        provider, at platform cost.
        """
        cached = await self._cache_get(tenant_id)
        if cached is not None:
            return cached
        if self._db is None:
            return None
        try:
            config = await self._db_get(tenant_id)
        except Exception as exc:
            logger.warning("llm_config_db_read_failed tenant=%s: %s", tenant_id, exc)
            if strict:
                raise LLMConfigReadError(str(exc)) from exc
            return None
        if config is not None:
            await self._cache_set(tenant_id, config)
        return config

    async def delete_config(self, tenant_id: str) -> None:
        if self._db is not None:
            try:
                await self._db_delete(tenant_id)
            except Exception as exc:
                logger.warning("llm_config_db_delete_failed tenant=%s: %s", tenant_id, exc)
                raise LLMConfigPersistError(str(exc)) from exc
        if self._redis is not None:
            try:
                await self._redis.delete(self._key(tenant_id))
            except Exception as exc:
                logger.warning("Failed to delete LLM config from Redis for %s: %s", tenant_id, exc)

    # ── cache ──────────────────────────────────────────────────────────────

    async def _cache_get(self, tenant_id: str) -> dict[str, Any] | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._key(tenant_id))
            return None if raw is None else dict(json.loads(raw))
        except Exception as exc:
            logger.warning("Failed to read LLM config from Redis for %s: %s", tenant_id, exc)
            return None

    async def _cache_set(self, tenant_id: str, config: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            # With a DB behind it the cache entry expires; Redis-only keeps it.
            ttl = _CACHE_TTL_SECONDS if self._db is not None else None
            await self._redis.set(self._key(tenant_id), json.dumps(config), ex=ttl)
        except Exception as exc:
            logger.warning("Failed to store LLM config in Redis for %s: %s", tenant_id, exc)

    # ── database (tenant RLS) ──────────────────────────────────────────────

    async def _db_upsert(self, tenant_id: str, config: dict[str, Any]) -> None:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text(
                    "INSERT INTO tenant_llm_configs "
                    "(tenant_id, provider, encrypted_key, model, base_url, masked_key, updated_at) "
                    "VALUES (:t, :p, :k, :m, :b, :mk, NOW()) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET provider = EXCLUDED.provider, "
                    "encrypted_key = EXCLUDED.encrypted_key, model = EXCLUDED.model, "
                    "base_url = EXCLUDED.base_url, masked_key = EXCLUDED.masked_key, "
                    "updated_at = NOW()"
                ),
                {
                    "t": tenant_id,
                    "p": config["provider"],
                    "k": config["encrypted_key"],
                    "m": config["model"] or "",
                    "b": config["base_url"],
                    "mk": config["masked_key"],
                },
            )

    async def _db_get(self, tenant_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with self._db() as session, sqlalchemy_rls_context(session, tenant_id):
            row = (
                await session.execute(
                    text(
                        "SELECT provider, encrypted_key, model, base_url, masked_key "
                        "FROM tenant_llm_configs WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                )
            ).fetchone()
        if row is None:
            return None
        return {
            "provider": row[0],
            "encrypted_key": row[1],
            "model": row[2],
            "base_url": row[3],
            "masked_key": row[4],
        }

    async def _db_delete(self, tenant_id: str) -> None:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text("DELETE FROM tenant_llm_configs WHERE tenant_id = :t"), {"t": tenant_id}
            )


# ── Module-level singleton ─────────────────────────────────────────────────────
# Set by ``create_app()`` / its lifespan. Workers build their own on first use
# (``get_or_create_worker_llm_config_store``).

_llm_config_store: LLMConfigStore | None = None


def get_llm_config_store() -> LLMConfigStore | None:
    """Return the process-wide LLM config store, or *None* if not yet wired."""
    return _llm_config_store


def set_llm_config_store(store: LLMConfigStore) -> None:
    """Wire the process-wide singleton (called once from ``create_app``)."""
    global _llm_config_store
    _llm_config_store = store


def get_or_create_worker_llm_config_store() -> LLMConfigStore | None:
    """The store for a Celery worker process, which has no app lifespan.

    Without this every worker saw ``None`` and ignored the tenant's own
    provider. DB-backed (application role + tenant RLS); no Redis cache,
    because workers run each task on a fresh event loop and an async Redis
    client is bound to the loop that created it.
    """
    if _llm_config_store is not None:
        return _llm_config_store
    try:
        from app.db.session import get_session_factory

        return LLMConfigStore(redis_client=None, db_factory=get_session_factory())
    except Exception as exc:
        logger.warning("worker_llm_config_store_unavailable: %s", exc)
        return None


async def aget_llm_api_key_for_tenant(tenant_id: str) -> str:
    """Decrypted BYOK API key for *tenant_id* ('' when none is configured).

    Used to hand a scoped key to an isolated execution environment. The worker
    imported a sync ``get_llm_api_key_for_tenant`` that never existed; the
    ImportError was swallowed, so isolated runs never received the tenant's key.
    """
    store = get_or_create_worker_llm_config_store()
    if store is None:
        # Unknown whether the tenant has BYOK: never hand out the platform key.
        raise LLMConfigReadError("the tenant LLM config store is unavailable")
    # Strict: a DB error must not read as "no BYOK" ('' → platform key).
    config = await store.get_config(tenant_id, strict=True)
    encrypted = str((config or {}).get("encrypted_key") or "")
    if not encrypted:
        return ""
    from app.db.session import get_session_factory
    from app.providers.tenant_vault import decrypt_tenant_secret

    return await decrypt_tenant_secret(get_session_factory(), tenant_id, encrypted)
