"""Classify "the database is unavailable" errors (→ a retryable HTTP 503).

During a Postgres or PgBouncer outage the API used to answer 500: every
SQLAlchemy / asyncpg / socket error fell through to the generic ``Exception``
handler. An outage is not a server bug — clients should back off and retry —
so these errors now map to ``503`` + ``Retry-After`` (see ``app.main``).

The classification is deliberately precise. A real programming error (bad SQL,
constraint violation, data error) must stay a 500/4xx, so the decision is made
from exception CLASSES and Postgres SQLSTATEs, not from broad string matching:

1. SQLAlchemy says so: pool ``TimeoutError`` (QueuePool exhausted — the DB or
   PgBouncer is not handing out connections), ``DisconnectionError``, or a
   ``DBAPIError`` raised on a connection SQLAlchemy had to invalidate
   (``connection_invalidated``, set by the dialect's ``is_disconnect``).
2. The server answered with a SQLSTATE (asyncpg ``sqlstate``, psycopg ``sqlstate``
   / ``pgcode``, copied onto SQLAlchemy's adapted DBAPI errors). The FIRST SQLSTATE
   in the cause chain decides: class ``08`` (connection exception), ``53300``
   too_many_connections, ``57P01``/``57P02``/``57P03`` (admin / crash shutdown,
   cannot connect now) mean unavailable; any other SQLSTATE (``23505``, ``42601``,
   ``22P02`` …) is a real error and is never reclassified.
   ``08P01`` (protocol_violation) is excluded from the class-08 rule because
   Postgres also uses it for client bugs (e.g. a bind/parameter-count mismatch);
   it counts only with a PgBouncer message (below).
3. PgBouncer reports pooler failures as ordinary ErrorResponses (mostly
   ``08P01``). Those are recognised by a short, documented list of PgBouncer's
   own message texts (``_PGBOUNCER_UNAVAILABLE_MESSAGES``) — strings Postgres
   itself never produces — and only on errors that carry a SQLSTATE.
4. No SQLSTATE at all (the client never got an answer):
   * a DBAPI ``OperationalError`` (psycopg: connection refused / server closed
     the connection unexpectedly);
   * a DBAPI / asyncpg ``InterfaceError`` whose text is the driver's
     "connection is closed" marker — the same test SQLAlchemy's asyncpg dialect
     uses in ``is_disconnect``. Other InterfaceErrors are client misuse (wrong
     argument count, "another operation is in progress") and stay 500;
   * a socket-level ``OSError`` (``ConnectionRefusedError``, ``ConnectionResetError``,
     DNS ``gaierror``, connect ``TimeoutError``, asyncio's "Multiple exceptions"
     ``OSError``) raised from inside the DB driver / SQLAlchemy pool — checked via
     the traceback's module names, so a refused HTTP call to some other service
     is not mistaken for a DB outage. ``ssl.SSLError`` and ``PermissionError``
     (configuration problems) are excluded.

The cause chain followed is ``__cause__`` (explicit ``raise X from y``, which is
how SQLAlchemy wraps driver errors) and SQLAlchemy's ``.orig`` — never the
implicit ``__context__`` (a bug raised while handling an outage is still a bug).
"""

from __future__ import annotations

import ssl
from types import TracebackType

from app.core.errors import DatabaseUnavailableError

__all__ = [
    "DB_RETRY_AFTER_SECONDS",
    "DatabaseUnavailableError",
    "db_unavailable_reason",
    "is_db_unavailable_error",
]

#: Seconds a client should wait before retrying (``Retry-After``).
DB_RETRY_AFTER_SECONDS: int = DatabaseUnavailableError.retry_after_seconds or 5

_MAX_CHAIN = 10

# SQLSTATEs (outside class 08) that mean "the database cannot serve you now".
_UNAVAILABLE_SQLSTATES = frozenset(
    {
        "53300",  # too_many_connections
        "57P01",  # admin_shutdown (fast/smart shutdown, pg_terminate_backend on shutdown)
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now (starting up / shutting down / recovery)
    }
)
_PROTOCOL_VIOLATION = "08P01"

# PgBouncer's own error texts for pooler-side unavailability (src/client.c,
# src/janitor.c, src/objects.c). Matched case-insensitively as substrings, and
# only on server-sent errors (those with a SQLSTATE). Keep this list narrow:
# every entry must be a PgBouncer-specific phrase Postgres never emits.
_PGBOUNCER_UNAVAILABLE_MESSAGES = (
    "no more connections allowed",  # max_client_conn / max_db_connections reached
    "server login has been failing",  # server_login_retry: backend unreachable
    "query_wait_timeout",  # waited in the pool queue longer than query_wait_timeout
    "pgbouncer cannot connect to server",
    "server conn crashed",  # backend died under an active client transaction
)

