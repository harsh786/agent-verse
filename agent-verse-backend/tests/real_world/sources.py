"""Knowledge-source (connector) helpers for the real-world suite."""

from __future__ import annotations

from typing import Any

from tests.real_world.helpers import LiveAPI, mask, tag, wait_until

SYNC_DONE = {"completed", "complete", "succeeded", "success", "failed", "error",
             "partial", "cancelled"}


def create_collection(api: LiveAPI, cleanup: Any, prefix: str) -> str:
    body = api.json_ok("POST", "/knowledge/collections",
                       json={"name": f"{prefix}-{tag()}", "embedder_type": "default"})
    cid = str(body.get("collection_id") or body.get("id"))
    cleanup("DELETE", f"/knowledge/collections/{cid}")
    return cid


def create_source(api: LiveAPI, cleanup: Any, *, family: str, source_type: str,
                  config: dict[str, Any], collection_id: str) -> dict[str, Any]:
    resp = api.post("/sources", json={
        "name": f"rw-{source_type}-{tag()}", "family": family, "source_type": source_type,
        "connection_config": config, "collection_id": collection_id, "sync_mode": "full",
        "sync_interval_seconds": 86400, "pii_action": "none", "min_quality_score": 0.0,
    })
    assert resp.status_code in (200, 201), (
        f"POST /sources ({source_type}) -> {resp.status_code}: {mask(resp.text[:400])}"
    )
    src = resp.json()
    sid = str(src.get("source_id") or src.get("id"))
    cleanup("DELETE", f"/sources/{sid}")
    return {"id": sid, **src}


def sync_and_wait(api: LiveAPI, source_id: str, timeout: float = 240) -> dict[str, Any]:
    resp = api.post(f"/sources/{source_id}/sync")
    assert resp.status_code in (200, 202), (
        f"sync -> {resp.status_code}: {mask(resp.text[:400])}"
    )
    queued = resp.json()
    if queued.get("status") not in ("queued", "already_running"):
        return {"queued": queued}
    status = wait_until(
        lambda: api.json_ok("GET", f"/sources/{source_id}/sync/status"),
        timeout=timeout, interval=4, desc=f"sync of source {source_id}",
        done=lambda s: str(s.get("status", "")).lower() in SYNC_DONE,
    )
    return {"queued": queued, "status": status}


def documents(api: LiveAPI, cid: str) -> list[dict[str, Any]]:
    body = api.json_ok("GET", f"/knowledge/collections/{cid}/documents")
    return list(body.get("documents", []) if isinstance(body, dict) else body)


def search(api: LiveAPI, cid: str, q: str, top_k: int = 5) -> list[dict[str, Any]]:
    body = api.json_ok("GET", "/knowledge/search",
                       params={"q": q, "collection_id": cid, "top_k": top_k})
    return list(body if isinstance(body, list) else body.get("results", []))
