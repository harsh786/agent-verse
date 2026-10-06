"""Fleet-wide provider circuit state in Redis (a01-F023-02).

:class:`~app.providers.circuit_breaker.ProviderCircuitBreaker` keeps its state in
process memory, so every API replica and every Celery worker child learned about
a failing model on its own: with N processes a dead endpoint took N x threshold
failed calls (each one a full call timeout) before the fleet stopped calling it,
and every process ran its own half-open probe when the cool-down ended.

This module shares the same circuit, keyed by the same breaker key (vendor /
model, plus the tenant scope for BYOK providers), across processes:

* ``agentverse:provider_cb:<key>`` — a hash with ``failures`` (consecutive
  failures reported by any process) and ``opened_at`` (epoch seconds, wall clock
  so every host can compare it). The hash self-expires (``_state_ttl``).
* ``agentverse:provider_cb:<key>:probe`` — the ONE fleet-wide half-open probe
  (``SET NX EX recovery_timeout``): a probe whose outcome never arrives (the
  worker died) frees its slot when the key expires.

A call is refused while ``opened_at`` is younger than the recovery timeout. After
it, exactly one caller in the fleet wins the probe slot; everyone else is refused
until the probe reports. A success anywhere closes the circuit everywhere; a
failed probe re-opens it.

The local breaker still runs alongside (a Redis outage leaves each process with
its own protection), so this layer fails open: a Redis error never refuses a
call and never raises into the provider path — it is logged and the local
decision stands.

The API lifespan configures one client (:func:`configure_shared_circuit_redis`).
A Celery worker runs every task on a fresh event loop and a ``redis.asyncio``
client is bound to the loop it first ran on, so the worker only *enables*
env-configured sharing (:func:`configure_shared_circuit_redis_from_env`): a
client is built per task loop and closed with that loop.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
import weakref
from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_PREFIX = "agentverse:provider_cb:"
_STATE_TTL_FLOOR_S = 600
# Redis is consulted on every LLM call: never let a slow Redis stall one.
_SOCKET_TIMEOUT_S = 1.0
_WARN_EVERY_S = 30.0

_redis: Any = None
_from_env = False
_env_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
    weakref.WeakKeyDictionary()
)
_last_warning = 0.0


@dataclass(frozen=True, slots=True)
class SharedVerdict:
    """The fleet's view of one circuit for one call."""

    open: bool
    # Set when this call won the fleet-wide half-open probe slot.
    probe_token: str | None = None


def configure_shared_circuit_redis(client: Any) -> None:
    """Set (or with ``None`` clear) this process's shared-circuit Redis client."""
    global _redis, _from_env
    _redis = client
    _from_env = False


