"""
Redis client factory with Sentinel and Cluster support.

Priority order:
1. REDIS_SENTINEL_URLS set → Redis Sentinel (HA primary/replica failover)
2. REDIS_CLUSTER_NODES set → Redis Cluster (horizontal sharding + HA)
3. REDIS_URL set           → single-node (default, dev/simple prod)

URL formats:
  Sentinel: REDIS_SENTINEL_URLS=host1:26379,host2:26379,host3:26379
            REDIS_SENTINEL_MASTER=mymaster  (default: "mymaster")
            REDIS_SENTINEL_DB=0
  Cluster:  REDIS_CLUSTER_NODES=host1:6379,host2:6379,host3:6379
  Single:   REDIS_URL=redis://host:6379/0  (or rediss:// for TLS)
"""
from __future__ import annotations

import os
from typing import Any


def _parse_host_port_list(csv: str, default_port: int) -> list[tuple[str, int]]:
    """Parse 'host1:port1,host2:port2' into [(host, port), ...] pairs."""
    nodes: list[tuple[str, int]] = []
    for entry in csv.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            host, port_str = entry.rsplit(":", 1)
            nodes.append((host.strip(), int(port_str.strip())))
        else:
            nodes.append((entry, default_port))
    return nodes


def make_async_redis(
    sentinel_urls: str = "",
    sentinel_master: str = "mymaster",
    cluster_nodes: str = "",
    redis_url: str = "redis://localhost:6379/0",
    **kwargs: Any,
) -> Any:
    """Return a ``redis.asyncio`` client for the best available topology.

    Sentinel takes precedence over Cluster which takes precedence over
    single-node.  ``**kwargs`` are forwarded to the underlying client
    constructor (e.g. ``decode_responses=True``, ``socket_timeout=5``).
    """
    import redis.asyncio as aioredis

    if sentinel_urls:
        sentinels = _parse_host_port_list(sentinel_urls, 26379)
        db = int(os.getenv("REDIS_SENTINEL_DB", "0"))
        password = os.getenv("REDIS_SENTINEL_PASSWORD") or os.getenv("REDIS_PASSWORD") or None
        sentinel = aioredis.Sentinel(
            sentinels,
            sentinel_kwargs={"password": password} if password else {},
        )
        return sentinel.master_for(
            sentinel_master,
            db=db,
            password=password,
            **kwargs,
        )

    if cluster_nodes:
        try:
            from redis.asyncio.cluster import ClusterNode, RedisCluster  # type: ignore[import]

            startup_nodes = [
                ClusterNode(h, p)
                for h, p in _parse_host_port_list(cluster_nodes, 6379)
            ]
            password = os.getenv("REDIS_CLUSTER_PASSWORD") or os.getenv("REDIS_PASSWORD") or None
            return RedisCluster(
                startup_nodes=startup_nodes,
                decode_responses=kwargs.pop("decode_responses", False),
                password=password,
                **kwargs,
            )
        except ImportError:
            # redis[hiredis] cluster extras not installed — fall through to single-node
            pass

    return aioredis.from_url(redis_url, **kwargs)


def make_sync_redis(
    sentinel_urls: str = "",
    sentinel_master: str = "mymaster",
    cluster_nodes: str = "",
    redis_url: str = "redis://localhost:6379/0",
    **kwargs: Any,
) -> Any:
    """Same as :func:`make_async_redis` but returns a synchronous redis client."""
    import redis as syncredis

    if sentinel_urls:
        sentinels = _parse_host_port_list(sentinel_urls, 26379)
        db = int(os.getenv("REDIS_SENTINEL_DB", "0"))
        password = os.getenv("REDIS_SENTINEL_PASSWORD") or os.getenv("REDIS_PASSWORD") or None
        sentinel = syncredis.Sentinel(
            sentinels,
            sentinel_kwargs={"password": password} if password else {},
        )
        return sentinel.master_for(sentinel_master, db=db, password=password, **kwargs)

    if cluster_nodes:
        try:
            from redis.cluster import ClusterNode, RedisCluster  # type: ignore[import]

            startup_nodes = [
                ClusterNode(h, p)
                for h, p in _parse_host_port_list(cluster_nodes, 6379)
            ]
            password = os.getenv("REDIS_CLUSTER_PASSWORD") or os.getenv("REDIS_PASSWORD") or None
            return RedisCluster(startup_nodes=startup_nodes, password=password, **kwargs)
        except ImportError:
            pass

    return syncredis.from_url(redis_url, **kwargs)


def get_redis_kwargs() -> dict[str, str]:
    """Read HA topology settings from environment variables.

    The returned dict can be unpacked directly into :func:`make_async_redis`
    or :func:`make_sync_redis`::

        client = make_async_redis(**get_redis_kwargs(), decode_responses=True)
    """
    return {
        "sentinel_urls": os.getenv("REDIS_SENTINEL_URLS", ""),
        "sentinel_master": os.getenv("REDIS_SENTINEL_MASTER", "mymaster"),
        "cluster_nodes": os.getenv("REDIS_CLUSTER_NODES", ""),
        "redis_url": os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    }
