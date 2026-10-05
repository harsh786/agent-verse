"""Source sync helpers that follow ONE sync job by id (SRC-OBJ-* / SRC-DB-*).

``sources.sync_and_wait`` polls ``/sync/status`` (the newest job), which right after
queuing can still be the PREVIOUS run — a second sync then "completes" instantly with
the first run's numbers. These helpers wait for the job id the trigger returned.
"""

from __future__ import annotations

import time
from typing import Any

from tests.real_world.helpers import LiveAPI, mask, tag, wait_until

TERMINAL = {"completed", "complete", "succeeded", "success", "failed", "error", "partial",
            "cancelled"}
COMPLETED = {"completed", "complete", "succeeded", "success"}
JOB_FIELDS = ("status", "docs_discovered", "docs_indexed", "docs_skipped", "docs_failed",
              "chunks_created", "cursor_before", "cursor_after", "error_message",
              "started_at", "completed_at")


def _same(a: Any, b: Any) -> bool:
    return str(a or "").replace("-", "").lower() == str(b or "").replace("-", "").lower()


def job(api: LiveAPI, sid: str, job_id: str) -> dict[str, Any] | None:
    for j in api.json_ok("GET", f"/sources/{sid}/sync/history", params={"limit": 50}):
        if _same(j.get("job_id"), job_id):
            return dict(j)
    return None


def jobs(api: LiveAPI, sid: str, limit: int = 50) -> list[dict[str, Any]]:
    return [dict(j) for j in api.json_ok("GET", f"/sources/{sid}/sync/history",
                                         params={"limit": limit})]


def trigger(api: LiveAPI, sid: str, *, wait_free: float = 600) -> str:
    """Queue a sync and return its job id (waits out a sync already running)."""
    deadline = time.monotonic() + wait_free
    while True:
        resp = api.post(f"/sources/{sid}/sync")
        assert resp.status_code in (200, 202), f"sync -> {resp.status_code}: {mask(resp.text)}"
        body = resp.json()
        if body.get("status") == "queued" and body.get("job_id"):
            return str(body["job_id"])
        assert body.get("status") == "already_running", f"unexpected sync answer {body}"
        assert time.monotonic() < deadline, "a previous sync never finished"
        time.sleep(4)


def wait_job(api: LiveAPI, sid: str, job_id: str, timeout: float = 600) -> dict[str, Any]:
    started = time.monotonic()
    final = wait_until(lambda: job(api, sid, job_id), timeout=timeout, interval=3,
                       desc=f"sync job {job_id}",
                       done=lambda j: bool(j) and str(j.get("status", "")).lower() in TERMINAL)
    out = {k: final.get(k) for k in JOB_FIELDS}
    out["job_id"] = job_id
    out["wall_s"] = round(time.monotonic() - started, 1)
    return out


def sync(api: LiveAPI, sid: str, timeout: float = 600) -> dict[str, Any]:
    """Trigger a sync and wait for THAT job; returns its masked summary."""
    return mask_job(wait_job(api, sid, trigger(api, sid), timeout))


def mask_job(j: dict[str, Any]) -> dict[str, Any]:
    out = dict(j)
    if out.get("error_message"):
        out["error_message"] = mask(out["error_message"])[:600]
    return out


def create_source(api: LiveAPI, cleanup: Any, *, family: str, source_type: str,
                  config: dict[str, Any], collection_id: str, expect: int = 201,
                  **extra: Any) -> dict[str, Any]:
    body = {"name": f"rw-{source_type}-{tag()}", "family": family, "source_type": source_type,
            "connection_config": config, "collection_id": collection_id,
            "sync_mode": "incremental", "sync_interval_seconds": 86400, "pii_action": "none",
            "min_quality_score": 0.0, **extra}
    resp = api.post("/sources", json=body)
    assert resp.status_code == expect, (
        f"POST /sources ({source_type}) -> {resp.status_code} (expected {expect}): "
        f"{mask(resp.text[:400])}")
    out = resp.json() if resp.content else {}
    if resp.status_code < 300:
        sid = str(out.get("source_id") or out.get("id"))
        cleanup("DELETE", f"/sources/{sid}")
        out["id"] = sid
    out["_http"] = resp.status_code
    return dict(out)


def validate(api: LiveAPI, *, family: str, source_type: str, config: dict[str, Any],
             collection_id: str = "") -> dict[str, Any]:
    resp = api.post("/sources/validate", json={
        "name": f"rw-validate-{tag()}", "family": family, "source_type": source_type,
        "connection_config": config, "collection_id": collection_id})
    body = resp.json() if resp.content else {}
    return {"http": resp.status_code, **(body if isinstance(body, dict) else {"raw": body})}


def dlq(api: LiveAPI, sid: str, include_resolved: bool = False) -> list[dict[str, Any]]:
    return list(api.json_ok("GET", "/ingestion/dlq",
                            params={"source_id": sid, "include_resolved": include_resolved,
                                    "limit": 500}))


def reconcile(api: LiveAPI, sid: str) -> str:
    resp = api.post(f"/sources/{sid}/reconcile")
    assert resp.status_code == 202, f"reconcile -> {resp.status_code}: {mask(resp.text[:300])}"
    return str(resp.json().get("status"))
