"""e2e_full (WF-35): a workflow HTTP step to cloud metadata fails in a real worker.

The workflow-local SSRF guard let ``http://[::ffff:169.254.169.254]/`` (IPv4-mapped
IPv6) and ``http://100.100.100.200/`` (Alibaba metadata, CGNAT range) through.
The step now uses the central guard + a pinned client, so the out-of-process
worker refuses the request and the run stops on that step with an SSRF error.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from tests.e2e_full.test_workflow_run_e2e import celery_worker  # noqa: F401

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"
_SETTLED = {"complete", "failed", "cancelled", "timed_out", "paused", "waiting_hitl"}


@pytest.mark.parametrize(
    "url",
    ["http://[::ffff:169.254.169.254]/latest/meta-data/", "http://100.100.100.200/latest/"],
)
async def test_http_step_to_metadata_fails_the_step(
    tenant_client: Any,
    celery_worker: dict[str, Any],  # noqa: F811
    url: str,
) -> None:
    definition = {
        "name": "ssrf probe",
        "steps": [{"id": "probe", "type": "http", "url": url, "method": "GET", "timeout": "5s"}],
    }
    resp = await tenant_client.post(
        f"{_API}/workflows",
        json={"name": f"ssrf-{uuid.uuid4().hex[:8]}", "definition": definition},
    )
    assert resp.status_code == 201, resp.text
    trig = await tenant_client.post(
        f"{_API}/workflows/{resp.json()['id']}/trigger", json={"inputs": {}}
    )
    assert trig.status_code == 202, trig.text
    run_id = trig.json()["run_id"]

    run: dict[str, Any] = {}
    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        r = await tenant_client.get(f"{_API}/runs/{run_id}")
        if r.status_code == 200:
            run = r.json()
            if str(run.get("status")) in _SETTLED:
                break
        await asyncio.sleep(1.0)

    assert run.get("status") in {"paused", "failed"}, f"expected stopped run, got {run!r}"
    assert "SSRF" in str(run.get("error") or ""), run