# The driver marker for a closed connection (SQLAlchemy asyncpg dialect's
# ``is_disconnect`` uses the same test; psycopg2 says "connection already closed").
_CLOSED_CONNECTION_MARKERS = ("connection is closed", "connection already closed")

# Modules whose frames prove a socket error happened while talking to the DB.
_DB_DRIVER_MODULE_PREFIXES = (
    "asyncpg.",
    "psycopg.",
    "psycopg2.",
    "psycopg_pool.",
    "sqlalchemy.pool.",
    "sqlalchemy.engine.",
    "sqlalchemy.dialects.",
)

try:  # SQLAlchemy is a hard dependency; guarded only to keep the module importable.
    from sqlalchemy import exc as _sa_exc
except ImportError:  # pragma: no cover
    _sa_exc = None  # type: ignore[assignment]


def _chain(exc: BaseException) -> list[BaseException]:
    """``exc`` followed by its explicit causes (``.orig`` first, then ``__cause__``)."""
    out: list[BaseException] = []
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending and len(out) < _MAX_CHAIN:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        out.append(current)
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException):
            pending.append(orig)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    return out


def _sqlstate(exc: BaseException) -> str | None:
    for attr in ("sqlstate", "pgcode"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and len(value) == 5:
            return value.upper()
    return None


def _is_pgbouncer_unavailable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _PGBOUNCER_UNAVAILABLE_MESSAGES)


def _sqlstate_means_unavailable(state: str) -> bool:
    if state in _UNAVAILABLE_SQLSTATES:
        return True
    return state.startswith("08") and state != _PROTOCOL_VIOLATION


def _is_interface_error(exc: BaseException) -> bool:
    # DBAPI InterfaceError: SQLAlchemy's wrapper, the asyncpg adapter's, asyncpg's
    # own and psycopg's all share the class name (no driver import needed here).
    return any(cls.__name__ == "InterfaceError" for cls in type(exc).__mro__)


def _is_operational_error(exc: BaseException) -> bool:
    return any(cls.__name__ == "OperationalError" for cls in type(exc).__mro__)


def _raised_in_db_driver(tb: TracebackType | None) -> bool:
    while tb is not None:
        module = tb.tb_frame.f_globals.get("__name__", "")
        if isinstance(module, str) and module.startswith(_DB_DRIVER_MODULE_PREFIXES):
            return True
        tb = tb.tb_next
    return False


def _is_db_socket_error(exc: BaseException) -> bool:
    if not isinstance(exc, OSError) or isinstance(exc, ssl.SSLError | PermissionError):
        return False
    return _raised_in_db_driver(exc.__traceback__)


def _sqlalchemy_reason(exc: BaseException) -> str | None:
    if _sa_exc is None:  # pragma: no cover
        return None
    if isinstance(exc, _sa_exc.TimeoutError):
        return "pool_timeout"
    if isinstance(exc, _sa_exc.DisconnectionError):
        return "disconnect"
    if isinstance(exc, _sa_exc.DBAPIError) and exc.connection_invalidated:
        return "connection_invalidated"
    return None


def _single_reason(exc: BaseException) -> str | None:
    chain = _chain(exc)
    for err in chain:
        reason = _sqlalchemy_reason(err)
        if reason:
            return reason
    # The first SQLSTATE decides: the server answered, so it is either an outage
    # code or a real error that must keep its 500/4xx.
    for err in chain:
        state = _sqlstate(err)
        if state is None:
            continue
        if _is_pgbouncer_unavailable(err):
            return "pgbouncer"
        if _sqlstate_means_unavailable(state):
            return f"sqlstate_{state}"
        return None
    for err in chain:
        if _is_interface_error(err):
            text = str(err).lower()
            if any(marker in text for marker in _CLOSED_CONNECTION_MARKERS):
                return "connection_closed"
        elif _is_operational_error(err):
            return "operational_error"
        elif _is_db_socket_error(err):
            return f"connect_{type(err).__name__}"
    return None


def db_unavailable_reason(exc: BaseException) -> str | None:
    """A short machine-readable reason when ``exc`` means "DB unavailable", else None.

    An ``ExceptionGroup`` (anyio task groups) counts only when every leaf is a DB
    availability error.
    """
    if isinstance(exc, DatabaseUnavailableError):
        return "database_unavailable"
    if isinstance(exc, BaseExceptionGroup):
        reasons = [db_unavailable_reason(e) for e in exc.exceptions]
        if reasons and all(reasons):
            return reasons[0]
        return None
    return _single_reason(exc)


def is_db_unavailable_error(exc: BaseException) -> bool:
    """True when ``exc`` means Postgres / PgBouncer cannot serve the request now."""
    return db_unavailable_reason(exc) is not None
