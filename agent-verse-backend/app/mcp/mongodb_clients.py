"""Pooled MongoClients for the MongoDB MCP builtin (C2).

Every tool call used to build a ``MongoClient`` — egress check, TCP, TLS, SCRAM,
monitor threads — and close it again. Clients are now cached per
``(tenant_id, server_id, credential fingerprint)``:

* bounded: at most ``MONGODB_CLIENT_CACHE_SIZE`` clients per process, least
  recently used evicted first;
* ``MONGODB_CLIENT_IDLE_TTL_S`` idle / ``MONGODB_CLIENT_MAX_AGE_S`` total
  lifetime: an expired client is closed (and its hosts re-checked by the egress
  policy when it is rebuilt);
* a connector update or delete closes its clients (:func:`evict`); a new
  credential fingerprint for a connector closes the old one; a connection
  failure evicts the client that failed.

Each entry owns the egress pins of the hosts checked when it was built (released
when it closes: the driver's monitor threads resolve those names for the
client's whole life) and its TLS material directory. An entry evicted while a
call is running is closed when that call releases it.

Multi-replica: the cache is process-local by design (a client is a socket pool).
The credential fingerprint is part of the key, so a replica that missed an
update never reuses a client for the OLD credentials; it ages out by TTL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)

Key = tuple[str, str, str]


def _now() -> float:
    return time.monotonic()


def fingerprint(uri: str, credentials: dict[str, Any] | None) -> str:
    """Stable digest of everything that shapes the client (never logged raw)."""
    material = json.dumps(
        {"uri": uri, "credentials": credentials or {}}, sort_keys=True, default=str
    )
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass
class Entry:
    key: Key
    client: Any
    closer: Callable[[], None]
    dsn: str = ""
    created: float = 0.0
    last_used: float = 0.0
    in_use: int = 0
    doomed: bool = False
    closed: bool = False


_lock = threading.Lock()
_entries: OrderedDict[Key, Entry] = OrderedDict()


def _limits() -> tuple[int, float, float]:
    from app.core.config import get_settings

    settings = get_settings()
    return (
        max(1, int(settings.mongodb_client_cache_size)),
        max(1.0, float(settings.mongodb_client_idle_ttl_s)),
        max(1.0, float(settings.mongodb_client_max_age_s)),
    )


def _close(entry: Entry) -> None:
    """Close the client and release its resources (outside the lock)."""
    if entry.closed:
        return
    entry.closed = True
    try:
        entry.client.close()
    except Exception as exc:  # closing must never fail a call
        _log.warning("mongodb_client_close_failed error=%s", type(exc).__name__)
    try:
        entry.closer()
    except Exception as exc:
        _log.warning("mongodb_client_cleanup_failed error=%s", type(exc).__name__)


def _drop_locked(entry: Entry, doomed: list[Entry]) -> None:
    """Remove ``entry`` from the cache; close it now, or when its call ends."""
    if _entries.get(entry.key) is entry:
        del _entries[entry.key]
    entry.doomed = True
    if entry.in_use == 0:
        doomed.append(entry)


def _expired(entry: Entry, now: float, idle_ttl: float, max_age: float) -> bool:
    return now - entry.last_used > idle_ttl or now - entry.created > max_age


def _sweep_locked(now: float, doomed: list[Entry]) -> None:
    _, idle_ttl, max_age = _limits()
    for entry in list(_entries.values()):
        if entry.in_use == 0 and _expired(entry, now, idle_ttl, max_age):
            _drop_locked(entry, doomed)


def acquire(key: Key) -> Entry | None:
    """The live cached client for ``key`` (marked in use), or None."""
    doomed: list[Entry] = []
    with _lock:
        now = _now()
        _sweep_locked(now, doomed)
        entry = _entries.get(key)
        _, idle_ttl, max_age = _limits()
        if entry is not None and _expired(entry, now, idle_ttl, max_age):
            _drop_locked(entry, doomed)
            entry = None
        if entry is not None:
            entry.in_use += 1
            entry.last_used = now
            _entries.move_to_end(key)
    for stale in doomed:
        _close(stale)
    return entry


def insert(key: Key, client: Any, closer: Callable[[], None], *, dsn: str = "") -> Entry:
    """Cache a freshly built client and return it acquired (in use).

    A concurrent builder that won the race keeps its entry: ours is closed and
    theirs returned. Other fingerprints of the same (tenant, connector) — its
    previous credentials — are dropped, and the LRU bound is enforced.
    """
    now = _now()
    fresh = Entry(
        key=key, client=client, closer=closer, dsn=dsn, created=now, last_used=now, in_use=1
    )
    doomed: list[Entry] = []
    winner = fresh
    with _lock:
        existing = _entries.get(key)
        if existing is not None and not existing.doomed:
            existing.in_use += 1
            existing.last_used = _now()
            winner = existing
            doomed.append(fresh)
        else:
            tenant_id, server_id, _ = key
            if tenant_id or server_id:
                for other in list(_entries.values()):
                    if other.key[:2] == (tenant_id, server_id) and other.key != key:
                        _drop_locked(other, doomed)
            _entries[key] = fresh
            max_size, _, _ = _limits()
            while len(_entries) > max_size:
                oldest = next(iter(_entries.values()))
                _drop_locked(oldest, doomed)
    for stale in doomed:
        _close(stale)
    return winner


def release(entry: Entry) -> None:
    """A call finished with ``entry``; close it if it was evicted meanwhile."""
    close_now = False
    with _lock:
        entry.in_use = max(0, entry.in_use - 1)
        entry.last_used = _now()
        close_now = entry.doomed and entry.in_use == 0
    if close_now:
        _close(entry)


def discard(entry: Entry) -> None:
    """Evict ``entry`` (its connection failed); closed once no call uses it."""
    doomed: list[Entry] = []
    with _lock:
        _drop_locked(entry, doomed)
    for stale in doomed:
        _close(stale)


def evict(tenant_id: str, server_id: str) -> int:
    """Close every cached client of one connector (connector updated / deleted)."""
    doomed: list[Entry] = []
    count = 0
    with _lock:
        for entry in list(_entries.values()):
            if entry.key[:2] == (tenant_id, server_id):
                _drop_locked(entry, doomed)
                count += 1
    for stale in doomed:
        _close(stale)
    return count


def close_all() -> None:
    """Close every cached client (shutdown, tests)."""
    doomed: list[Entry] = []
    with _lock:
        for entry in list(_entries.values()):
            _drop_locked(entry, doomed)
    for stale in doomed:
        _close(stale)


def size() -> int:
    with _lock:
        return len(_entries)


__all__ = [
    "Entry",
    "acquire",
    "close_all",
    "discard",
    "evict",
    "fingerprint",
    "insert",
    "release",
    "size",
]
