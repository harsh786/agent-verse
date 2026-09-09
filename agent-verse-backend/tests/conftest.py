"""Shared pytest fixtures for all test packages."""

from __future__ import annotations

import os

import pytest

# Allow subprocess execution in test environments (not production).
# The CodeInterpreter uses subprocess as Docker fallback in dev/CI.
os.environ.setdefault("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")
# Ensure tests run in development mode (not production fail-closed)
os.environ.setdefault("ENVIRONMENT", "development")


def _docker_available() -> bool:
    """Return True if a Docker daemon is reachable."""
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:
        return False


_DOCKER_OK: bool = _docker_available()


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests that require Docker or OPENAI_API_KEY when unavailable."""
    import os
    openai_key_set = bool(os.getenv("OPENAI_API_KEY"))
    skip_docker = pytest.mark.skip(reason="Docker daemon not available")
    skip_openai = pytest.mark.skip(reason="OPENAI_API_KEY not set")
    for item in items:
        try:
            import pathlib
            src = pathlib.Path(str(item.fspath)).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not _DOCKER_OK and ("testcontainers" in src or "DockerContainer" in src):
            item.add_marker(skip_docker, append=False)
        # Skip real-API integration tests when the API key is absent
        if not openai_key_set and 'os.getenv("OPENAI_API_KEY"' in src:
            item.add_marker(skip_openai, append=False)


@pytest.fixture(autouse=True)
def _keep_scaling_tasks_bound():
    """Guard against full-suite module-state pollution.

    A handful of tests re-import ``app.scaling.*`` (e.g. to re-evaluate
    module-level ``os.getenv`` in ``celery_app``) by clearing those entries from
    ``sys.modules``. If the ``app.scaling`` *package* is re-imported fresh without
    ``app.scaling.tasks`` being re-imported, the package object loses its
    ``tasks`` attribute — and a later ``monkeypatch.setattr("app.scaling.tasks.X")``
    fails with ``AttributeError: module 'app.scaling' has no attribute 'tasks'``.
    These failures are order-dependent (the victims pass in isolation). Re-bind
    the submodule when it has gone missing so the attribute-resolution path is
    stable regardless of test ordering.
    """
    import contextlib
    import importlib
    import sys

    scaling = sys.modules.get("app.scaling")
    if scaling is not None and not hasattr(scaling, "tasks"):
        with contextlib.suppress(Exception):
            importlib.import_module("app.scaling.tasks")
    yield


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


@pytest.fixture
async def signed_up_client(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "Test", "email": "b@b.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        yield c
