"""Shared pytest fixtures for all test packages."""

from __future__ import annotations

import contextlib
import os
import socket
from typing import Any

import pytest

# ── Live-infra safety net ──────────────────────────────────────────────────────
# The app's defaults (Settings.database_url / redis_url, the Celery broker, the
# many ``os.getenv("REDIS_URL", "redis://localhost:6379/0")`` call sites) point at
# the developer's LIVE local Postgres (:5432), pgbouncer (:6432) and Redis (:6379).
# Tests that fell through to those defaults used to write rows into the dev
# database. So, unless a run explicitly opts in with
# ``AGENTVERSE_TESTS_ALLOW_LIVE_INFRA=1`` (only ever for disposable infra, e.g. a
# deliberate run of the live-stack suites under tests/real_e2e), this:
#
# 1. forces DATABASE_URL / REDIS_URL to an unreachable address BEFORE any app
#    module is imported (so module-level ``os.getenv`` reads and ``Settings``
#    both see it) and clears the Sentinel/Cluster/maintenance DSNs, and
# 2. refuses any socket connection to a loopback address on 5432/6432/6379, so
#    even a hard-coded ``localhost:5432`` in a test or a literal default argument
#    cannot reach live infra.
#
# Tests that genuinely need Postgres/Redis use testcontainers (random host
# ports, unaffected) via the ``pg_url`` / ``redis_url`` fixtures below or the
# ``tests/e2e_full`` harness, and are marked ``integration`` / ``e2e_full``.
_ALLOW_LIVE_INFRA = os.getenv("AGENTVERSE_TESTS_ALLOW_LIVE_INFRA", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://nouser:nopass@127.0.0.1:1/none"
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/0"
_LIVE_INFRA_PORTS = frozenset({5432, 6432, 6379})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"})

if not _ALLOW_LIVE_INFRA:
    os.environ["DATABASE_URL"] = UNREACHABLE_DATABASE_URL
    os.environ["REDIS_URL"] = UNREACHABLE_REDIS_URL
    for _var in (
        "MAINTENANCE_DATABASE_URL",
        "REDIS_SENTINEL_URLS",
        "REDIS_CLUSTER_NODES",
    ):
        os.environ.pop(_var, None)

    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex

    def _is_live_infra(address: object) -> bool:
        if not isinstance(address, tuple) or len(address) < 2:
            return False
        host, port = address[0], address[1]
        host = str(host).removeprefix("::ffff:")  # IPv4-mapped IPv6
        return port in _LIVE_INFRA_PORTS and (host in _LOOPBACK_HOSTS or host.startswith("127."))

    def _refuse(address: object) -> ConnectionRefusedError:
        return ConnectionRefusedError(
            111,
            f"tests/conftest.py blocked a connection to live local infra {address!r}; "
            "use a testcontainers fixture, or set AGENTVERSE_TESTS_ALLOW_LIVE_INFRA=1",
        )

    def _guarded_connect(self: socket.socket, address: object) -> None:
        if _is_live_infra(address):
            raise _refuse(address)
        return _real_connect(self, address)  # type: ignore[arg-type]

    def _guarded_connect_ex(self: socket.socket, address: object) -> int:
        if _is_live_infra(address):
            return 111  # ECONNREFUSED
        return _real_connect_ex(self, address)  # type: ignore[arg-type]

    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]

