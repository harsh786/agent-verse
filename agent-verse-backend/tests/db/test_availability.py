"""Which exceptions mean "the database is unavailable" (→ 503) and which stay 500/4xx.

Exceptions are built the way SQLAlchemy's asyncpg dialect builds them at runtime
(``AsyncAdapt_asyncpg_connection._handle_exception`` + ``DBAPIError.instance``),
so the classifier is tested against the real cause chain, not a simplification.
"""

from __future__ import annotations

import socket
import ssl
from typing import Any

import asyncpg
import pytest
from asyncpg.exceptions import _base as asyncpg_base
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi

from app.core.errors import DatabaseUnavailableError
from app.db.availability import (
    DB_RETRY_AFTER_SECONDS,
    db_unavailable_reason,
    is_db_unavailable_error,
)

_DBAPI = AsyncAdapt_asyncpg_dbapi(asyncpg)


def _raise_from(exc: BaseException, cause: BaseException | None) -> BaseException:
    """Return ``exc`` after a real ``raise exc from cause`` (sets __cause__ + traceback)."""
    try:
        raise exc from cause
    except BaseException as raised:
        return raised


def _sa_wrap(pg_error: BaseException, *, invalidated: bool = False) -> BaseException:
    """Wrap an asyncpg error exactly like SQLAlchemy's asyncpg dialect does."""
    mapping = _DBAPI._asyncpg_error_translate
    adapted: BaseException = pg_error
    for super_ in type(pg_error).__mro__:
        if super_ in mapping:
            adapted = mapping[super_](f"{type(pg_error)}: {pg_error}")
            adapted.pgcode = adapted.sqlstate = getattr(pg_error, "sqlstate", None)  # type: ignore[attr-defined]
            adapted = _raise_from(adapted, pg_error)
            break
    wrapped = sa_exc.DBAPIError.instance(
        "SELECT 1",
        {},
        adapted,  # type: ignore[arg-type]
        _DBAPI.Error,
        connection_invalidated=invalidated,
    )
    return _raise_from(wrapped, adapted)


def _raise_in_module(module: str, exc: BaseException) -> BaseException:
    """Raise ``exc`` from a frame whose module is ``module`` (e.g. inside asyncpg)."""
    namespace: dict[str, Any] = {"__name__": module}
    exec("def boom(exc):\n    raise exc\n", namespace)
    try:
        namespace["boom"](exc)
    except BaseException as raised:
        return raised
    raise AssertionError("unreachable")  # pragma: no cover


