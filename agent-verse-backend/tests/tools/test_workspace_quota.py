"""NATIVE-04: per-file size limit and per-tenant workspace quota.

Nothing bounded a workspace write, so one tenant could fill the shared storage
and break every tenant's workspace. A file larger than
``workspace_max_file_bytes`` is 413; a write that would take the tenant past
``workspace_max_tenant_bytes`` or ``workspace_max_entries`` is 507 and changes
nothing. Deletes give the space back.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.tools.workspace_store import (
    InMemoryWorkspaceStore,
    WorkspaceFileTooLargeError,
    WorkspaceLimits,
    WorkspaceQuotaExceededError,
)

_LIMITS = WorkspaceLimits(max_file_bytes=10, max_tenant_bytes=25, max_entries=5)


@pytest.mark.asyncio
async def test_file_larger_than_the_cap_is_refused() -> None:
    store = InMemoryWorkspaceStore(limits=_LIMITS)
    with pytest.raises(WorkspaceFileTooLargeError):
        await store.write("t", "big.txt", "x" * 11)
    # Multi-byte characters count as bytes, not characters.
    with pytest.raises(WorkspaceFileTooLargeError):
        await store.write("t", "u.txt", "é" * 6)
    assert await store.usage("t") == {"bytes_used": 0, "entries": 0, **_LIMITS.as_dict()}


@pytest.mark.asyncio
async def test_tenant_byte_quota_is_enforced_and_overwrites_count_the_delta() -> None:
    store = InMemoryWorkspaceStore(limits=_LIMITS)
    await store.write("t", "a.txt", "x" * 10)
    await store.write("t", "b.txt", "x" * 10)
    with pytest.raises(WorkspaceQuotaExceededError):
        await store.write("t", "c.txt", "x" * 6)
    # Overwriting with a smaller body frees space; the failed write left nothing.
    await store.write("t", "a.txt", "x")
    await store.write("t", "c.txt", "x" * 6)
    usage = await store.usage("t")
    assert usage["bytes_used"] == 17 and usage["entries"] == 3
    with pytest.raises(FileNotFoundError):
        await store.read("t", "missing.txt")
    # Another tenant has its own quota.
    await store.write("other", "a.txt", "x" * 10)


@pytest.mark.asyncio
async def test_entry_quota_counts_directories_and_failed_writes_change_nothing() -> None:
    store = InMemoryWorkspaceStore(limits=_LIMITS)
    await store.write("t", "d1/d2/f.txt", "x")  # 3 entries
    await store.write("t", "g.txt", "x")  # 4
    with pytest.raises(WorkspaceQuotaExceededError):
        await store.write("t", "n1/n2.txt", "x")  # would be 6
    assert [e["name"] for e in await store.list("t", ".")] == ["d1", "g.txt"]
    assert (await store.usage("t"))["entries"] == 4


@pytest.mark.asyncio
async def test_delete_returns_the_space() -> None:
    store = InMemoryWorkspaceStore(limits=_LIMITS)
    await store.write("t", "d/a.txt", "x" * 10)
    await store.write("t", "d/b.txt", "x" * 10)
    assert await store.delete("t", "d") is True
    assert await store.usage("t") == {"bytes_used": 0, "entries": 0, **_LIMITS.as_dict()}
    await store.write("t", "e.txt", "x" * 10)


def test_default_limits_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "workspace_max_file_bytes", 123)
    monkeypatch.setattr(s, "workspace_max_tenant_bytes", 456)
    monkeypatch.setattr(s, "workspace_max_entries", 7)
    assert WorkspaceLimits.from_settings().as_dict() == {
        "max_file_bytes": 123,
        "max_tenant_bytes": 456,
        "max_entries": 7,
    }


def _client(store: Any, audit: AuditLog) -> TestClient:
    from app.api.tools import router

    ctx = TenantContext(tenant_id="t-q", plan=PlanTier.FREE, api_key_id="k", roles=("admin",))
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    app.state.audit_log = audit
    app.state.workspace_store = store
    return TestClient(app)


def test_api_maps_size_and_quota_and_reports_usage() -> None:
    audit = AuditLog()
    client = _client(InMemoryWorkspaceStore(limits=_LIMITS), audit)
    big = client.post("/tools/files/big.txt", json={"content": "x" * 11})
    assert big.status_code == 413
    # Refused before the audit row: nothing was attempted.
    assert (
        audit.query(tenant_ctx=TenantContext(tenant_id="t-q", plan=PlanTier.FREE, api_key_id="k"))
        == []
    )
    assert client.post("/tools/files/a.txt", json={"content": "x" * 10}).status_code == 201
    assert client.post("/tools/files/b.txt", json={"content": "x" * 10}).status_code == 201
    full = client.post("/tools/files/c.txt", json={"content": "x" * 10})
    assert full.status_code == 507
    assert "quota" in full.json()["detail"].lower()
    usage = client.get("/tools/workspace/usage")
    assert usage.status_code == 200
    assert usage.json() == {"bytes_used": 20, "entries": 2, **_LIMITS.as_dict()}
