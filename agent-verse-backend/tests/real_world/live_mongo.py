"""Shared live-MongoDB plumbing of the MONGO-PIPELINE / CHAOS / SCALE scenarios.

The throwaway replica set ``rw-mongo`` (see ``test_src_mongodb.py``) holds two
databases with least-privilege users:

* ``rw_p1c`` — the knowledge Source's database; the stack reads it as ``rwreader``.
* ``rw_shop`` — the operational database the built-in MongoDB MCP connector works on
  as ``rwtool`` (``readWrite``): the workflow's read-only aggregate and its gated
  ``mongodb_insert_one`` side effect, counted in MongoDB itself.

Seeding, user management (a per-run user whose role is revoked / re-granted) and
side-effect counting run from this host as ``rwroot`` through the published port.
Nothing here imports ``app``.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

from tests.real_world import kb
from tests.real_world import source_jobs as sj
from tests.real_world.helpers import LiveAPI, mask, register_secret, tag, wait_until

FAMILY = "nosql_database"
SOURCE_DB = "rw_p1c"
TOOL_DB = "rw_shop"
RS_URI = "mongodb://rw-mongo:27017/?replicaSet=rs0"
RS_HOST = "rw-mongo:27017"


def env_secret(name: str, why: str = "the throwaway MongoDB replica set (rw-mongo)") -> str:
    import pytest

    value = os.getenv(name, "")
    if not value:
        pytest.skip(f"needs {name}: {why}")
    register_secret(value)
    return value


def seed_client(port_var: str = "RW_MONGO_SEED_PORT", default: str = "57017") -> Any:
    import pymongo

    return pymongo.MongoClient(
        f"mongodb://127.0.0.1:{os.getenv(port_var, default)}/", directConnection=True,
        serverSelectionTimeoutMS=8000, username="rwroot",
        password=env_secret("RW_MONGO_ROOT_PASSWORD"), authSource="admin")


def reader_config(collections: list[str], *, database: str = SOURCE_DB,
                  username: str = "rwreader", password: str | None = None,
                  **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "uri": RS_URI, "username": username,
        "password": password if password is not None else env_secret("RW_MONGO_READER_PASSWORD"),
        "database": database, "collections": collections, "batch_size": 500}
    cfg.update(over)
    return cfg


def create_source(api: LiveAPI, cleanup: Any, cid: str, cfg: dict[str, Any], *,
                  pii_action: str = "none", expect: int = 201) -> dict[str, Any]:
    return sj.create_source(api, cleanup, family=FAMILY, source_type="mongodb", config=cfg,
                            collection_id=cid, expect=expect, pii_action=pii_action)


# ── Inventory: what the platform holds for a Source / collection ────────────


def source_documents(api: LiveAPI, sid: str, page: int = 500) -> list[dict[str, Any]]:
    """Every indexed document of a Source (``GET /ingestion/documents``, keyset paged)."""
    out: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        params: dict[str, Any] = {"source_id": sid, "limit": page}
        if after:
            params["after"] = after
        batch = list(api.json_ok("GET", "/ingestion/documents", params=params) or [])
        out.extend(batch)
        if len(batch) < page:
            return out
        after = str(batch[-1].get("doc_id") or batch[-1].get("id"))


def url_tail(url: str) -> str:
    """``/<db>/<collection>/<quoted _id>`` of a ``mongodb://host/db/coll/id`` URL."""
    rest = url.split("://", 1)[-1]
    return "/" + rest.split("/", 1)[-1] if "/" in rest else ""


def expected_tails(database: str, names: dict[str, str], keys: dict[str, list[str]]
                   ) -> set[str]:
    return {f"/{database}/{names[lg]}/{quote(k, safe='')}" for lg, ks in keys.items()
            for k in ks}


def inventory(api: LiveAPI, sid: str, cid: str, expected: set[str]) -> dict[str, Any]:
    """Exactness report: missing / extra / duplicated documents, chunk accounting."""
    from tests.real_world.commerce_seed import duplicates

    docs = source_documents(api, sid)
    ids = [str(d.get("doc_id") or d.get("id")) for d in docs]
    tails = [url_tail(str(d.get("source_url") or "")) for d in docs]
    stats = api.get(f"/knowledge/collections/{cid}/stats")
    stats_body = stats.json() if stats.status_code == 200 else {"http": stats.status_code}
    listed = kb.documents_page(api, cid, 1, 0)
    return {
        "documents": len(docs),
        "expected": len(expected),
        "missing": sorted(expected - set(tails))[:20],
        "missing_count": len(expected - set(tails)),
        "extra": sorted(set(tails) - expected)[:20],
        "extra_count": len(set(tails) - expected),
        "duplicate_ids": len(duplicates(ids)),
        "duplicate_urls": sorted(duplicates(tails))[:10],
        "chunks_by_documents": sum(int(d.get("chunk_count") or 0) for d in docs),
        "collection_stats": {k: stats_body.get(k) for k in ("doc_count", "chunk_count")}
        if isinstance(stats_body, dict) else stats_body,
        "collection_listing_total": listed.get("total"),
        "pii_redacted_docs": [url_tail(str(d.get("source_url"))) for d in docs
                              if d.get("has_pii_redacted")],
        "content_hash": {url_tail(str(d.get("source_url"))): d.get("content_hash")
                         for d in docs},
    }


