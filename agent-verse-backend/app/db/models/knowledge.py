"""SQLAlchemy ORM models for knowledge base: collections, documents, and memory stores."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class KnowledgeCollection(Base):
    __tablename__ = "knowledge_collections"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    embedder: Mapped[str | None] = mapped_column(String(100), nullable=True, default="voyage")
    document_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    documents: Mapped[list[Document]] = relationship(
        "Document", back_populates="collection", cascade="all, delete-orphan"
    )


class Document(Base):
    """Chunked document with pgvector embedding for hybrid RAG search."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
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
    doc_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=True, server_default=func.cast("{}", JSONB)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    collection: Mapped[KnowledgeCollection] = relationship(
        "KnowledgeCollection", back_populates="documents"
    )


class ExecutionMemory(Base):
    """Short-lived per-goal execution plan and outcome memory."""

    __tablename__ = "execution_memory"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LongTermMemory(Base):
    """Durable agent knowledge — success patterns, failure lessons, domain facts."""

    __tablename__ = "long_term_memory"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_goal_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=""
    )
    memory_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default="success_pattern"
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=1.0)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


