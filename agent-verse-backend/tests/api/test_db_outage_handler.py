"""A DB outage is answered 503 + Retry-After with the platform error envelope.

Covers both places an outage can surface: a route (handled per exception class in
ExceptionMiddleware) and a middleware (only the catch-all ``Exception`` handler in
ServerErrorMiddleware sees it). Real errors keep their 500.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import asyncpg
import pytest
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import exc as sa_exc

from app.core.errors import DatabaseUnavailableError
from app.main import _register_error_handlers, create_app
from tests.db.test_availability import _raise_in_module, _sa_wrap

_SECRET = "postgres-host-10.1.2.3"


def _outage_errors() -> dict[str, Callable[[], BaseException]]:
    return {
        "admin_shutdown": lambda: _sa_wrap(
            asyncpg.exceptions.AdminShutdownError(f"terminating connection {_SECRET}"),
            invalidated=True,
        ),
        "pool_timeout": lambda: sa_exc.TimeoutError(f"QueuePool limit reached {_SECRET}"),
        "pgbouncer": lambda: _sa_wrap(
            asyncpg.exceptions.ProtocolViolationError(
                "no more connections allowed (max_client_conn)"
            )
        ),
        "connection_closed": lambda: asyncpg.InterfaceError(
            "cannot perform operation: connection is closed"
        ),
        "connect_refused": lambda: _raise_in_module(
            "asyncpg.connect_utils",
            ConnectionRefusedError(61, f"Connect call failed ({_SECRET}, 5432)"),
        ),
        "cannot_connect_now": lambda: asyncpg.exceptions.CannotConnectNowError(
            "the database system is starting up"
        ),
        "explicit": lambda: DatabaseUnavailableError("db down"),
    }


def _app_raising(make: Callable[[], BaseException]) -> FastAPI:
    app = FastAPI()
    _register_error_handlers(app)
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise make()

    app.include_router(router)
    return app


def _assert_503(resp: Response) -> None:
    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "5"
    err = resp.json()["error"]
    assert err["code"] == "DATABASE_UNAVAILABLE"
    assert err["retryable"] is True
    assert err["error_id"]
    assert err["details"] == {"retry_after_seconds": 5}
    assert _SECRET not in resp.text  # never leak driver / host detail


@pytest.mark.parametrize("name", list(_outage_errors()))
def test_route_outage_answers_503(name: str) -> None:
    app = _app_raising(_outage_errors()[name])
    # raise_server_exceptions=True: a route outage is answered without bubbling up.
    _assert_503(TestClient(app).get("/boom"))


@pytest.mark.parametrize("name", list(_outage_errors()))
def test_middleware_outage_answers_503(name: str) -> None:
    make = _outage_errors()[name]
    app = FastAPI()
    _register_error_handlers(app)

    @app.middleware("http")
    async def failing_auth(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        raise make()

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"ok": "yes"}

    _assert_503(TestClient(app, raise_server_exceptions=False).get("/ok"))


@pytest.mark.parametrize(
    "make",
    [
        lambda: _sa_wrap(asyncpg.exceptions.UniqueViolationError("duplicate key")),
        lambda: _sa_wrap(asyncpg.exceptions.PostgresSyntaxError("syntax error")),
        lambda: _sa_wrap(asyncpg.exceptions.InvalidTextRepresentationError("bad uuid")),
        lambda: asyncpg.InterfaceError("another operation is in progress"),
        lambda: ConnectionRefusedError(61, "refused by some HTTP service"),
        lambda: RuntimeError("database connection lost"),
    ],
    ids=["integrity", "syntax", "data", "misuse", "foreign_socket", "runtime"],
)
def test_real_errors_stay_500(make: Callable[[], BaseException]) -> None:
    app = _app_raising(make)
    resp = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert resp.status_code == 500
    assert "Retry-After" not in resp.headers
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


def test_real_db_error_still_reaches_the_server_unchanged() -> None:
    # Non-outage DB errors are re-raised by the per-class handler, so they behave
    # exactly as before this handler existed (bubble to the server / test client).
    integrity = _sa_wrap(asyncpg.exceptions.UniqueViolationError("duplicate key"))
    app = _app_raising(lambda: integrity)
    with pytest.raises(sa_exc.IntegrityError):
        TestClient(app).get("/boom")


def test_create_app_maps_outage_to_503() -> None:
    app = create_app()
    router = APIRouter()

    # /health/ prefix: TenantMiddleware bypasses auth for this test route.
    @router.get("/health/db-outage")
    async def outage() -> None:
        raise _sa_wrap(asyncpg.exceptions.AdminShutdownError("terminating"), invalidated=True)

    app.include_router(router)
    _assert_503(TestClient(app).get("/health/db-outage"))
