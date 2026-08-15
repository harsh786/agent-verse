"""Shared type definitions used by AgentGraph and its node mixins.

Extracted from graph.py to avoid circular imports when mixins need GraphState.
"""
from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    # Set at graph entry
    goal: str
    tenant_ctx: Any          # TenantContext (stored as Any for TypedDict compat)
    autonomy_mode: str       # supervised | bounded-autonomous | fully-autonomous
    # Populated by nodes
    agent_state: Any         # AgentState - the rich runtime state object
    rag_context: str         # retrieved context text
    plan: list[str]          # current step list
    iteration: int           # current iteration count
    terminal_reason: str     # why the graph terminated
    reasoning_evidence: dict[str, Any]  # aggregate-only, privacy-safe evidence


class RetrievalEntryPointError(RuntimeError):
    """A required, tenant-scoped retrieval leg failed."""
