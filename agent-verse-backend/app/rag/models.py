"""RAG data models — Knowledge collections, documents, and chunks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class KnowledgeCollection:
    """A named container for a set of related documents."""

    name: str
    description: str = ""
    collection_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    document_count: int = 0
    embedder: str = "voyage"


@dataclass
class Document:
    """A single ingested source document (before chunking)."""

    collection_id: str
    source: str
    content: str
    content_hash: str
    document_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Chunk:
    """A sub-document fragment with its embedding vector."""

    document_id: str
    content: str
    embedding: list[float]
    chunk_index: int
    chunk_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    metadata: dict[str, str] = field(default_factory=dict)
