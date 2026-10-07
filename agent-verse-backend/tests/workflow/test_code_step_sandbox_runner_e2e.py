"""A workflow ``code`` step runs end-to-end in the remote code-sandbox runner.

Owner report: in the deployed workers (compose and Kubernetes) every code step
failed with "Docker sandbox unavailable ... Subprocess execution is disabled" —
there is no Docker daemon in a worker, and the unsandboxed fallback is (rightly)
off — while the run banner showed just ``''``.

Here the real runner (``python -m app.sandbox.runner``) is started as a
subprocess and the worker side is configured exactly like the shipped
deployments: CODE_SANDBOX_URL + CODE_SANDBOX_TOKEN, no Docker, subprocess
execution disabled. The run goes through the real runner -> compiler -> code
step -> governed execution (concurrency slots + audit row) -> CodeInterpreter ->
HTTP client -> runner.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import app.tools.code_interpreter as ci
from app.core.config import get_settings
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.engine_audit import audit_tenant_id
from app.workflow.runner import WorkflowRunner
from tests.workflow.test_run_failure_error import _RUN, _Store

BACKEND = Path(__file__).resolve().parents[2]
TOKEN = "e2e-sandbox-token-0123456789"
TENANT = "11111111-2222-3333-4444-555555555555"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def runner_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    tmp = tmp_path_factory.mktemp("code-sandbox")
    port = _free_port()
    log = (tmp / "runner.log").open("wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.sandbox.runner"],
        cwd=BACKEND,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(BACKEND),
            "CODE_SANDBOX_TOKEN": TOKEN,
            "CODE_SANDBOX_HOST": "127.0.0.1",
            "CODE_SANDBOX_PORT": str(port),
            "CODE_SANDBOX_WORKDIR": str(tmp / "work"),
        },
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(url + "/healthz", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                pass
            if proc.poll() is not None or time.monotonic() > deadline:
                raise AssertionError((tmp / "runner.log").read_text()[-2000:])
            time.sleep(0.2)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(autouse=True)
def audit_log(monkeypatch: pytest.MonkeyPatch) -> AuditLog:
    """Executions are durably audited; these tests use the in-memory log."""
    log = AuditLog()
    monkeypatch.setattr("app.tools.code_execution._default_audit_log", log)
    return log


@pytest.fixture
def deployed_worker(monkeypatch: pytest.MonkeyPatch, runner_url: str) -> Iterator[None]:
    """The worker env of the shipped deployments: a runner, no Docker, no host exec."""
    monkeypatch.setenv("CODE_SANDBOX_URL", runner_url)
    monkeypatch.setenv("CODE_SANDBOX_TOKEN", TOKEN)
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "false")

    def no_docker() -> bool:
        raise AssertionError("Docker must not be consulted when a runner is configured")

    monkeypatch.setattr(ci, "_docker_available", no_docker)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _OutputStore(_Store):
    def __init__(self, definition: dict[str, Any]) -> None:
        super().__init__(definition)
        self.outputs: dict[str, Any] = {}

    async def record_step_finish(
        self, *, step_id: str, status: Any, error: str | None = None, **kw: Any
    ) -> bool:
        self.outputs[step_id] = kw.get("output")
        return await super().record_step_finish(step_id=step_id, status=status, error=error)


async def _run(steps: list[StepDefinition]) -> _OutputStore:
    store = _OutputStore(WorkflowDefinition(name="sandboxed", id="wf", steps=steps).to_json())
    runner = WorkflowRunner(
        compiler=WorkflowCompiler(ContextResolver(), run_store=store), run_store=store
    )
    await runner.execute_fresh(_RUN, "wf", TENANT)
    return store


@pytest.mark.usefixtures("deployed_worker")
async def test_code_steps_run_in_the_remote_sandbox(audit_log: AuditLog) -> None:
    store = await _run(
        [
            StepDefinition(
                id="create_message",
                type="code",
                runtime="python",
                code=(
                    "import os\n"
                    "output = {'doubled': inputs['n'] * 2, 'cwd': os.getcwd(),\n"
                    "          'env': sorted(os.environ)}"
                ),
                input={"n": 21},
                on_failure="abort",
            ),
            StepDefinition(
                id="add_one",
                type="code",
                runtime="python",
                code="output = {'total': int(inputs['d']) + 1}",
                input={"d": "{{steps.create_message.output.doubled}}"},
                depends_on=["create_message"],
                on_failure="abort",
            ),
        ]
    )

    assert store.run["status"] == "complete", store.run
    first = store.outputs["create_message"]
    assert first["doubled"] == 42
    assert "/run-" in first["cwd"]  # a private runner workdir, not the worker's cwd
    # Nothing of the worker's environment (DB URL, Redis, keys) reaches the program.
    assert not {"DATABASE_URL", "REDIS_URL", "CODE_SANDBOX_TOKEN"} & set(first["env"])
    assert store.outputs["add_one"] == {"total": 43}
    # Each execution is durably audited against the run's tenant.
    tenant = TenantContext(tenant_id=audit_tenant_id(TENANT), plan=PlanTier.FREE, api_key_id="k")
    events = audit_log.query(tenant_ctx=tenant)
    assert [e.tool_name for e in events] == ["code_interpreter.python"] * 2
    assert all(e.outcome == "success" for e in events)


@pytest.mark.usefixtures("deployed_worker")
async def test_failing_code_step_error_reaches_the_run() -> None:
    store = await _run(
        [
            StepDefinition(
                id="create_message",
                type="code",
                runtime="python",
                code="raise ValueError('template variable missing')",
                on_failure="abort",
            )
        ]
    )
    assert store.run["status"] == "failed"
    assert store.run["error_step_id"] == "create_message"
    error = store.run["error"]
    assert "ValueError: template variable missing" in error
    assert error.strip("'\" ")  # never the bare "''" banner
    assert error == store.steps["create_message"]["error"]


@pytest.mark.usefixtures("deployed_worker")
async def test_paused_run_carries_the_step_error() -> None:
    store = await _run(
        [
            StepDefinition(
                id="create_message",
                type="code",
                runtime="python",
                code="import sys\nsys.exit(3)",
            )
        ]
    )
    assert store.run["status"] == "paused"
    assert store.run["error_step_id"] == "create_message"
    assert "code step 'create_message' failed" in store.run["error"]


async def test_wrong_token_fails_the_step_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, runner_url: str
) -> None:
    monkeypatch.setenv("CODE_SANDBOX_URL", runner_url)
    monkeypatch.setenv("CODE_SANDBOX_TOKEN", "not-the-runner-token-at-all")
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")  # must still not run on the host
    get_settings.cache_clear()
    try:
        store = await _run(
            [
                StepDefinition(
                    id="create_message",
                    type="code",
                    runtime="python",
                    code="output = 1",
                    on_failure="abort",
                )
            ]
        )
    finally:
        get_settings.cache_clear()
    assert store.run["status"] == "failed"
    assert "rejected the credentials" in store.run["error"]
    assert store.run["error_step_id"] == "create_message"
