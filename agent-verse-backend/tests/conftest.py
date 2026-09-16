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

    # Ensure the app.scaling package exposes its `tasks` submodule as an
    # attribute before every test. A prior test may replace the app.scaling
    # *package* object in sys.modules (re-importing it fresh without tasks, e.g.
    # a test that only imports app.scaling.celery_app). The fresh package then
    # lacks a `tasks` attribute and monkeypatch.setattr("app.scaling.tasks.X")
    # fails with AttributeError. A plain ``import app.scaling.tasks`` does NOT
    # fix this when the submodule is already cached — CPython only binds the
    # parent attribute during the submodule's original import, not on cached
    # re-imports — so force-rebind the attribute explicitly.
    with contextlib.suppress(Exception):
        # import_module recreates the app.scaling package if a prior test removed
        # it entirely, and returns the (possibly cached) tasks submodule.
        scaling = importlib.import_module("app.scaling")
        tasks_mod = sys.modules.get("app.scaling.tasks") or importlib.import_module(
            "app.scaling.tasks"
        )
        if getattr(scaling, "tasks", None) is not tasks_mod:
            scaling.tasks = tasks_mod  # type: ignore[attr-defined]
    yield


@pytest.fixture(autouse=True)
def _reset_process_embedding_cache():
    """Reset the process-wide RAG embedding cache between tests.

    ``app.rag.gateway._EMBED_CACHE`` is a module singleton, so an embedding
    cached by one test would otherwise be served to the next (leaking token
    counts and budget accounting across tests). Clearing it before each test
    keeps them deterministic regardless of whether the cache is enabled.
    """
    try:
        from app.rag.gateway import _EMBED_CACHE

        _EMBED_CACHE.clear()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_dept_memory_singleton():
    """Reset the process-global DepartmentMemory singleton after each test.

    ``app.memory.dept_memory._dept_memory`` is a module singleton whose
    ``_db_factory`` is wired by the app lifespan (main.py). A test that runs the
    lifespan with a fake/recording session factory (e.g. the gateway lifespan
    tests) leaves that fake db on the singleton — monkeypatch does not undo the
    ``set_db`` side-effect — which then leaks into any later test that lists
    department memory. Restoring the singleton to its pristine, unwired state
    after every test makes each test start as it does in isolation (the next
    test's app re-wires the real factory), without mocking anything.
    """
    yield
    try:
        from app.memory.dept_memory import _dept_memory

        _dept_memory._store.clear()
        _dept_memory._db_factory = None
    except Exception:
        pass


# Provider / model / embedding / on-prem env vars. When a real provider is
# configured in the environment (e.g. NVIDIA keys in a dev shell), the app
# auto-registers that configured model / backfills on-prem settings at startup,
# which contaminates tests that assert on an empty or explicitly-seeded model
# registry. Stripping them gives those tests the clean provider environment they
# assume (the same one CI runs in) — this is env isolation, not mocking.
_PROVIDER_ENV_VARS = (
    "NVIDIA_API_KEY", "NVIDIA_BASE_URL", "NVIDIA_MODEL", "NVIDIA_EMBED_MODEL",
    "NVIDIA_VISION_MODEL", "NVIDIA_AUDIO_MODEL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "DEFAULT_LLM_PROVIDER", "DEFAULT_MODEL", "DEFAULT_CLASSIFICATION_MODEL",
    "DEFAULT_EXECUTION_MODEL", "DEFAULT_PLANNING_MODEL",
    "DEFAULT_SUMMARIZATION_MODEL", "DEFAULT_VERIFICATION_MODEL",
    "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_DIM",
    "ONPREM_ENABLED", "ONPREM_MODELS", "ONPREM_EMBEDDING_MODEL", "ONPREM_RERANKER_MODEL",
)


@pytest.fixture(autouse=True)
def isolate_provider_env(request, monkeypatch):
    """Strip ambient provider/model env for tests that opt in.

    Opt in per-module with ``pytestmark = pytest.mark.usefixtures(...)`` — no, this
    is autouse but only ACTS when the module sets ``_ISOLATE_PROVIDER_ENV = True``,
    so it is a no-op for every other test and cannot disturb tests that rely on the
    ambient provider env.
    """
    if getattr(request.module, "_ISOLATE_PROVIDER_ENV", False):
        for var in _PROVIDER_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        try:
            from app.core.config import get_settings

            get_settings.cache_clear()
        except Exception:
            pass
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