def configure_shared_circuit_redis_from_env() -> None:
    """Worker helper: share circuits via REDIS_URL / sentinel / cluster env, if any.

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


def shared_circuit_enabled() -> bool:
    return _redis is not None or _from_env


def _make_env_client() -> Any:
    from app.net.redis_factory import get_redis_kwargs, make_async_redis

    return make_async_redis(
        **get_redis_kwargs(),
        socket_timeout=_SOCKET_TIMEOUT_S,
        socket_connect_timeout=_SOCKET_TIMEOUT_S,
    )


def _client() -> Any:
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


def _state_key(key: str) -> str:
    return f"{_PREFIX}{key}"


def _probe_key(key: str) -> str:
    return f"{_PREFIX}{key}:probe"


def _state_ttl(recovery_timeout: float) -> int:
    return max(_STATE_TTL_FLOOR_S, math.ceil(recovery_timeout * 10))


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _warn(event: str, key: str, exc: BaseException) -> None:
    """Rate-limited warning: a Redis outage must not flood the log per LLM call."""
    global _last_warning
    now = time.monotonic()
    if now - _last_warning < _WARN_EVERY_S:
        return
    _last_warning = now
    logger.warning(event, circuit=key, error=f"{type(exc).__name__}: {str(exc)[:200]}")


async def _opened_at(client: Any, key: str) -> float | None:
    raw = await client.hget(_state_key(key), "opened_at")
    if raw is None:
        return None
    try:
        return float(_text(raw))
    except ValueError:
        return None


async def shared_is_open(key: str, *, recovery_timeout: float) -> bool:
    """True while the fleet holds the circuit open (no probe slot is taken)."""
    client = _client()
    if client is None:
        return False
    try:
        opened = await _opened_at(client, key)
        if opened is None:
            return False
        if time.time() - opened < recovery_timeout:
            return True
        # Half-open: open for everyone except the holder of the probe slot.
        return bool(await client.exists(_probe_key(key)))
    except Exception as exc:
        _warn("provider_circuit_shared_read_failed", key, exc)
        return False


async def shared_admit(key: str, *, recovery_timeout: float) -> SharedVerdict:
    """Admit one call against the fleet circuit (taking the probe slot if half-open)."""
    client = _client()
    if client is None:
        return SharedVerdict(open=False)
    try:
        opened = await _opened_at(client, key)
        if opened is None:
            return SharedVerdict(open=False)
        if time.time() - opened < recovery_timeout:
            return SharedVerdict(open=True)
        token = uuid.uuid4().hex
        won = await client.set(
            _probe_key(key), token, nx=True, ex=max(1, math.ceil(recovery_timeout))
        )
        if won:
            return SharedVerdict(open=False, probe_token=token)
        return SharedVerdict(open=True)
    except Exception as exc:
        _warn("provider_circuit_shared_read_failed", key, exc)
        return SharedVerdict(open=False)


async def _release(client: Any, key: str, token: str) -> None:
    current = await client.get(_probe_key(key))
    if current is not None and _text(current) == token:
        await client.delete(_probe_key(key))


async def shared_record_success(key: str, *, probe_token: str | None = None) -> None:
    """The provider answered: close the circuit for the whole fleet."""
    client = _client()
    if client is None:
        return
    try:
        await client.delete(_state_key(key))
        if probe_token is not None:
            await _release(client, key, probe_token)
    except Exception as exc:
        _warn("provider_circuit_shared_write_failed", key, exc)


async def shared_record_failure(
    key: str,
    *,
    failure_threshold: int,
    recovery_timeout: float,
    probe_token: str | None = None,
) -> None:
    """Count one provider failure fleet-wide; open the circuit at the threshold.

    A failed half-open probe re-opens it at once (a new cool-down for everyone).
    """
    client = _client()
    if client is None:
        return
    state = _state_key(key)
    ttl = _state_ttl(recovery_timeout)
    try:
        failures = int(await client.hincrby(state, "failures", 1))
        await client.expire(state, ttl)
        if probe_token is not None or failures >= failure_threshold:
            await client.hset(state, "opened_at", repr(time.time()))
            await client.expire(state, ttl)
            logger.warning(
                "provider_circuit_opened_fleet_wide",
                circuit=key,
                failures=failures,
                probe_failed=probe_token is not None,
            )
        if probe_token is not None:
            await _release(client, key, probe_token)
    except Exception as exc:
        _warn("provider_circuit_shared_write_failed", key, exc)


async def shared_release_probe(key: str, *, probe_token: str | None) -> None:
    """The probe ended without a provider verdict: free the fleet's probe slot."""
    if probe_token is None:
        return
    client = _client()
    if client is None:
        return
    try:
        await _release(client, key, probe_token)
    except Exception as exc:
        _warn("provider_circuit_shared_write_failed", key, exc)


__all__ = [
    "SharedVerdict",
    "configure_shared_circuit_redis",
    "configure_shared_circuit_redis_from_env",
    "shared_admit",
    "shared_circuit_enabled",
    "shared_is_open",
    "shared_record_failure",
    "shared_record_success",
    "shared_release_probe",
]
