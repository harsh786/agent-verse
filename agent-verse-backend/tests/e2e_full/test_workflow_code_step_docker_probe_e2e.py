"""e2e_full (WF-CODE-STEP-SUBPROCESS): a worker that could not reach Docker
once keeps no memory of it — the code step runs in the sandbox as soon as the
daemon is reachable, deterministically.

Root cause: ``app.tools.code_interpreter`` probed Docker ONCE at import time
and cached the answer for the life of the process. A worker whose first import
happened while the daemon was briefly unreachable (here: the colima VM resuming
from sleep) was pinned to the unsandboxed subprocess fallback, which is
disabled, so its code steps failed with "Subprocess execution is disabled"
while other workers ran them fine — intermittent, per process.

Reproduction: the worker's DOCKER_HOST points at a socket path that does not
exist yet; a first run imports the interpreter while Docker is unreachable;
then the socket appears (a symlink to the real daemon) and repeated concurrent
runs must all execute in the Docker sandbox. Real app + Postgres + Redis +
out-of-process worker + the real Docker daemon.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.e2e_full._wf_worker import API, create_workflow, poll_run, workflow_worker

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_REAL_SOCKET = (os.getenv("DOCKER_HOST") or "").removeprefix("unix://")


@pytest.fixture(scope="module")
def late_docker_socket() -> Iterator[Path]:
    if not _REAL_SOCKET or not Path(_REAL_SOCKET).exists():
        pytest.skip("needs a local Docker daemon socket in DOCKER_HOST")
    # A unix socket path must stay under ~104 bytes, so this one lives in /tmp
    # (removed at teardown) rather than in the long pytest tmp dir.
    folder = Path(tempfile.mkdtemp(prefix="avdk-", dir="/tmp"))
    try:
        yield folder / "d.sock"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture(scope="module")
def celery_worker(
    app: Any, tmp_path_factory: Any, late_docker_socket: Path
) -> Iterator[dict[str, Any]]:
    with workflow_worker(
        tmp_path_factory.mktemp("dockerprobeworker"),
        name="wfdockere2e",
        extra_env={
            "DOCKER_HOST": f"unix://{late_docker_socket}",
            "AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "false",
        },
    ) as w:
        yield w


_CODE_WF = {
    "name": "Code step",
    "inputs": {"n": {"type": "number", "required": False, "default": 21}},
    "steps": [
        {
            "id": "double",
            "type": "code",
            "runtime": "python",
            "code": "output = {'doubled': int(inputs['n']) * 2}",
            "input": {"n": "{{inputs.n}}"},
            "on_failure": "abort",
        }
    ],
}


async def _trigger(client: Any, wid: str, n: int) -> str:
    resp = await client.post(
        f"{API}/workflows/{wid}/trigger", json={"inputs": {"n": n}, "dry_run": False}
    )
    assert resp.status_code == 202, resp.text
    return str(resp.json()["run_id"])


async def test_code_step_recovers_once_docker_becomes_reachable(
    app: Any, tenant_client: Any, celery_worker: dict[str, Any], late_docker_socket: Path
) -> None:
    # Four triggers in a row exceed the free plan's trigger rate limit.
    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    await app.state.tenant_service.update_plan(tenant_id, "enterprise")
    wid = await create_workflow(tenant_client, _CODE_WF, name=f"code-{uuid.uuid4().hex[:8]}")

    # 1. Docker unreachable: the step fails, honestly naming the sandbox.
    first = await _trigger(tenant_client, wid, 1)
    failed = await poll_run(tenant_client, first, {"failed", "complete"}, timeout=120)
    assert failed["status"] == "failed", failed
    assert "docker" in (failed.get("error") or "").lower(), failed

    # 2. The daemon becomes reachable (VM resumed).
    os.symlink(_REAL_SOCKET, late_docker_socket)
    # A failed probe is remembered for _DOCKER_RETRY_SECONDS (so a down daemon is
    # not pinged on every call); runs landing inside that window still fail.
    # The contract is "the sandbox is used once Docker is reachable and that
    # window has passed" — never "pinned for the life of the process".
    from app.tools.code_interpreter import _DOCKER_RETRY_SECONDS

    await asyncio.sleep(_DOCKER_RETRY_SECONDS + 1.0)

    # 3. Repeated, concurrent runs on the same worker process all run in the
    #    sandbox — none is pinned to the disabled subprocess fallback.
    run_ids = await asyncio.gather(*[_trigger(tenant_client, wid, n) for n in (2, 3, 4)])
    for n, run_id in zip((2, 3, 4), run_ids, strict=True):
        done = await poll_run(tenant_client, run_id, {"complete", "failed"}, timeout=180)
        assert done["status"] == "complete", done
        steps = (await tenant_client.get(f"{API}/runs/{run_id}/steps")).json()
        output = next(s for s in steps if s["step_id"] == "double")["output"]
        assert output == {"doubled": n * 2}, steps
