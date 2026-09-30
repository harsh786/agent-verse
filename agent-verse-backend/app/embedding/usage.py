"""Per-tenant embedding usage, shared across replicas and processes (Redis).

``GET /embeddings/usage`` used to report only ``POST /embeddings`` calls made on
the serving replica since it started; ingestion and query embeds were never
counted. Every embed path now calls :func:`record_embedding_usage`, which adds
the tenant's (approximate, word-count) tokens to ``emb:usage:<tenant>`` — a
Redis hash keyed by model — so every replica and the Celery worker count into
the same place.

The Redis client is configured per process (:func:`configure_usage_redis`): by
the API lifespan and by the worker. Without one, usage is not recorded here
(the embedding router still keeps its per-process counters).
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

_PREFIX = "emb:usage:"
_TTL_SECONDS = 90 * 24 * 3600  # rolling retention of the counters

_redis: Any = None


def configure_usage_redis(client: Any) -> None:
    """Set (or with ``None`` clear) this process's usage Redis client."""
    global _redis
    _redis = client


def approx_tokens(texts: list[str]) -> int:
    return sum(len(str(text).split()) for text in texts)


async def record_embedding_usage(
    tenant_id: str, model: str, tokens: int, *, redis: Any = None
) -> None:
    """Best-effort: add ``tokens`` to the tenant's shared counter for ``model``."""
    client = redis if redis is not None else _redis
    if client is None or not tenant_id or tokens <= 0:
        return
    key = f"{_PREFIX}{tenant_id}"
    try:
        await client.hincrby(key, model or "default", int(tokens))
        await client.expire(key, _TTL_SECONDS)
    except Exception as exc:
        _log.warning("embedding_usage_record_failed", tenant=tenant_id, error=str(exc)[:200])


async def read_embedding_usage(tenant_id: str, *, redis: Any = None) -> dict[str, int] | None:
    """The tenant's shared counters by model, or None when not configured / unreadable."""
    client = redis if redis is not None else _redis
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
    """Worker helper: configure from REDIS_URL / sentinel / cluster env, if any."""
    import os

    if _redis is not None or not any(
        os.getenv(name) for name in ("REDIS_URL", "REDIS_SENTINEL_URLS", "REDIS_CLUSTER_NODES")
    ):
        return
    from app.net.redis_factory import get_redis_kwargs, make_async_redis

    configure_usage_redis(make_async_redis(**get_redis_kwargs(), socket_timeout=5))
