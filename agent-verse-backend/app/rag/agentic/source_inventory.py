"""SourceInventory — builds a snapshot of all retrieval sources available for a tenant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext


@dataclass
class SourceInventoryResult:
    kb_collections: int
    kb_total_chunks: int
    kg_nodes: int
    ltm_entries: int
    exec_memory_plans: int
    web_available: bool
    embedder_available: bool
    kb_state: str = "unknown"     # empty|sparse|healthy|stale
    graph_state: str = "unknown"  # empty|healthy|partial

    def to_dict(self) -> dict[str, Any]:
        return {
            "kb_collections": self.kb_collections,
            "kb_total_chunks": self.kb_total_chunks,
            "kg_nodes": self.kg_nodes,
            "ltm_entries": self.ltm_entries,
            "exec_memory_plans": self.exec_memory_plans,
            "web_available": self.web_available,
            "embedder_available": self.embedder_available,
            "kb_state": self.kb_state,
            "graph_state": self.graph_state,
        }


class SourceInventory:
    def __init__(
        self,
        *,
        knowledge_store: Any = None,
        kg_store: Any = None,
        ltm_store: Any = None,
        exec_memory: Any = None,
        web_search_available: bool = False,
        embedder_available: bool = False,
    ) -> None:
        self._kb = knowledge_store
        self._kg = kg_store
        self._ltm = ltm_store
        self._exec = exec_memory
        self._web = web_search_available
        self._embedder = embedder_available

    async def build(self, *, tenant_ctx: TenantContext) -> SourceInventoryResult:
        # KB state
        kb_collections = 0
        kb_chunks = 0
        if self._kb is not None:
            cols = await self._kb.list_collections_async(tenant_ctx=tenant_ctx)
            kb_collections = len(cols)
            kb_chunks = sum(getattr(c, "document_count", 0) for c in cols)

        # KG state
        kg_nodes = 0
        if self._kg is not None:
            try:
                nodes = self._kg.query_nodes(tenant_ctx.tenant_id, limit=1000)
                kg_nodes = len(nodes)
            except Exception:
                pass

        # LTM state
        ltm_entries = 0
        if self._ltm is not None:
            try:
                mems = self._ltm.list_all(tenant_ctx=tenant_ctx)
                ltm_entries = len(mems)
            except Exception:
                pass

        # Exec memory
        exec_plans = 0
        if self._exec is not None:
            try:
                plans = self._exec.recall(goal_hint="", tenant_ctx=tenant_ctx, top_k=100)
                exec_plans = len(plans)
            except Exception:
                pass

        # Determine KB state
        if kb_collections == 0:
            kb_state = "empty"
        elif kb_chunks < 10:
            kb_state = "sparse"
        else:
            kb_state = "healthy"

        graph_state = "empty" if kg_nodes == 0 else "healthy"

        return SourceInventoryResult(
            kb_collections=kb_collections,
            kb_total_chunks=kb_chunks,
            kg_nodes=kg_nodes,
            ltm_entries=ltm_entries,
            exec_memory_plans=exec_plans,
            web_available=self._web,
            embedder_available=self._embedder,
            kb_state=kb_state,
            graph_state=graph_state,
        )
