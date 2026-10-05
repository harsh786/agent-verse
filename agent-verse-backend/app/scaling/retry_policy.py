"""Which worker failures are worth a Celery retry (NF-10).

``run_goal`` used to retry ANY exception as if it were transient: a programming
error (``AttributeError``/``TypeError``), a missing provider configuration or a
vault-key mismatch was re-run three times with backoff, repeating the goal's side
effects (fresh approvals, tool calls) before it was dead-lettered as "exceeded max
retries", burying the real reason.

Only transient INFRASTRUCTURE failures are retried now:

* network / socket level: ``ConnectionError``, ``TimeoutError``, DNS failures,
  ``httpx`` transport errors and vendor SDK connection/timeout errors;
* Postgres: SQLAlchemy ``OperationalError`` / ``DisconnectionError`` / pool
  ``TimeoutError``, an invalidated DBAPI connection, asyncpg connection,
  too-many-connections, serialization and deadlock errors;
* Redis: ``redis.exceptions.ConnectionError`` / ``TimeoutError``;
* LLM providers: HTTP 429 / 408 / 5xx (incl. Anthropic's 529 "overloaded") and an
  open provider circuit.

Everything else — and in particular every programming error — is permanent: the
goal fails once, with its real (redacted) reason. An explicit ``raise X from
cause`` is followed so a wrapped infrastructure error stays transient; an
implicit ``__context__`` is not (a bug raised while handling a connection error
is still a bug).
"""

from __future__ import annotations

import socket

# A programming error is never transient, whatever it was raised from.
_PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (
    AttributeError,
    TypeError,
    NameError,
    KeyError,
    IndexError,
    ValueError,
    AssertionError,
    NotImplementedError,
    ImportError,
    SyntaxError,
    RecursionError,
    ZeroDivisionError,
)

# Vendor SDK classes matched by name, so no SDK has to be importable here.
_TRANSIENT_CLASS_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "ServiceUnavailableError",
        "OverloadedError",
        "ServiceUnavailable",
        "DeadlineExceeded",
        "ProviderCircuitOpenError",
        "ProviderRateLimitedError",
    }
)

_TRANSIENT_STATUS = frozenset({408, 425, 429})

_MAX_CHAIN = 8


def _transient_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [ConnectionError, TimeoutError, socket.gaierror]
    try:
        from sqlalchemy import exc as sa_exc

        types += [sa_exc.OperationalError, sa_exc.DisconnectionError, sa_exc.TimeoutError]
    except ImportError:  # pragma: no cover - sqlalchemy is a hard dependency
        pass
    try:
        from asyncpg import exceptions as pg_exc

        types += [
            pg_exc.PostgresConnectionError,
            pg_exc.ConnectionDoesNotExistError,
            pg_exc.CannotConnectNowError,
            pg_exc.TooManyConnectionsError,
            pg_exc.SerializationError,
            pg_exc.DeadlockDetectedError,
        ]
    except ImportError:  # pragma: no cover
        pass
    try:
        from redis import exceptions as redis_exc

        types += [redis_exc.ConnectionError, redis_exc.TimeoutError]
    except ImportError:  # pragma: no cover
        pass
    try:
        import httpx

        types += [httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError]
    except ImportError:  # pragma: no cover
        pass
    return tuple(types)


_TRANSIENT_TYPES = _transient_types()


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int) and not isinstance(val, bool):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    response = getattr(exc, "response", None)
    val = getattr(response, "status_code", None)
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    return None


def _is_transient_single(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_TYPES):
        return True
    # SQLAlchemy marks a DBAPI error raised on a connection it had to drop.
    if getattr(exc, "connection_invalidated", False) is True:
        return True
    if type(exc).__name__ in _TRANSIENT_CLASS_NAMES:
        return True
    status = _status_of(exc)
    return status is not None and (status in _TRANSIENT_STATUS or 500 <= status <= 599)


def is_transient_infra_error(exc: BaseException) -> bool:
    """True when ``exc`` is a transient infrastructure failure worth a retry."""
    if isinstance(exc, _PROGRAMMING_ERRORS) and not _is_transient_single(exc):
        return False
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(_MAX_CHAIN):
        if current is None or id(current) in seen:
            return False
        seen.add(id(current))
        if _is_transient_single(current):
            return True
        current = current.__cause__
    return False