# Allow subprocess execution in test environments (not production).
# The CodeInterpreter uses subprocess as Docker fallback in dev/CI.
os.environ.setdefault("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")
# A remote code-sandbox runner configured in the developer's shell would route
# every test's code execution to it; tests that use one start their own.
os.environ.pop("CODE_SANDBOX_URL", None)
os.environ.pop("CODE_SANDBOX_TOKEN", None)
# Ensure tests run in development mode (not production fail-closed)
os.environ.setdefault("ENVIRONMENT", "development")
# The beat reads Postgres by default (TRG-15); unit tests must never reach a
# developer database through the default DATABASE_URL, so they opt out.
os.environ.setdefault("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "false")
# Unit tests must not load the real cross-encoder in the background at every app
# startup (RERANK-PRELOAD); tests that exercise the warm-up opt in explicitly.
os.environ.setdefault("RAG_RERANK_PRELOAD", "false")
# The SSRF tests assert the public-only policy; production defaults
# ALLOW_PRIVATE_NETWORK_ACCESS on (owner decision 2026-10-06), covered by
# tests/net/test_private_network_access.py.
os.environ.setdefault("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
# OCR tests exercise the Tesseract-first path; production defaults Tesseract OFF
# (OCR_TESSERACT_ENABLED, owner decision 2026-10-06), covered by its own test.
os.environ.setdefault("OCR_TESSERACT_ENABLED", "true")
os.environ.setdefault("COLBERT_PREFETCH", "false")

# Tests must not depend on the developer's .env: a real provider key there (e.g.
# NVIDIA_API_KEY) turned "no keys -> FakeProvider" tests into real-provider runs.
# Opt-in real-provider suites (REAL_PROVIDERS=1) keep reading it.
if os.getenv("REAL_PROVIDERS") != "1":
    from app.core.config import Settings as _Settings

    _Settings.model_config["env_file"] = None


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


def _shutdown_ocr_pools() -> None:
    """Stop the process-wide OCR thread pool before the interpreter tears down.

    An OCR thread still inside tesseract / poppler during interpreter shutdown
    is the likely cause of the one-off ``libc++abi: ... recursive_mutex lock
    failed: Invalid argument`` abort (EXIT 134) at suite exit. Only when a test
    imported the module: the teardown must not import the app itself.
    """
    import sys

    module = sys.modules.get("app.ocr.concurrency")
    if module is None:
        return
    try:
        module.shutdown_ocr_concurrency(wait=True, cancel_futures=True)
    except Exception as exc:  # teardown must still reap the workers below
        print(f"\n[OCR] could not shut down the OCR pool cleanly: {exc!r}")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """USR-7: no out-of-process Celery worker outlives the test session.

    First the process-wide OCR pool is stopped in order (``_shutdown_ocr_pools``).

    The e2e worker helpers (``tests/_worker_procs.py``) stop their workers on
    every exit path; this is the backstop. Any worker group of this session that
    is still running is killed here and fails the run, so a leak is reported
    instead of holding Postgres, Redis and files open after pytest exits.
    """
    _shutdown_ocr_pools()

    from tests import _worker_procs

    try:
        leaked = _worker_procs.reap_surviving_workers()
    except Exception as exc:  # the check itself failing must not pass silently
        leaked = [-1]
        print(f"\n[USR-7] could not verify worker cleanup: {exc!r}")
    if leaked:
        print(
            f"\n[USR-7] {len(leaked)} Celery worker process group(s) outlived the "
            f"session and were killed: {leaked}"
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _keep_scaling_tasks_bound():
    """Guard against full-suite module-state pollution.

    A handful of tests (e.g. ``tests/net/test_redis_factory.py``) re-import
    ``app.scaling.*`` (to re-evaluate module-level ``os.getenv()`` in
    ``celery_app``) by clearing those entries from ``sys.modules``, importing a
    fresh ``app.scaling.celery_app`` under a mocked ``celery``, then restoring
    the ORIGINAL module objects into ``sys.modules`` afterwards. That
    ``sys.modules`` restore does not undo a side effect of the fresh import:
    Python's import machinery rebinds the *parent* package's attribute
    (``sys.modules["app"].scaling``) to the fresh ``app.scaling`` object as a
    matter of course, and restoring ``sys.modules["app.scaling"]`` back to the
    original object does not rebind that parent attribute back — so
    ``app.scaling`` (accessed as an attribute, which is exactly how pytest's
    ``monkeypatch.setattr("app.scaling.tasks.X", ...)`` resolves dotted paths)
    keeps pointing at the leaked fresh package, which never had ``.tasks``
    imported onto it. The result: ``monkeypatch.setattr("app.scaling.tasks.X")``
    fails with ``AttributeError: module 'app.scaling' has no attribute 'tasks'``
    even though ``sys.modules["app.scaling"].tasks`` is fine. These failures are
    order-dependent (the victims pass in isolation). Re-bind both the parent's
    ``scaling`` attribute and ``scaling``'s ``tasks`` attribute to the current
    ``sys.modules`` entries before every test so attribute-resolution is stable
    regardless of test ordering.
    """
    import importlib
    import sys

    with contextlib.suppress(Exception):
        # import_module recreates app/app.scaling fresh if a prior test removed
        # them entirely, and otherwise returns the (possibly cached) module.
        app_pkg = importlib.import_module("app")
        scaling = sys.modules.get("app.scaling") or importlib.import_module("app.scaling")
        if getattr(app_pkg, "scaling", None) is not scaling:
            app_pkg.scaling = scaling  # type: ignore[attr-defined]
        tasks_mod = sys.modules.get("app.scaling.tasks") or importlib.import_module(
            "app.scaling.tasks"
        )
        if getattr(scaling, "tasks", None) is not tasks_mod:
            scaling.tasks = tasks_mod  # type: ignore[attr-defined]
    yield


@pytest.fixture(autouse=True)
def _close_pooled_mongodb_clients():
    """Pooled MongoDB MCP clients (C2) never leak from one test into the next.

    A test that swaps ``pymongo.MongoClient`` for a recorder would otherwise be
    handed a client an earlier test cached under the same key.
    """
    import sys

    def _close() -> None:
        pool = sys.modules.get("app.mcp.mongodb_clients")
        if pool is not None:
            pool.close_all()

    _close()
    yield
    _close()


@pytest.fixture(autouse=True)
def _reset_worker_deployment_provider_cache():
    """Drop the Celery worker's once-per-process deployment provider after each test.

    ``app.scaling.tasks`` caches the on-prem/NVIDIA cluster provider (built from
    env) for the life of the worker process; a test that ran ``run_goal`` under a
    provider env must not leak that provider into the next test.
    """
    yield
    import sys

    tasks_mod = sys.modules.get("app.scaling.tasks")
    reset = getattr(tasks_mod, "_reset_worker_deployment_provider", None)
    if callable(reset):
        reset()


@pytest.fixture(autouse=True)
def _restore_guardrail_rule_repository():
    """Undo a guardrail rule repository bound during a test.

    ``_build_worker_ingestion`` (like the API lifespan) binds the process-global
    ``guardrails_engine`` to a DB repository; left bound, the next test's
    evaluations would try to load rules from a mock or absent database.
    """
    from app.guardrails_v2.engine import guardrails_engine

    repo, auto = guardrails_engine._repo, guardrails_engine._auto_persist
    yield
    if guardrails_engine._repo is not repo:
        guardrails_engine.bind_repository(repo, auto_persist=auto)


@pytest.fixture(autouse=True)
def _restore_model_registry_store():
    """Undo a shared model-registry store and registry state bound during a test.

    ``run_goal`` and the API lifespan bind the process-global store to Redis
    (the test env's Redis is unreachable); left bound, every later routing-policy
    or configured-model call in the run answers 503 "store unavailable".
    """
    from app.ai_router import registry_store, selection
    from app.ai_router.registry import model_registry

    saved = registry_store._store
    # The configured set and the per-capability preference order are process
    # state too: a test that saved an order (or registered models) used to leave
    # it behind, and every later role-routing test then picked that model.
    saved_configured = dict(model_registry._configured)
    saved_preferences = {k: list(v) for k, v in model_registry._preferences.items()}
    saved_seed = (selection._lazy_seeded, selection._seeded_version)
    yield
    registry_store._store = saved
    model_registry._configured = saved_configured
    model_registry._preferences = saved_preferences
    selection._lazy_seeded, selection._seeded_version = saved_seed


@pytest.fixture(autouse=True)
def _restore_default_calibration_store():
    """Undo the API lifespan binding the process-wide verifier calibration store to
    a database (a05-F092-01): left bound, later worker tests saw a DB-bound default."""
    import sys

    mod = sys.modules.get("app.intelligence.verifier_calibration")
    store = getattr(mod, "_default_calibration_store", None) if mod else None
    saved = getattr(store, "_db", None) if store is not None else None
    yield
    mod = sys.modules.get("app.intelligence.verifier_calibration")
    store = getattr(mod, "_default_calibration_store", None) if mod else None
    if store is not None and getattr(store, "_db", None) is not saved:
        store._db = saved


@pytest.fixture(autouse=True)
def _reset_provider_circuit_breaker():
    """Close the process-wide provider circuits after each test.

    Tests that make a fake provider fail open the per-model circuit for
    ``provider:""`` (fakes have no model name); left open, a later test's first
    call to a different fake is refused for 60 s and its step "fails".
    """
    yield
    from app.providers.circuit_breaker import _provider_cb

    _provider_cb._failures.clear()
    _provider_cb._last_failure.clear()
    _provider_cb._state.clear()
    _provider_cb._half_open_calls.clear()


@pytest.fixture(autouse=True)
def _reset_ip_rate_limit_windows():
    """Reset the in-process per-IP limiter (signup / SSO token endpoints).

    Without Redis it keeps a per-IP sliding window in process memory; every
    TestClient request comes from the same "testclient" peer, so windows filled
    by one test would 429 the next.
    """
    from app.tenancy import ip_rate_limit

    ip_rate_limit._local_windows.clear()
    yield
    ip_rate_limit._local_windows.clear()


@pytest.fixture(autouse=True)
def _reset_tenant_fallback_rate_limit():
    """Reset the in-process per-TENANT fallback limiter (no-Redis path).

    ``TenantMiddleware`` keeps a process-global fixed 60 s window per tenant id;
    many tests reuse tenant ids like ``tenant-a`` on the FREE plan, so whether a
    later test got a 429 depended on how fast the preceding tests ran.
    """
    from app.tenancy import middleware

    middleware._fallback_counters.clear()
    yield
    middleware._fallback_counters.clear()


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


@pytest.fixture(autouse=True)
def _reset_llm_config_store_singleton():
    """Unwire the process-global LLMConfigStore after each test.

    ``create_app()`` publishes its store via ``set_llm_config_store`` and the
    lifespan ``set_db``s it; a lifespan test with a fake session factory left
    that store behind, so later GoalService tests (whose app_state has no store
    and falls back to the singleton) made strict BYOK reads against the fake DB
    and failed with LLMConfigReadError — only in full-suite order.
    """
    yield
    try:
        import app.services.llm_config_store as _lcs

        _lcs._llm_config_store = None
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_db_engine_singleton():
    """Reset the module-level DB engine/session-factory singleton after each test.

    ``app.db.session._engine``/``_session_factory`` are lazily-created,
    process-wide singletons: the first call to ``get_session_factory()`` with
    no ``app.state.db_session_factory`` override (e.g. any endpoint's
    ``_get_db(request)`` fallback, taken by every test whose app doesn't wire a
    fake DB) builds a real ``AsyncEngine`` and caches it here for the rest of
    the process. asyncpg connections are bound to the event loop that was
    running when the pool was built, and pytest-asyncio (Mode.AUTO) gives each
    async test — and each plain ``TestClient`` call — its own loop. So an
    engine cached by one test leaks into a LATER test on a different loop,
    where using it raises "Event loop is closed" / "Future attached to a
    different loop" instead of the response the later test expects — this bit
    ``tests/api/test_governance_extra2.py::TestAuditIntegrity::
    test_verify_audit_integrity``, which passes in isolation but is order-
    sensitive in the full suite for exactly this reason. Dropping the
    references after every test forces the next one that needs a DB engine to
    build a fresh one bound to ITS OWN loop, matching how it behaves in
    isolation — the same hazard ``dispose_task_engine()`` exists to avoid for
    Celery workers, just applied per-test instead of per-worker-task.
    """
    yield
    try:
        import app.db.session as _db_session_mod

        _db_session_mod._engine = None
        _db_session_mod._session_factory = None
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
    "AUDIO_MODEL", "TRANSCRIPTION_MODEL", "VOICE_STT_PROVIDER", "VOICE_TTS_PROVIDER",
    "VOICE_STT_MODEL", "VOICE_TTS_MODEL",
)


def _cached_settings_environment() -> str | None:
    """ENVIRONMENT of the CACHED Settings, or None (nothing cached / not cacheable).

    Tests may monkeypatch get_settings with a plain function — no cache to read.
    """
    from app.core import config

    cache_info = getattr(config.get_settings, "cache_info", None)
    if cache_info is None or not cache_info().currsize:
        return None
    try:
        return str(config.get_settings().environment)
    except Exception:
        return None


@pytest.fixture(autouse=True)
def _reset_leaked_settings_environment():
    """Drop a cached Settings whose ENVIRONMENT a test changed.

    A test that monkeypatches ENVIRONMENT=production and calls get_settings()
    caches a production Settings; monkeypatch restores the env var but not the
    lru_cache, so every later test silently ran as production (durable-audit
    refusals, FakeProvider refusals) — order-dependent failures.
    """
    before = _cached_settings_environment()
    yield
    after = _cached_settings_environment()
    if after is not None and after != before:
        from app.core import config

        clear = getattr(config.get_settings, "cache_clear", None)
        if clear is not None:
            clear()


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


@pytest.fixture(autouse=True)
def _reset_no_db_store_fallbacks(request: pytest.FixtureRequest):
    """Clear the process-local fallbacks the DB-backed stores use without a database.

    Also unbinds the module-global PromptOptimizer from any test's database.

    ``app.org.runtime_store`` and ``app.intelligence.eval_suite_store`` keep
    tenant-keyed dicts for the no-DB path; test apps reuse fixed tenant ids, so
    without this one test's suites/roles/commands would appear in the next.
    """
    yield
    from app.intelligence import eval_suite_store
    from app.intelligence.prompt_optimizer import _default_optimizer
    from app.org import runtime_store

    # A lifespan test binds the process-global optimizer to its (fake) DB. Not in
    # the e2e tiers: there the session-scoped app bound it to the real database,
    # and unbinding it after the first test silently took every later test out
    # of DB mode.
    if "e2e" not in str(getattr(request.node, "path", "")):
        _default_optimizer.__dict__.pop("_db", None)
        # Same for the process-global cost-breakdown DB binding (lifespan / worker).
        from app.observability import cost_breakdown as _cost_breakdown

        _cost_breakdown.reset_db()

    for store in (
        eval_suite_store._MEM_SUITES,
        eval_suite_store._MEM_RUNS,
        runtime_store._MEM_ROLES,
        runtime_store._MEM_COMMANDS,
    ):
        store.clear()


@pytest.fixture(autouse=True)
def _restore_environment_variable():
    """Restore ENVIRONMENT after every test.

    Several tests set or pop it directly on os.environ; one left it unset,
    which made every later test that needs a dev environment (e.g. workflow
    webhook tokens) fail only in full-suite order.
    """
    saved = os.environ.get("ENVIRONMENT")
    yield
    if saved is None:
        os.environ.pop("ENVIRONMENT", None)
    else:
        os.environ["ENVIRONMENT"] = saved


@pytest.fixture(autouse=True)
def _restore_lifespan_db_singletons():
    """Undo DB factories a lifespan left bound to process-global singletons.

    See tests/_lifespan_singletons.py (the list, and why it exists).
    """
    from tests import _lifespan_singletons as singletons

    saved = singletons.snapshot()
    yield
    singletons.restore(saved)


class _GuardLocks:
    """In-memory stand-in for a Redis lock (SET NX EX/PX + Lua check-and-delete).

    Used for the beat guard lock and run_goal's per-goal execution lock.
    """

    def __init__(self) -> None:
        self.held: dict[str, str] = {}

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and key in self.held:
            return None
        self.held[key] = value
        return True

    def eval(self, _script: str, _numkeys: int, key: str, token: str) -> int:
        if self.held.get(key) == token:
            del self.held[key]
            return 1
        return 0


@pytest.fixture(autouse=True)
def _beat_guard_lock(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give guarded beat tasks a working overlap lock.

    The guard fails closed (skips the run) when it cannot take its Redis lock, and
    many tests monkeypatch ``redis.from_url`` with fakes that have no SET NX, so
    every guarded task would silently skip. test_beat_guard.py tests the real guard
    client and is left alone.
    """
    if request.module.__name__.endswith("test_beat_guard"):
        return
    import app.scaling.beat_guard as beat_guard  # string paths need it imported first

    locks = _GuardLocks()
    monkeypatch.setattr(beat_guard, "_guard_client", lambda *_a, **_k: locks)


# ── Ephemeral backends for tests that genuinely need Postgres / Redis ─────────
# See tests/_test_backends.py. Session-scoped and lazy: a container starts only
# when a test first asks for it. Tests using these must be marked ``integration``.


@pytest.fixture(scope="session")
def pg_url():
    """DSN of a migrated pgvector Postgres testcontainer (asyncpg driver)."""
    from tests._test_backends import migrated_postgres

    with migrated_postgres() as url:
        yield url


@pytest.fixture(scope="session")
def redis_url():
    """URL of a Redis testcontainer."""
    from tests._test_backends import redis_container

    with redis_container() as url:
        yield url


@pytest.fixture
def test_backends(pg_url, redis_url, monkeypatch):
    """Point the whole process at the testcontainers for one test.

    For code that reaches Postgres/Redis through process-global configuration
    (``get_settings()``, ``app.db.session.get_session_factory()``, the Celery
    task module's ``REDIS_URL``) rather than an injected factory. Everything is
    restored afterwards, so later tests are back behind the live-infra net.
    """
    from tests._test_backends import reset_db_singletons

    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    import app.scaling.celery_app as celery_app_mod
    import app.scaling.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "REDIS_URL", redis_url, raising=False)
    monkeypatch.setattr(celery_app_mod, "REDIS_URL", redis_url, raising=False)
    reset_db_singletons()
    yield pg_url, redis_url
    reset_db_singletons()



@pytest.fixture
def in_memory_goal_lock(monkeypatch: pytest.MonkeyPatch) -> _GuardLocks:
    """Give run_goal's per-goal execution lock an in-memory Redis stand-in.

    run_goal fails closed (retries, then fails the goal) when it cannot take its
    Redis lock. Unit tests of what happens after the lock use this instead of a
    Redis server; the lock itself is tested in tests/scaling/test_*lock*.py.
    """
    import app.scaling.tasks as tasks_mod

    locks = _GuardLocks()
    monkeypatch.setattr(tasks_mod, "_goal_lock_client", lambda _url: locks)
    return locks


class _ReadableControlRedis:
    """In-memory sync Redis that serves run_goal's control-plane reads.

    Holds no emergency stop and no cancel flag unless a test writes one. Any
    command it does not model raises ``ConnectionError``, i.e. behaves like the
    suite's deliberately unreachable Redis for everything else.
    """

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.sets: dict[str, set[str]] = {}
        self.published: list[tuple[str, Any]] = []

    def get(self, key: str) -> Any:
        return self.values.get(key)

    def set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> bool:
        self.values[key] = value
        return True

    def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.values.pop(k, None) is not None)

    def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.values)

    def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def sadd(self, key: str, *members: str) -> int:
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    def srem(self, key: str, *members: str) -> int:
        self.sets.get(key, set()).difference_update(members)
        return len(members)

    def scan_iter(self, *args: Any, **kwargs: Any) -> Any:
        return iter(())

    def publish(self, channel: str, message: Any) -> int:
        self.published.append((channel, message))
        return 0

    def __getattr__(self, name: str) -> Any:
        def _unavailable(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError(f"_ReadableControlRedis does not model {name!r}")

        return _unavailable


@pytest.fixture
def readable_emergency_stop(monkeypatch: pytest.MonkeyPatch) -> _ReadableControlRedis:
    """Give run_goal a READABLE emergency-stop state with no stop active.

    Since WF-16 the worker fails closed (retries, then records the goal as
    blocked) when it cannot read the stop flags, and the suite's Redis is
    deliberately unreachable. Unit tests of what happens after the start check
    use this; the fail-closed check itself is tested in
    tests/scaling/test_run_goal_estop_failclosed.py and test_worker_emergency_stop.py.
    A test that patches ``tasks._get_sync_redis`` itself overrides this.
    """
    import app.scaling.tasks as tasks_mod

    fake = _ReadableControlRedis()
    monkeypatch.setattr(tasks_mod, "_get_sync_redis", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _fresh_goal_deduplicator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own process-global goal deduplicator.

    ``GoalService.submit_goal`` wires the FIRST service's Redis into the module
    singleton (``_redis_wired``) and otherwise keeps 60 s in-memory claims, so a
    goal submitted by one test was "deduplicated" onto another test's goal with
    the same text in a full-suite run (order-dependent KeyErrors on the submit
    result). Tests that patch the deduplicator themselves still override this.
    """
    import app.services.dedup as dedup_mod

    monkeypatch.setattr(dedup_mod, "_default_deduplicator", dedup_mod.GoalDeduplicator())


@pytest.fixture(autouse=True)
def _restore_decision_cost_services():
    """Unregister the process-global decision cost services after each test.

    ``create_app()`` (and the worker's ``worker_init``) register them module-wide;
    since PROV-05 a decision call with no goal / tenant is refused while they are
    registered, so an app built by one test (or at module import) must not leak
    them into the next. Each test starts unregistered; tests that need them
    register them (or build an app) themselves.
    """
    from app.providers import guarded_completion as _gc

    _gc.set_platform_cost_services(None)
    yield
    _gc.set_platform_cost_services(None)


@pytest.fixture(autouse=True)
def _isolate_model_registry_store(monkeypatch):
    """Keep the process-global ModelRegistryStore per test (PROV-20).

    Worker goals read tenant routing policies from it and fail closed when it
    cannot be read; the suite's REDIS_URL is deliberately unreachable, so the
    worker must not auto-wire a store pointing there. Tests that exercise the
    store wire a fake one themselves.
    """
    from app.ai_router import registry_store as _rs

    saved = _rs.get_model_registry_store()
    _rs._store = None
    import app.scaling.tasks as _tasks_mod

    monkeypatch.setattr(_tasks_mod, "_wire_worker_model_registry_store", lambda: None)

    # A worker goal now FAILS when its goal-level model_override cannot be read
    # (the suite's DB is unreachable): default to "no override"; tests of the
    # lookup call _read_goal_model_override directly.
    async def _no_goal_override(goal_id: str, tenant_id: str) -> str:
        return ""

    monkeypatch.setattr(_tasks_mod, "_goal_model_override", _no_goal_override)
    yield
    _rs._store = saved


# Import-time environment leaks (found 2026-10-06): pytest imports EVERY test
# module during collection, including deselected opt-in suites. tests/real_e2e/*
# call load_dotenv(BACKEND_ROOT / ".env") at module level, which put the
# developer's real .env (provider keys, VISION_MODEL, egress allowlists) into the
# whole unit session — ~100 order-dependent failures (SSRF, routing, embedder,
# BYOK) and unit tests running with a real provider key. Restore the environment
# conftest set up, after collection, and drop settings cached from it. Only the
# changed key NAMES are reported, never values.
_ENV_AFTER_CONFTEST = dict(os.environ)
# Defaults the APPLICATION itself sets when its modules are imported (kept: the
# process really runs with them). Keep in sync with
# `grep -rn "^os.environ.setdefault" app`.
_APP_IMPORT_ENV_DEFAULTS = frozenset({"OMP_THREAD_LIMIT"})  # app/ocr/concurrency.py


def pytest_collection_finish(session: pytest.Session) -> None:
    changed = sorted(
        k
        for k in set(os.environ) | set(_ENV_AFTER_CONFTEST)
        if os.environ.get(k) != _ENV_AFTER_CONFTEST.get(k) and k not in _APP_IMPORT_ENV_DEFAULTS
    )
    if not changed:
        return
    kept = {k: os.environ[k] for k in _APP_IMPORT_ENV_DEFAULTS if k in os.environ}
    os.environ.clear()
    os.environ.update(_ENV_AFTER_CONFTEST)
    os.environ.update(kept)
    with contextlib.suppress(Exception):
        from app.core.config import get_settings

        get_settings.cache_clear()
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            f"[env-guard] restored {len(changed)} env var(s) changed while importing "
            f"test modules: {', '.join(changed[:20])}"
        )
