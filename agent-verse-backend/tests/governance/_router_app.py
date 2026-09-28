"""Minimal FastAPI app for exercising governance routers with a chosen tenant role."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from app.tenancy.context import PlanTier, TenantContext


def tenant(tenant_id: str = "t-gov", roles: tuple[str, ...] = ("admin",)) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k-gov", roles=roles
    )


def make_app(*routers: Any, ctx: TenantContext | None = None) -> FastAPI:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    _ctx = ctx or tenant()

    @app.middleware("http")
    async def _auth(request: Request, call_next: Any) -> Any:
        request.state.tenant = _ctx
        return await call_next(request)

    return app
