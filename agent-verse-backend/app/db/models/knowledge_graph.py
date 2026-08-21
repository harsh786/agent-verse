"""Knowledge Graph DB models."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, Float, Index, String, Text

from app.db.models import Base


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id = Column(String(255), primary_key=True)
    tenant_id = Column(String(255), nullable=False, index=True)
    node_type = Column(String(50), nullable=False, index=True)
    label = Column(String(500), nullable=False)
    content = Column(Text, nullable=True)
    source_id = Column(String(255), nullable=True)
    confidence = Column(Float, default=1.0, nullable=False)
    extra_metadata = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_kn_tenant_type", "tenant_id", "node_type"),
        Index("ix_kn_tenant_label", "tenant_id", "label"),
    )


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"

    id = Column(String(255), primary_key=True)
    tenant_id = Column(String(255), nullable=False, index=True)
    source_node_id = Column(String(255), nullable=False)
    target_node_id = Column(String(255), nullable=False)
    edge_type = Column(String(50), nullable=False)
    label = Column(String(255), nullable=True)
    confidence = Column(Float, default=1.0, nullable=False)
    evidence = Column(Text, nullable=True)
    provenance = Column(String(255), nullable=True)
    extra_metadata = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_ke_tenant", "tenant_id"),
        Index("ix_ke_source", "source_node_id"),
        Index("ix_ke_target", "target_node_id"),
    )
