"""NATIVE-01: the /tools/files workspace is a durable, shared store.

It used to be pod-local ``/tmp/agentverse-workspace/{tenant}`` (other replicas and
workers never saw a file; a restart lost it). The API now goes through a store on
``app.state.workspace_store`` (Postgres in the lifespan) and refuses the
per-process store outside development rather than silently using pod memory.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.tools.workspace_store import (
    InMemoryWorkspaceStore,
    WorkspaceConflictError,
    WorkspacePathError,
    split_path,
)

# ── path normalisation ────────────────────────────────────────────────────────


def test_split_path_normalises() -> None:
    assert split_path("a/b/c.txt") == ("a/b", "c.txt")
    assert split_path("./a//b.txt") == ("a", "b.txt")
    assert split_path("x.txt") == ("", "x.txt")
    assert split_path(".", allow_root=True) == ("", "")


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/../../b", "/etc/passwd"])
def test_split_path_refuses_escapes(bad: str) -> None:
    with pytest.raises(PermissionError):
        split_path(bad)


@pytest.mark.parametrize("bad", ["", ".", "a\\b", "a\x00b", "x" * 300, "/".join(["d"] * 40)])
def test_split_path_refuses_malformed(bad: str) -> None:
    with pytest.raises(WorkspacePathError):
        split_path(bad)


# ── store semantics (shared by the Postgres store; see the pg integration test) ──


@pytest.mark.asyncio
async def test_write_read_list_delete_roundtrip() -> None:
    store = InMemoryWorkspaceStore()
    assert await store.write("t1", "docs/a.txt", "héllo") == len("héllo".encode())
    await store.write("t1", "docs/sub/b.txt", "b")
    await store.write("t1", "top.txt", "t")
    assert await store.read("t1", "docs/a.txt") == "héllo"

    root = await store.list("t1", ".")
    assert [(e["name"], e["type"]) for e in root] == [("docs", "directory"), ("top.txt", "file")]
    docs = await store.list("t1", "docs")
    assert [e["path"] for e in docs] == ["docs/a.txt", "docs/sub"]
    assert docs[0]["size_bytes"] == 6 and docs[0]["is_dir"] is False
    assert await store.list("t1", "missing") == []

    assert await store.delete("t1", "docs") is True  # the whole subtree
    assert [e["name"] for e in await store.list("t1", ".")] == ["top.txt"]
    assert await store.delete("t1", "docs/a.txt") is False
    with pytest.raises(FileNotFoundError):
        await store.read("t1", "docs/sub/b.txt")


@pytest.mark.asyncio
async def test_tenants_are_isolated() -> None:
    store = InMemoryWorkspaceStore()
    await store.write("t1", "secret.txt", "s")
    with pytest.raises(FileNotFoundError):
        await store.read("t2", "secret.txt")
    assert await store.list("t2", ".") == []
    assert await store.delete("t2", "secret.txt") is False


@pytest.mark.asyncio
async def test_file_and_directory_conflicts() -> None:
    store = InMemoryWorkspaceStore()
    await store.write("t", "a", "file")
    with pytest.raises(WorkspaceConflictError):
        await store.write("t", "a/b.txt", "x")
    await store.write("t", "d/x.txt", "x")
    with pytest.raises(WorkspaceConflictError):
        await store.write("t", "d", "now a file?")
    with pytest.raises(WorkspaceConflictError):
        await store.read("t", "d")
    with pytest.raises(WorkspaceConflictError):
        await store.list("t", "a")


@pytest.mark.asyncio
async def test_list_is_bounded_and_keyset_paged() -> None:
    store = InMemoryWorkspaceStore()
    for i in range(5):
        await store.write("t", f"f{i}.txt", "x")
    first = await store.list("t", ".", limit=2)
    assert [e["name"] for e in first] == ["f0.txt", "f1.txt"]
    nxt = await store.list("t", ".", limit=2, after=first[-1]["name"])
    assert [e["name"] for e in nxt] == ["f2.txt", "f3.txt"]


@pytest.mark.asyncio
async def test_nul_content_is_refused() -> None:
    with pytest.raises(WorkspacePathError):
        await InMemoryWorkspaceStore().write("t", "a.txt", "a\x00b")


# ── API ───────────────────────────────────────────────────────────────────────


def _client(store: Any) -> TestClient:
    from app.api.tools import router

    ctx = TenantContext(tenant_id="t-ws", plan=PlanTier.FREE, api_key_id="k", roles=("admin",))
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    app.state.audit_log = AuditLog()
    if store is not None:
        app.state.workspace_store = store
    return TestClient(app)


def test_api_uses_the_shared_store_not_the_pod_filesystem() -> None:
    store = InMemoryWorkspaceStore()
    replica_a, replica_b = _client(store), _client(store)
    assert replica_a.post("/tools/files/r/x.txt", json={"content": "hi"}).status_code == 201
    # Another replica sharing the store sees it.
    assert replica_b.get("/tools/files/r/x.txt").json()["content"] == "hi"
    assert not pathlib.Path("/tmp/agentverse-workspace/t-ws/r/x.txt").exists()


def test_api_without_a_store_is_503_not_a_local_fallback() -> None:
    client = _client(None)
    assert client.get("/tools/files").status_code == 503
    assert client.post("/tools/files/a.txt", json={"content": "x"}).status_code == 503


def test_api_refuses_the_per_process_store_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.governance.audit._durable_audit_required", lambda: True)
    client = _client(InMemoryWorkspaceStore())
    assert client.get("/tools/files").status_code == 503
    assert client.post("/tools/files/a.txt", json={"content": "x"}).status_code == 503


def test_api_error_mapping() -> None:
    client = _client(InMemoryWorkspaceStore())
    assert client.get("/tools/files/nope.txt").status_code == 404
    assert client.get("/tools/files/..%2Fetc%2Fpasswd").status_code == 403
    assert client.post("/tools/files/a", json={"content": "x"}).status_code == 201
    assert client.post("/tools/files/a/b", json={"content": "x"}).status_code == 409
    assert client.post("/tools/files/n.txt", json={"content": "a\x00"}).status_code == 400
    assert client.get("/tools/files", params={"limit": 5000}).status_code == 422


def test_create_app_wires_a_workspace_store() -> None:
    from app.main import create_app

    assert isinstance(create_app().state.workspace_store, InMemoryWorkspaceStore)


def test_no_app_code_uses_the_pod_local_workspace() -> None:
    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "agentverse-workspace" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
