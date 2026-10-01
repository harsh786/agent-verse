"""Durable, tenant-managed messaging-gateway bindings (TRG-42).

The gateway's ``(channel, addressee) -> tenant`` bindings lived only in a
per-process :class:`~app.gateway.channel_registry.ChannelRegistry` seeded from
the ``CHANNEL_TENANT_MAP`` env var: tenants could not manage them, every replica
had to be seeded identically, and a change needed a redeploy.

Bindings now live in ``channel_tenant_mappings`` — the same verified,
one-tenant-per-channel table the inbound channel routes use. A binding is a
ROUTABLE mapping row (``verified`` / ``legacy_unverified``) whose
``channel_config`` carries the gateway settings:

* ``secret_enc`` — the binding's inbound credential, vault-encrypted (Telegram
  ``secret_token``, WhatsApp app secret, generic-webhook HMAC key, the tenant's
  Slack app signing secret);
* ``outbound_token_enc`` — the vault-encrypted token used to send replies;
* ``app_id`` — the tenant's Bot Framework app id (Teams; the JWT audience);
* ``org_id``.

Resolution is cross-tenant by nature (the webhook names only the addressee), so
it reads through the maintenance (BYPASSRLS) factory. Every replica caches
resolved bindings briefly; a write bumps a Redis version key so other replicas
drop their cache on the next message (without Redis the cache TTL bounds the
staleness). The env registry remains an operator-level fallback only.
"""

from __future__ import annotations

import time
from typing import Any

from app.gateway.channel_registry import ChannelBinding, ChannelRegistry
from app.observability.logging import get_logger

_log = get_logger(__name__)

VERSION_KEY = "gateway:channel_bindings:version"
_CACHE_TTL_SECONDS = 30.0
_NEGATIVE_TTL_SECONDS = 5.0
_ROUTABLE = ("verified", "legacy_unverified")
# Channels the gateway /chat routes can bind.
GATEWAY_CHANNELS = ("telegram", "whatsapp", "webhook", "slack", "teams")


def _norm(channel: str, addressee: str) -> tuple[str, str]:
    return channel.strip().lower(), str(addressee).strip()


def _decrypt(value: Any) -> str:
    if not value:
        return ""
    from app.providers.vault import get_vault

    return get_vault().decrypt(str(value))


def encrypt_secret(value: str) -> str:
    from app.providers.vault import get_vault

    return get_vault().encrypt(value) if value else ""


class ChannelBindingStoreUnavailableError(RuntimeError):
    """The binding table could not be read (the request is refused, not guessed)."""


class ChannelBindingStore:
    """DB-backed binding resolution with a version-invalidated per-replica cache.

    Reads ``system_db_session_factory`` / ``redis`` from ``state`` at call time,
    so the lifespan's DB/Redis wiring takes effect without rebuilding the store.
    """

    def __init__(self, state: Any, env_registry: ChannelRegistry | None = None) -> None:
        self._state = state
        self._env = env_registry
        # (channel, addressee) -> (binding or None, cached_at, version)
        self._cache: dict[tuple[str, str], tuple[ChannelBinding | None, float, str]] = {}

    # ── wiring ────────────────────────────────────────────────────────────
    def _system_db(self) -> Any:
        return getattr(self._state, "system_db_session_factory", None)

    def _redis(self) -> Any:
        # The lifespan's shared async client (real Redis once wired).
        state = self._state
        return getattr(state, "_rate_limiter_redis", None) or getattr(state, "_redis", None)

    async def _version(self) -> str:
        redis = self._redis()
        if redis is None:
            return ""
        try:
            raw = await redis.get(VERSION_KEY)
        except Exception:
            return ""  # TTL still bounds staleness
        if isinstance(raw, bytes):
            raw = raw.decode()
        return str(raw or "0")

    async def invalidate(self) -> None:
        """Drop this replica's cache and tell the others (Redis version bump)."""
        self._cache.clear()
        redis = self._redis()
        if redis is None:
            return
        try:
            await redis.incr(VERSION_KEY)
        except Exception as exc:
            _log.warning("gateway_binding_invalidate_failed", error=str(exc)[:200])

    # ── resolution ────────────────────────────────────────────────────────
    async def aresolve(self, channel: str, addressee: str) -> ChannelBinding | None:
        key = _norm(channel, addressee)
        if not key[1]:
            return None
        version = await self._version()
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached is not None:
            binding, at, ver = cached
            ttl = _CACHE_TTL_SECONDS if binding is not None else _NEGATIVE_TTL_SECONDS
            if ver == version and now - at < ttl:
                return binding
        binding = await self._load(*key)
        if binding is None and self._env is not None:
            binding = self._env.resolve(*key)
        self._cache[key] = (binding, now, version)
        return binding

    async def _load(self, channel: str, addressee: str) -> ChannelBinding | None:
        db = self._system_db()
        if db is None:
            return None
        from sqlalchemy import text

        from app.db.rls import system_session

        try:
            async with db() as session, session.begin(), system_session(session):
                row = (
                    await session.execute(
                        text(
                            "SELECT tenant_id, channel_config FROM channel_tenant_mappings "
                            "WHERE channel_type = :ct AND channel_id = :ci "
                            "AND status IN ('verified', 'legacy_unverified') AND enabled "
                            "LIMIT 1"
                        ),
                        {"ct": channel, "ci": addressee},
                    )
                ).first()
        except Exception as exc:
            raise ChannelBindingStoreUnavailableError(str(exc)[:200]) from exc
        if row is None:
            return None
        config = dict(row[1] or {})
        if not config.get("gateway"):
            return None  # an inbound-only mapping, not a gateway binding
        try:
            secret = _decrypt(config.get("secret_enc"))
            outbound = _decrypt(config.get("outbound_token_enc"))
        except Exception as exc:
            # Undecryptable (rotated master key, corrupt row): refuse, never
            # route unauthenticated.
            _log.warning("gateway_binding_secret_undecryptable", channel=channel, error=str(exc))
            secret, outbound = "", ""
        return ChannelBinding(
            tenant_id=str(row[0]),
            org_id=str(config.get("org_id") or ""),
            outbound_token=outbound,
            secret=secret,
            app_id=str(config.get("app_id") or ""),
        )


def get_binding_store(state: Any) -> ChannelBindingStore | None:
    store = getattr(state, "channel_binding_store", None)
    return store if isinstance(store, ChannelBindingStore) else None


async def resolve_binding(state: Any, channel: str, addressee: str) -> ChannelBinding | None:
    """DB binding first (multi-replica), then the operator env registry."""
    store = get_binding_store(state)
    if store is not None:
        return await store.aresolve(channel, addressee)
    registry = getattr(state, "channel_registry", None)
    return registry.resolve(channel, addressee) if registry is not None else None
