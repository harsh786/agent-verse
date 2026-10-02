"""Real-world scenario suite: runs against the LIVE local Docker stack.

Opt-in only — every test here is skipped unless ``AGENTVERSE_REAL_WORLD=1``:

    AGENTVERSE_REAL_WORLD=1 AGENTVERSE_TENANT_FILE=/path/tenant.json \
        uv run pytest tests/real_world -m real_world --no-cov -W default

or ``scripts/run_real_world.sh <report-dir>`` (backend + Playwright + report).

Each test records a result row (scenario, outcome, duration, evidence, failure
detail) as JSON lines in ``RW_RESULTS_FILE`` when set; the runner turns those
into the JSON + markdown report. The API key is never printed: every string
that reaches a report goes through :func:`helpers.mask`.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.real_world.helpers import LiveAPI, docker_logs, load_api_key, mask

ENABLED = os.getenv("AGENTVERSE_REAL_WORLD") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="real-world suite: set AGENTVERSE_REAL_WORLD=1 (live stack)")
    for item in items:
        if not str(item.fspath).startswith(here):
            continue
        item.add_marker(pytest.mark.real_world)
        if not ENABLED:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def api_key() -> str:
    key = load_api_key()
    if not key:
        pytest.fail("no tenant key: set AGENTVERSE_API_KEY or AGENTVERSE_TENANT_FILE")
    return key


@pytest.fixture(scope="session")
def api(api_key: str) -> Iterator[LiveAPI]:
    client = LiveAPI(api_key)
    health = client.get("/health")
    if health.status_code != 200:
        pytest.fail(f"live stack not healthy: /health -> {health.status_code}")
    yield client
    client.close()


@pytest.fixture(scope="session")
def tenant_id(api: LiveAPI) -> str:
    return str(api.json_ok("GET", "/tenants/me")["tenant_id"])


@pytest.fixture(scope="session")
def embedder_info(api: LiveAPI) -> dict[str, Any]:
    caps = (api.json_ok("GET", "/health").get("capabilities") or {}).get("embedder") or {}
    return dict(caps)


@pytest.fixture
def evidence(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Per-test evidence dict that lands in the report row (masked)."""
    ev: dict[str, Any] = {}
    request.node._rw_evidence = ev  # type: ignore[attr-defined]
    return ev


@pytest.fixture
def cleanup(api: LiveAPI) -> Iterator[Callable[[str, str], None]]:
    """Register ``(method, path)`` calls that delete what a test created (LIFO)."""
    todo: list[tuple[str, str]] = []

    def add(method: str, path: str) -> None:
        todo.append((method, path))

    yield add
    for method, path in reversed(todo):
        with contextlib.suppress(Exception):
            api.request(method, path)


# ── Shared fixtures of the complex scenarios ───────────────────────────────


@pytest.fixture(scope="session")
def fixture_server() -> Iterator[Any]:
    """The local HTTP fixture server (counters, flaky / failing endpoints, feeds)."""
    from tests.real_world.fixture_server import FixtureServer

    server = FixtureServer().start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def corpus() -> list[Any]:
    """The generated Larkspur corpus (deterministic; built once per session)."""
    from tests.real_world.corpus import build_corpus

    return build_corpus()


@pytest.fixture(scope="session")
def complex_kb(api: LiveAPI, corpus: list[Any]) -> Iterator[dict[str, Any]]:
    """One collection holding the whole corpus, shared by the read-only KB scenarios
    (retrieval, strategies, multi-step RAG goal). Upload results are kept per file."""
    from tests.real_world import kb

    cid = kb.create_collection(api, "rw-complex-kb")
    uploads = {d.filename: kb.upload(api, cid, d) for d in corpus}
    yield {"collection_id": cid, "docs": corpus, "uploads": uploads}
    with contextlib.suppress(Exception):
        api.delete(f"/knowledge/collections/{cid}")


def _optional_client(var: str, file_var: str) -> Iterator[LiveAPI | None]:
    from tests.real_world.helpers import key_from_env

    key = key_from_env(var, file_var)
    if not key:
        yield None
        return
    client = LiveAPI(key)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def second_tenant_api() -> Iterator[LiveAPI | None]:
    """A key of a DIFFERENT tenant (RW_SECOND_TENANT_API_KEY / RW_SECOND_TENANT_FILE)."""
    yield from _optional_client("RW_SECOND_TENANT_API_KEY", "RW_SECOND_TENANT_FILE")


