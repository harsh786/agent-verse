"""Knowledge Graph data models."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    DOCUMENT = "document"
    CHUNK = "chunk"
    ENTITY = "entity"
    CONCEPT = "concept"
    GOAL = "goal"
    TOOL = "tool"
    MEMORY = "memory"
    ARTIFACT = "artifact"
    AGENT = "agent"
    WORKFLOW = "workflow"


class EdgeType(str, Enum):
    MENTIONS = "mentions"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CAUSED_BY = "caused_by"
    DEPENDS_ON = "depends_on"
    USED_TOOL = "used_tool"
    PRODUCED_ARTIFACT = "produced_artifact"
    SIMILAR_TO = "similar_to"
    PARENT_OF = "parent_of"
    REFERENCES = "references"


@dataclass
class GraphNode:
    """A node in the knowledge graph."""
    node_id: str
    tenant_id: str
    node_type: NodeType
    label: str
    content: str = ""
    source_id: str | None = None  # ID of the originating document/goal/etc
    confidence: float = 1.0
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class GraphEdge:
    """A directed edge between two graph nodes."""
    edge_id: str
    tenant_id: str
    source_node_id: str
    target_node_id: str
    edge_type: EdgeType
    label: str = ""
    confidence: float = 1.0
    evidence: str = ""  # Text evidence for this relationship
    provenance: str = ""  # Where this edge was extracted from
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass
class GraphCommunity:
    """A cluster of closely related nodes."""
    community_id: str
    tenant_id: str
    name: str
    node_ids: list[str] = field(default_factory=list)
    summary: str = ""
    size: int = 0
