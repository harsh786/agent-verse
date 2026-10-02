"""Per-tenant embedding usage, shared across replicas and processes (Redis).

``GET /embeddings/usage`` used to report only ``POST /embeddings`` calls made on
the serving replica since it started; ingestion and query embeds were never
counted. Every embed path now calls :func:`record_embedding_usage`, which adds
the tenant's (approximate, word-count) tokens to ``emb:usage:<tenant>`` — a
Redis hash keyed by model — so every replica and the Celery worker count into
the same place.

The API lifespan configures one client for its long-lived loop
(:func:`configure_usage_redis`). A Celery worker runs every task on its own
fresh loop, and a ``redis.asyncio`` client is bound to the loop it first ran on,
so the worker only *enables* env-configured counting
(:func:`configure_usage_redis_from_env`): a client is built per task loop and
closed with that loop (``app.db.session.on_loop_teardown``). Without either,
usage is not recorded here (the embedding router still keeps its per-process
counters).
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

_PREFIX = "emb:usage:"
_TTL_SECONDS = 90 * 24 * 3600  # rolling retention of the counters

_redis: Any = None
# Worker mode: build a client per running loop from the environment.
_from_env = False
_env_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
    weakref.WeakKeyDictionary()
)


def configure_usage_redis(client: Any) -> None:
    """Set (or with ``None`` clear) this process's usage Redis client."""
    global _redis, _from_env
    _redis = client
    _from_env = False


def _make_env_client() -> Any:
    from app.net.redis_factory import get_redis_kwargs, make_async_redis

    return make_async_redis(**get_redis_kwargs(), socket_timeout=5)


def _client(explicit: Any) -> Any:
    """The explicit, process-configured, or this loop's env-configured client."""
    if explicit is not None:
        return explicit
    if _redis is not None or not _from_env:
        return _redis
    loop = asyncio.get_running_loop()
    client = _env_clients.get(loop)
    if client is None:
        from app.db.session import on_loop_teardown

        client = _make_env_client()
        _env_clients[loop] = client

        async def _close() -> None:
            _env_clients.pop(loop, None)
            await client.aclose()

        on_loop_teardown(_close)
    return client


def approx_tokens(texts: list[str]) -> int:
    return sum(len(str(text).split()) for text in texts)


async def record_embedding_usage(
    tenant_id: str, model: str, tokens: int, *, redis: Any = None
) -> None:
    """Best-effort: add ``tokens`` to the tenant's shared counter for ``model``."""
    if not tenant_id or tokens <= 0:
        return
    client = _client(redis)
    if client is None:
        return
    key = f"{_PREFIX}{tenant_id}"
    try:
        await client.hincrby(key, model or "default", int(tokens))
        await client.expire(key, _TTL_SECONDS)
    except Exception as exc:
        _log.warning("embedding_usage_record_failed", tenant=tenant_id, error=str(exc)[:200])


async def read_embedding_usage(tenant_id: str, *, redis: Any = None) -> dict[str, int] | None:
    """The tenant's shared counters by model, or None when not configured / unreadable."""
    client = _client(redis)
    if client is None:
        return None
    try:
        raw = await client.hgetall(f"{_PREFIX}{tenant_id}")
    except Exception as exc:
        _log.warning("embedding_usage_read_failed", tenant=tenant_id, error=str(exc)[:200])
        return None
    usage: dict[str, int] = {}
    for field, value in (raw or {}).items():
        name = field.decode() if isinstance(field, bytes) else str(field)
        usage[name] = int(value.decode() if isinstance(value, bytes) else value)
    return usage


def configure_usage_redis_from_env() -> None:
    """Worker helper: count via REDIS_URL / sentinel / cluster env, if any.

    No client is built here: one is built per task loop on first use and closed
    with that loop, so none is ever reused on a later (or closed) loop.
    """
    import os

    global _from_env
    if _redis is not None or not any(
        os.getenv(name) for name in ("REDIS_URL", "REDIS_SENTINEL_URLS", "REDIS_CLUSTER_NODES")
    ):
        return
    _from_env = True