@pytest.fixture(scope="session")
def enterprise_api() -> Iterator[LiveAPI | None]:
    """A key of an enterprise-plan tenant (short schedule floors, high rate limits)."""
    yield from _optional_client("RW_ENTERPRISE_API_KEY", "RW_ENTERPRISE_TENANT_FILE")


@pytest.fixture(scope="session", autouse=True)
def _sweep_leftovers(request: pytest.FixtureRequest) -> Iterator[None]:
    """At session end, delete ``rw-*`` objects a crashed/aborted test left behind.

    Only touches the test tenant (the key's own tenant) and only objects whose
    name carries the suite's ``rw-`` prefix.
    """
    yield
    if not ENABLED:
        return
    key = load_api_key()
    if not key:
        return
    sweeper = LiveAPI(key, timeout=30)
    try:
        with contextlib.suppress(Exception):
            body = sweeper.get("/api/v1/approvals", params={"per_page": 100}).json()
            me = sweeper.get("/tenants/me").json().get("tenant_id", "")
            for item in body.get("items", []):
                if not str(item.get("workflow_name", "")).startswith("rw-"):
                    continue
                if str(item.get("tenant_id", "")).replace("-", "") != me.replace("-", ""):
                    continue
                run = sweeper.get(f"/api/v1/runs/{item.get('run_id')}").json()
                if run.get("status") != "waiting_hitl":  # orphaned gate of a dead run
                    sweeper.post(f"/api/v1/approvals/{item['request_id']}/decide",
                                 json={"action": "reject", "note": "rw suite cleanup"})
        with contextlib.suppress(Exception):
            wfs = sweeper.get("/api/v1/workflows", params={"per_page": 100}).json()
            for wf in wfs.get("items", []):
                if str(wf.get("name", "")).startswith("rw-"):
                    sweeper.post(f"/api/v1/workflows/{wf['id']}/unpublish")
                    sweeper.delete(f"/api/v1/workflows/{wf['id']}")
        with contextlib.suppress(Exception):
            cols = sweeper.get("/knowledge/collections").json()
            cols = cols.get("collections", cols) if isinstance(cols, dict) else cols
            for col in cols or []:
                if str(col.get("name", "")).startswith("rw-"):
                    cid = col.get("collection_id") or col.get("id")
                    sweeper.delete(f"/knowledge/collections/{cid}")
        with contextlib.suppress(Exception):
            agents = sweeper.get("/agents").json()
            agents = agents.get("agents", agents.get("items", agents)) if isinstance(
                agents, dict) else agents
            for ag in agents or []:
                if str(ag.get("name", "")).startswith("rw-"):
                    sweeper.delete(f"/agents/{ag.get('agent_id') or ag.get('id')}")
        with contextlib.suppress(Exception):
            for src in sweeper.get("/sources").json() or []:
                if str(src.get("name", "")).startswith("rw-"):
                    sweeper.delete(f"/sources/{src.get('source_id') or src.get('id')}")
    finally:
        sweeper.close()


# ── Result rows for the report ─────────────────────────────────────────────


def _scenario_of(item: pytest.Item) -> str:
    marker = item.get_closest_marker("scenario")
    if marker and marker.args:
        return str(marker.args[0])
    return item.name


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()
    if not str(item.fspath).startswith(os.path.dirname(__file__)):
        return
    # One row per test: the call phase, or setup when it failed/skipped there.
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        ev: dict[str, Any] = getattr(item, "_rw_evidence", {}) or {}
        result = report.outcome
        if hasattr(report, "wasxfail"):
            result = "xfail" if report.skipped else "xpass"
        detail = ""
        if report.failed or result == "xfail":
            detail = mask(str(report.longreprtext or getattr(report, "wasxfail", "")))[-2500:]
            needles = [str(v) for k, v in ev.items() if k.endswith("_id") and v][:6]
            if needles and report.failed:
                ev.setdefault("docker_logs", docker_logs(needles))
        elif report.skipped:
            detail = mask(str(report.longrepr[-1] if isinstance(report.longrepr, tuple)
                              else report.longrepr))[:500]
        row = {
            "suite": "backend",
            "scenario": _scenario_of(item),
            "test": item.nodeid,
            "result": result,
            "duration_s": round(report.duration, 1),
            "evidence": json.loads(mask(ev)) if ev else {},
            "failure_detail": detail,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        path = os.getenv("RW_RESULTS_FILE")
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