def exactness_problems(inv: dict[str, Any]) -> list[str]:
    soft = []
    if inv["missing_count"]:
        soft.append(f"{inv['missing_count']} source documents not indexed (e.g. "
                    f"{inv['missing'][:3]})")
    if inv["extra_count"]:
        soft.append(f"{inv['extra_count']} indexed documents with no source document (e.g. "
                    f"{inv['extra'][:3]})")
    if inv["duplicate_ids"] or inv["duplicate_urls"]:
        soft.append(f"duplicates: {inv['duplicate_ids']} repeated ids, urls "
                    f"{inv['duplicate_urls'][:3]}")
    if inv["documents"] != inv["expected"]:
        soft.append(f"{inv['documents']} documents indexed, expected exactly {inv['expected']}")
    stats = inv.get("collection_stats") or {}
    if isinstance(stats, dict) and stats.get("chunk_count") not in (None, inv[
            "chunks_by_documents"]):
        soft.append(f"collection reports {stats.get('chunk_count')} chunks but its documents "
                    f"hold {inv['chunks_by_documents']} (orphan or duplicate chunks)")
    return soft


# ── The built-in MongoDB MCP connector ──────────────────────────────────────


def tool_dsn(password: str | None = None, host: str = RS_HOST, extra: str = "") -> str:
    pw = password if password is not None else env_secret("RW_MONGO_TOOL_PASSWORD")
    return (f"mongodb://rwtool:{quote(pw, safe='')}@{host}/{TOOL_DB}?replicaSet=rs0"
            f"&authSource=admin{extra}")


def register_connector(api: LiveAPI, cleanup: Any, name: str, dsn: str,
                       expect: int = 201) -> dict[str, Any]:
    """POST /connectors exactly as the catalog -> MongoDB -> Configure form does."""
    body = api.json_ok("GET", "/connectors/catalog")
    items = body if isinstance(body, list) else body.get("connectors") or body.get("items") or []
    entry = next((c for c in items if c.get("name") == "mongodb"), None)
    assert entry, "no mongodb entry in GET /connectors/catalog"
    resp = api.post("/connectors", json={
        "name": name, "url": "builtin://", "auth_type": entry["auth_type"],
        "auth_config": {"url": dsn, "database": TOOL_DB}, "auto_approve": False,
        "type": entry.get("builtin_server_id") or "mongodb"})
    assert resp.status_code == expect, (
        f"POST /connectors -> {resp.status_code} (expected {expect}): {mask(resp.text)[:400]}")
    out = resp.json() if resp.content else {}
    if resp.status_code < 300:
        server_id = str(out.get("server_id") or out.get("id"))
        cleanup("DELETE", f"/connectors/{server_id}")
        out["server_id"] = server_id
    return dict(out)


def tool_result(step: dict[str, Any] | None) -> dict[str, Any]:
    """The MCP result inside a workflow tool step's output (``output.output``)."""
    from tests.real_world.workflows import step_output

    output = step_output(step) or {}
    inner = output.get("output", output) if isinstance(output, dict) else {}
    if isinstance(inner, str):
        with contextlib.suppress(ValueError):
            inner = json.loads(inner)
    return inner if isinstance(inner, dict) else {"raw": inner}


# ── A per-run MongoDB user (permission revoke / re-grant) ───────────────────


@contextlib.contextmanager
def temp_reader(client: Any, database: str = SOURCE_DB) -> Iterator[dict[str, str]]:
    """A fresh user with ``read`` on ``database``; dropped at the end."""
    user = f"rwsync_{tag()}"
    password = secrets.token_urlsafe(18)
    register_secret(password)
    client.admin.command("createUser", user, pwd=password,
                         roles=[{"role": "read", "db": database}])
    try:
        yield {"user": user, "password": password, "database": database}
    finally:
        with contextlib.suppress(Exception):
            client.admin.command("dropUser", user)


def revoke_read(client: Any, user: str, database: str = SOURCE_DB) -> None:
    client.admin.command("revokeRolesFromUser", user, roles=[{"role": "read", "db": database}])


def grant_read(client: Any, user: str, database: str = SOURCE_DB) -> None:
    client.admin.command("grantRolesToUser", user, roles=[{"role": "read", "db": database}])


# ── Waiting on syncs ────────────────────────────────────────────────────────


def wait_running_job(api: LiveAPI, sid: str, job_id: str, *, min_indexed: int = 1,
                     timeout: float = 600) -> dict[str, Any]:
    """Wait until THAT job is running and has indexed at least ``min_indexed`` docs."""
    def probe() -> dict[str, Any]:
        return sj.job(api, sid, job_id) or {}

    return dict(wait_until(probe, timeout=timeout, interval=1.5,
                           desc=f"job {job_id} running with >= {min_indexed} indexed",
                           done=lambda j: str(j.get("status", "")).lower() in sj.TERMINAL or (
                               int(j.get("docs_indexed") or 0) >= min_indexed)))


def jobs_since(api: LiveAPI, sid: str, started_iso: str) -> list[dict[str, Any]]:
    return [j for j in sj.jobs(api, sid) if str(j.get("created_at") or "") >= started_iso]


def utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
