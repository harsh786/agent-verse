"""SQLAlchemy ORM models for knowledge base: collections, documents, and memory stores."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class KnowledgeCollection(Base):
    __tablename__ = "knowledge_collections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_knowledge_collections_tenant_id_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedder: Mapped[str] = mapped_column(Text, nullable=False, default="voyage-4-large")
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False, default=768)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    language: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness_ttl_hours: Mapped[int | None] = mapped_column(Integer)
    collection_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    documents: Mapped[list[Document]] = relationship(
        "Document", back_populates="collection", cascade="all, delete-orphan"
    )


class Document(Base):
    """Chunked document with pgvector embedding for hybrid RAG search."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    collection_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 768-dim embedding vector (Voyage / text-embedding-3-small compatible)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # Use "doc_metadata" as the Python attribute to avoid shadowing Base.metadata
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=True, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    collection: Mapped[KnowledgeCollection] = relationship(
        "KnowledgeCollection", back_populates="documents"
    )


class _KnowledgeChunkMixin:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    collection_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    domain_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(32))
    chunk_level: Mapped[str | None] = mapped_column(String(10), default="leaf")
    window_start: Mapped[int | None] = mapped_column(Integer)
    window_end: Mapped[int | None] = mapped_column(Integer)
    window_id: Mapped[str | None] = mapped_column(Text)
    hierarchy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_proposition: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    strategy_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk768(_KnowledgeChunkMixin, Base):
    __tablename__ = "knowledge_chunks_768"

    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)


class KnowledgeChunk1024(_KnowledgeChunkMixin, Base):
    __tablename__ = "knowledge_chunks_1024"

    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)


class KnowledgeChunk1536(_KnowledgeChunkMixin, Base):
    __tablename__ = "knowledge_chunks_1536"

    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)


class KnowledgeChunk3072(_KnowledgeChunkMixin, Base):
    __tablename__ = "knowledge_chunks_3072"

    embedding: Mapped[list[float]] = mapped_column(Vector(3072), nullable=False)


class ExecutionMemory(Base):
    """Short-lived per-goal execution plan and outcome memory."""

    __tablename__ = "execution_memory"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LongTermMemory(Base):
    """Durable agent knowledge — success patterns, failure lessons, domain facts."""

    __tablename__ = "long_term_memory"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_goal_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default="")
    memory_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default="success_pattern"
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=1.0)
    tags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MemoryConflict(Base):
    """Persistent storage for memory conflicts detected in memory_v2."""

    __tablename__ = "memory_conflicts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    memory_id_a: Mapped[str] = mapped_column(String(32), nullable=False)
    memory_id_b: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conflict_type: Mapped[str] = mapped_column(String(50), nullable=False, default="contradiction")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
