"""E2E: Tenancy isolation — audit, MCP registry, RAG store, permission matrix."""

from __future__ import annotations

import math

from app.governance.audit import AuditEvent, AuditLog
from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

TENANT_A = TenantContext(
    tenant_id="iso-a", plan=PlanTier.PROFESSIONAL, api_key_id="key-iso-a"
)
TENANT_B = TenantContext(
    tenant_id="iso-b", plan=PlanTier.PROFESSIONAL, api_key_id="key-iso-b"
)

_DIM = 16


def _embed(seed: int) -> list[float]:
    raw = [math.sin(seed + i) for i in range(_DIM)]
    mag = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / mag for x in raw]


# ── Audit log tenant isolation ────────────────────────────────────────────────

async def test_audit_log_tenant_isolation() -> None:
    """Audit log entries are strictly tenant-scoped; no cross-tenant leakage."""
    audit = AuditLog()
    audit.record(
        AuditEvent(
            goal_id="g-a",
            tool_name="github",
            action_level=ActionLevel.ALLOW_LOG,
            outcome="list",
        ),
        tenant_ctx=TENANT_A,
    )
    audit.record(
        AuditEvent(
            goal_id="g-b",
            tool_name="jira",
            action_level=ActionLevel.ALLOW_LOG,
            outcome="create",
        ),
        tenant_ctx=TENANT_B,
    )

    entries_a = audit.query(tenant_ctx=TENANT_A)
    entries_b = audit.query(tenant_ctx=TENANT_B)

    assert len(entries_a) == 1
    assert len(entries_b) == 1
    assert entries_a[0].goal_id == "g-a"
    assert entries_b[0].goal_id == "g-b"
    # Verify no cross-tenant contamination
    assert entries_a[0].tool_name == "github"
    assert entries_b[0].tool_name == "jira"


# ── MCP registry tenant isolation ────────────────────────────────────────────

async def test_mcp_registry_tenant_isolation() -> None:
    """MCP registry entries from tenant A cannot be fetched by tenant B."""

    class _FakeRedis:
        def __init__(self) -> None:
            self._d: dict[str, str] = {}
            self._s: dict[str, set[str]] = {}

        async def get(self, k: str) -> str | None:
            return self._d.get(k)

        async def set(self, k: str, v: str, ex: object = None) -> None:
            self._d[k] = v

        async def delete(self, k: str) -> int:
            existed = k in self._d
            self._d.pop(k, None)
            return int(existed)

        async def sadd(self, k: str, v: str) -> None:
            self._s.setdefault(k, set()).add(v)

        async def srem(self, k: str, v: str) -> None:
            self._s.get(k, set()).discard(v)

        async def smembers(self, k: str) -> set[str]:
            return self._s.get(k, set())

    registry = MCPRegistry(redis=_FakeRedis())

    cfg = MCPServerConfig(
        name="github", url="http://github-mcp", auth_type="bearer"
    )
    server_id = await registry.register(cfg, tenant_ctx=TENANT_A)

    # Tenant A can retrieve the server
    server_a = await registry.get(server_id, tenant_ctx=TENANT_A)
    assert server_a is not None
    assert server_a.name == "github"

    # Tenant B cannot access tenant A's server (key namespaced by tenant_id)
    server_b = await registry.get(server_id, tenant_ctx=TENANT_B)
    assert server_b is None


# ── RAG store tenant isolation ────────────────────────────────────────────────

async def test_rag_store_tenant_isolation() -> None:
    """Documents stored for tenant A are not accessible via tenant B's context."""
    store = KnowledgeStore()
    col_a = KnowledgeCollection(
        collection_id="shared-col", name="A Docs", description="tenant a"
    )
    store.create_collection(col_a, tenant_ctx=TENANT_A)
    store.ingest_chunk(
        Chunk(
            document_id="da1",
            content="secret a docs",
            embedding=_embed(0),
            chunk_index=0,
        ),
        collection_id="shared-col",
        tenant_ctx=TENANT_A,
    )

    # Tenant B queries the same logical collection_id → no results
    results_b = store.hybrid_search(
        query="secret a docs",
        query_embedding=_embed(0),
        collection_id="shared-col",
        tenant_ctx=TENANT_B,
        top_k=5,
    )
    assert results_b == []


# ── Permission matrix per-tenant ──────────────────────────────────────────────

async def test_permission_matrix_per_tenant() -> None:
    """Permission matrix settings are independently scoped per tenant."""
    matrix = PermissionMatrix()

    matrix.set_rule(
        PermissionRule(tool_name="github.push", level=ActionLevel.ALLOW),
        tenant_ctx=TENANT_A,
    )
    matrix.set_rule(
        PermissionRule(tool_name="github.push", level=ActionLevel.DENY),
        tenant_ctx=TENANT_B,
    )

    level_a = matrix.check(tool_name="github.push", tenant_ctx=TENANT_A)
    level_b = matrix.check(tool_name="github.push", tenant_ctx=TENANT_B)

    assert level_a == ActionLevel.ALLOW
    assert level_b == ActionLevel.DENY


# ── Default permission level for unconfigured tools ──────────────────────────

async def test_permission_matrix_default_is_allow_log() -> None:
    """Unconfigured tools default to ALLOW_LOG (execute + audit)."""
    matrix = PermissionMatrix()
    level = matrix.check(tool_name="unknown_tool", tenant_ctx=TENANT_A)
    assert level == ActionLevel.ALLOW_LOG
