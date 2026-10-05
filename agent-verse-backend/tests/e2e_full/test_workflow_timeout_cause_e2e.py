"""e2e_full (WF-TIMEOUT-MISREPORT): an inner timeout is reported as itself.

The compiler wrapped each step in ``asyncio.wait_for(step deadline)`` and its
``except TimeoutError`` also caught TimeoutErrors raised INSIDE the step — the
provider's own generation timeout (``AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS``,
60 s live) — so a run failed after 61 s with "step 'draft_report' exceeded
timeout 180s". The step deadline and an inner timeout are now told apart and
the real cause plus the elapsed time are reported.

Real app + Postgres + Redis; the run executes in-process with a provider that
hangs, through the real guarded completion path.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.e2e_full._wf_worker import API, create_workflow, poll_run

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


class _HangingProvider:
    name = "hanging"

    async def complete(self, request: Any) -> Any:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")


@pytest.fixture
def _inprocess_with_hanging_llm(app: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    runner = app.state.workflow_runner
    compiler = runner._compiler
    prev_celery = runner._celery
    prev_services = dict(compiler._services)
    prev_cache = dict(compiler._cache)
    runner._celery = None
    compiler._cache.clear()
    compiler._services["llm_provider"] = _HangingProvider()
    compiler._services["provider"] = compiler._services["llm_provider"]
    monkeypatch.setenv("AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS", "0.5")
    try:
        yield
    finally:
        runner._celery = prev_celery
        compiler._services.clear()
        compiler._services.update(prev_services)
        compiler._cache.clear()
        compiler._cache.update(prev_cache)


async def test_inner_llm_timeout_is_not_reported_as_the_step_deadline(
    tenant_client: Any, _inprocess_with_hanging_llm: None
) -> None:
    wid = await create_workflow(
        tenant_client,
        {
            "name": "Timeout cause",
            "steps": [
                {
                    "id": "draft_report",
                    "type": "llm",
                    "prompt": "Write the weekly report.",
                    "timeout": "30s",
                    "on_failure": "abort",
                }
            ],
        },
        name=f"timeout-{uuid.uuid4().hex[:8]}",
    )
    trig = await tenant_client.post(
        f"{API}/workflows/{wid}/trigger", json={"inputs": {}, "dry_run": False}
    )
    assert trig.status_code == 202, trig.text
    run = await poll_run(tenant_client, str(trig.json()["run_id"]), {"failed"}, timeout=60)

    error = run.get("error") or ""
    assert "exceeded timeout 30s" not in error, error
    assert "draft_report" in error
    assert "30s step deadline was not reached" in error, error
    # The elapsed time is the real one (~0.5 s), not the 30 s deadline.
    elapsed = float(error.split("failed after ")[1].split("s")[0])
    assert elapsed < 10, error
