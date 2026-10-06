"""a10-F229-01: the builder has no live preview / asset routes.

They were honest 501s (and before that served a "Building..." page forever,
listed artifacts across tenants and leaked ``str(exc)``). Owner decision: remove
them; a builder project is a code-generation goal (status via
GET /builder/projects/{id}: tests/api/test_builder_project_status.py).
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.builder import router


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _t(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.tenant = SimpleNamespace(tenant_id="t1")
        return await call_next(request)

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/builder/preview/ws-1", "/builder/assets/ws-1/app.js"])
def test_preview_and_asset_routes_are_gone(path: str) -> None:
    assert _client().get(path).status_code == 404


def test_router_exposes_only_project_routes() -> None:
    paths = {getattr(r, "path", "") for r in router.routes}
    assert paths == {"/builder/projects", "/builder/projects/{project_id}"}


def test_submit_response_advertises_no_preview_url() -> None:
    from app.api.builder import BuilderProject

    assert "preview_url" not in BuilderProject.model_fields
