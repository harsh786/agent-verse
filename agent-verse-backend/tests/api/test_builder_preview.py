"""Builder project status / preview / assets are NOT IMPLEMENTED — honest 501.

Regression: the preview listed artifacts by a workspace-id substring across all
tenants, called the keyword-only ``read_bytes`` positionally (TypeError swallowed),
and so showed a "Building..." page forever; project status was a stub that said
``building`` for any id; the asset route leaked ``str(exc)`` in a 500.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.builder import router


def _client(*, authed: bool = True) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _t(request: Request, call_next):  # type: ignore[no-untyped-def]
        if authed:
            request.state.tenant = SimpleNamespace(tenant_id="t1")
        return await call_next(request)

    app.include_router(router)
    store = MagicMock()
    store.list_artifacts = AsyncMock(return_value=[{"id": "a", "name": "index.html"}])
    store.read_bytes = AsyncMock(return_value=b"<html>other tenant's site</html>")
    app.state.artifact_store = store
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path", ["/builder/projects/p1", "/builder/preview/ws-1", "/builder/assets/ws-1/app.js"]
)
def test_unimplemented_builder_reads_are_501_and_serve_nothing(path: str) -> None:
    resp = _client().get(path)
    assert resp.status_code == 501
    assert "other tenant" not in resp.text


@pytest.mark.parametrize("path", ["/builder/projects/p1", "/builder/preview/ws-1"])
def test_builder_reads_require_auth(path: str) -> None:
    assert _client(authed=False).get(path).status_code == 401