# ── outage → 503 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pg_error",
    [
        asyncpg.exceptions.ConnectionDoesNotExistError(
            "connection was closed in the middle of operation"
        ),
        asyncpg.exceptions.ConnectionFailureError("connection failure"),
        asyncpg.exceptions.ClientCannotConnectError("cannot connect"),
        asyncpg.exceptions.ConnectionRejectionError("rejected"),
        asyncpg.exceptions.CannotConnectNowError("the database system is shutting down"),
        asyncpg.exceptions.AdminShutdownError(
            "terminating connection due to administrator command"
        ),
        asyncpg.exceptions.CrashShutdownError("crash shutdown"),
        asyncpg.exceptions.TooManyConnectionsError("sorry, too many clients already"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_outage_sqlstates_are_unavailable_raw_and_wrapped(pg_error: BaseException) -> None:
    assert is_db_unavailable_error(pg_error)
    assert is_db_unavailable_error(_sa_wrap(pg_error))
    assert db_unavailable_reason(pg_error) == f"sqlstate_{pg_error.sqlstate}"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "message",
    [
        "no more connections allowed (max_client_conn)",
        "server login has been failing, try again later (server_login_retry)",
        "query_wait_timeout",
        "pgbouncer cannot connect to server",
        "server conn crashed?",
    ],
)
def test_pgbouncer_errors_are_unavailable(message: str) -> None:
    # PgBouncer sends these as ErrorResponse 08P01 (protocol_violation).
    err = asyncpg.exceptions.ProtocolViolationError(message)
    assert db_unavailable_reason(err) == "pgbouncer"
    assert db_unavailable_reason(_sa_wrap(err)) == "pgbouncer"


def test_pgbouncer_message_counts_whatever_its_sqlstate() -> None:
    # Newer PgBouncer versions use specific SQLSTATEs (e.g. 57014 / 53300).
    err = asyncpg.exceptions.QueryCanceledError("query_wait_timeout")
    assert is_db_unavailable_error(err)


def test_sqlalchemy_pool_timeout_is_unavailable() -> None:
    err = sa_exc.TimeoutError(
        "QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 30.00"
    )
    assert db_unavailable_reason(err) == "pool_timeout"


def test_sqlalchemy_disconnection_error_is_unavailable() -> None:
    assert db_unavailable_reason(sa_exc.DisconnectionError("gone")) == "disconnect"


def test_invalidated_connection_is_unavailable() -> None:
    err = _sa_wrap(asyncpg_base.InterfaceError("unexpected"), invalidated=True)
    assert db_unavailable_reason(err) == "connection_invalidated"


@pytest.mark.parametrize(
    "message",
    [
        "cannot perform operation: connection is closed",
        "connection is closed",
    ],
)
def test_closed_connection_interface_error_is_unavailable(message: str) -> None:
    raw = asyncpg.InterfaceError(message)
    assert db_unavailable_reason(raw) == "connection_closed"
    assert db_unavailable_reason(_sa_wrap(raw)) == "connection_closed"


def test_psycopg_style_operational_error_without_sqlstate_is_unavailable() -> None:
    # psycopg raises OperationalError (no SQLSTATE) for "connection refused" and
    # "server closed the connection unexpectedly".
    class OperationalError(Exception):
        sqlstate = None

    orig = OperationalError('connection to server at "db" (10.0.0.5), port 5432 failed')
    wrapped = _raise_from(sa_exc.OperationalError("SELECT 1", {}, orig), orig)
    assert db_unavailable_reason(wrapped) == "operational_error"


def test_psycopg2_connection_already_closed_is_unavailable() -> None:
    class InterfaceError(Exception):
        pgcode = None

    orig = InterfaceError("connection already closed")
    wrapped = _raise_from(sa_exc.InterfaceError("SELECT 1", {}, orig), orig)
    assert is_db_unavailable_error(wrapped)


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError(61, "Connect call failed ('127.0.0.1', 5432)"),
        ConnectionResetError(54, "Connection reset by peer"),
        OSError("Multiple exceptions: [Errno 61] Connect call failed ('::1', 5432, 0, 0)"),
        socket.gaierror(8, "nodename nor servname provided, or not known"),
        TimeoutError(),
        BrokenPipeError(32, "Broken pipe"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_socket_errors_inside_the_db_driver_are_unavailable(exc: BaseException) -> None:
    raised = _raise_in_module("asyncpg.connect_utils", exc)
    assert db_unavailable_reason(raised) == f"connect_{type(exc).__name__}"
    raised = _raise_in_module("sqlalchemy.pool.base", type(exc)(*exc.args))
    assert is_db_unavailable_error(raised)


def test_explicitly_raised_platform_error_is_unavailable() -> None:
    assert is_db_unavailable_error(DatabaseUnavailableError("db down"))
    assert DB_RETRY_AFTER_SECONDS == DatabaseUnavailableError.retry_after_seconds == 5


def test_explicit_cause_chain_is_followed() -> None:
    cause = asyncpg.exceptions.CannotConnectNowError("starting up")
    assert is_db_unavailable_error(_raise_from(RuntimeError("store read failed"), cause))


def test_exception_group_of_outages_is_unavailable() -> None:
    group = ExceptionGroup(
        "tg",
        [
            asyncpg.exceptions.AdminShutdownError("terminating"),
            sa_exc.TimeoutError("QueuePool limit"),
        ],
    )
    assert is_db_unavailable_error(group)


# ── real errors stay 500 / 4xx ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pg_error",
    [
        asyncpg.exceptions.UniqueViolationError("duplicate key value violates unique constraint"),
        asyncpg.exceptions.ForeignKeyViolationError("violates foreign key constraint"),
        asyncpg.exceptions.NotNullViolationError("null value in column"),
        asyncpg.exceptions.PostgresSyntaxError('syntax error at or near "SELEC"'),
        asyncpg.exceptions.UndefinedTableError('relation "nope" does not exist'),
        asyncpg.exceptions.UndefinedColumnError('column "nope" does not exist'),
        asyncpg.exceptions.InvalidTextRepresentationError("invalid input syntax for type uuid"),
        asyncpg.exceptions.DivisionByZeroError("division by zero"),
        asyncpg.exceptions.InsufficientPrivilegeError("permission denied for table goals"),
        asyncpg.exceptions.SerializationError("could not serialize access"),
        asyncpg.exceptions.DeadlockDetectedError("deadlock detected"),
        asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout"),
        asyncpg.exceptions.InvalidPasswordError("password authentication failed"),
        asyncpg.exceptions.InvalidCatalogNameError('database "nope" does not exist'),
        # Postgres' own 08P01 for a client bug — not PgBouncer, not an outage.
        asyncpg.exceptions.ProtocolViolationError(
            "bind message supplies 1 parameters, but prepared statement requires 2"
        ),
    ],
    ids=lambda e: type(e).__name__,
)
def test_real_postgres_errors_are_not_unavailable(pg_error: BaseException) -> None:
    assert not is_db_unavailable_error(pg_error)
    assert not is_db_unavailable_error(_sa_wrap(pg_error))


def test_wrapped_integrity_error_keeps_its_class() -> None:
    wrapped = _sa_wrap(asyncpg.exceptions.UniqueViolationError("duplicate key"))
    assert isinstance(wrapped, sa_exc.IntegrityError)
    assert not is_db_unavailable_error(wrapped)


@pytest.mark.parametrize(
    "message",
    [
        "cannot perform operation: another operation is in progress",
        "the server expects 2 arguments for this query, 1 was passed",
        "cannot call Connection.fetch(): connection has been released back to the pool",
    ],
)
def test_client_misuse_interface_errors_are_not_unavailable(message: str) -> None:
    raw = asyncpg.InterfaceError(message)
    assert not is_db_unavailable_error(raw)
    assert not is_db_unavailable_error(_sa_wrap(raw))


def test_client_side_data_error_is_not_unavailable() -> None:
    raw = asyncpg_base.DataError("invalid input for query argument $1: 'x' (expected int)")
    assert not is_db_unavailable_error(raw)
    assert not is_db_unavailable_error(_sa_wrap(raw))


def test_programming_errors_and_strings_are_not_unavailable() -> None:
    assert not is_db_unavailable_error(RuntimeError("database connection lost"))
    assert not is_db_unavailable_error(ValueError("connection is closed"))
    assert not is_db_unavailable_error(sa_exc.InvalidRequestError("bad ORM use"))
    assert not is_db_unavailable_error(sa_exc.NoResultFound("No row was found"))


def test_socket_error_outside_the_db_driver_is_not_unavailable() -> None:
    # A refused HTTP call to some other service is not a DB outage.
    raised = _raise_in_module("httpx._transports.default", ConnectionRefusedError(61, "refused"))
    assert not is_db_unavailable_error(raised)
    assert not is_db_unavailable_error(ConnectionRefusedError(61, "never raised"))


@pytest.mark.parametrize(
    "exc",
    [
        ssl.SSLCertVerificationError("certificate verify failed"),
        PermissionError(13, "Permission denied: '/var/run/postgresql/.s.PGSQL.5432'"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_configuration_errors_inside_the_driver_are_not_unavailable(exc: BaseException) -> None:
    assert not is_db_unavailable_error(_raise_in_module("asyncpg.connect_utils", exc))


def test_implicit_context_is_not_followed() -> None:
    # A bug raised while handling an outage is still a bug.
    try:
        try:
            raise asyncpg.exceptions.AdminShutdownError("terminating")
        except asyncpg.exceptions.AdminShutdownError:
            raise KeyError("missing") from None
    except KeyError as bug:
        assert not is_db_unavailable_error(bug)
    try:
        try:
            raise asyncpg.exceptions.AdminShutdownError("terminating")
        except asyncpg.exceptions.AdminShutdownError:
            raise AttributeError("oops")  # noqa: B904 - implicit __context__ on purpose
    except AttributeError as bug:
        assert not is_db_unavailable_error(bug)


def test_first_sqlstate_decides_over_a_wrapper_chain() -> None:
    # A constraint violation wrapped in a RuntimeError keeps its non-outage verdict.
    integrity = _sa_wrap(asyncpg.exceptions.UniqueViolationError("duplicate key"))
    assert not is_db_unavailable_error(_raise_from(RuntimeError("save failed"), integrity))


def test_mixed_exception_group_is_not_unavailable() -> None:
    group = ExceptionGroup(
        "tg", [asyncpg.exceptions.AdminShutdownError("terminating"), ValueError("bug")]
    )
    assert not is_db_unavailable_error(group)
