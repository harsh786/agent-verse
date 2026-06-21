"""FastAPI application factory.

Wires logging, tracing, CORS, security headers, structured error handling, the system
router (health/metrics), and a per-app health-check registry. The factory takes optional
``settings`` and ``health_checks`` so tests can drive it deterministically.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.connectors import router as connectors_router
from app.api.goals import router as goals_router
from app.api.system import router as system_router
from app.api.tenants import router as tenants_router
from app.core.config import Settings, get_settings
from app.core.errors import InternalError, PlatformError
from app.core.pools import ConnectionPools
from app.observability.health import HealthCheck, HealthRegistry
from app.observability.logging import configure_logging, get_logger
from app.observability.tracing import configure_tracing
from app.tenancy.middleware import SecurityHeadersMiddleware

logger = get_logger(__name__)


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def _platform_error_handler(_: Request, exc: PlatformError) -> JSONResponse:
        if exc.severity.value in {"high", "critical"}:
            logger.error("platform_error", code=exc.code, error_id=exc.error_id)
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Never leak internal detail to the client; log the real cause server-side.
        internal = InternalError("An internal error occurred", cause=exc)
        logger.error("unhandled_error", error_id=internal.error_id, exc_info=exc)
        return JSONResponse(internal.to_dict(), status_code=internal.http_status)


def create_app(
    settings: Settings | None = None,
    health_checks: Sequence[HealthCheck] | None = None,
    pools: ConnectionPools | None = None,
    manage_pools: bool = False,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.is_production)

    registry = HealthRegistry(list(health_checks or []))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Pools are managed in real runtime; tests pass manage_pools=False to stay offline.
        if manage_pools:
            active = pools or ConnectionPools(settings=settings)
            await active.startup()
            for check in active.health_checks():
                registry.register(check)
            app.state.pools = active
            try:
                yield
            finally:
                await active.shutdown()
        else:
            yield

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.health = registry

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(SecurityHeadersMiddleware)
    _register_error_handlers(app)
    app.include_router(system_router)
    app.include_router(tenants_router)
    app.include_router(goals_router)
    app.include_router(connectors_router)
    configure_tracing(app, settings)

    return app


app = create_app()
