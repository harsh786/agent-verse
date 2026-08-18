"""QA4 — Sub-Tenants (Enterprise Hierarchy).

Enterprise orgs need hierarchy:
  Acme Corp (parent tenant)
    └── EMEA Division (sub-tenant)
          ├── EMEA Marketing Org
          └── EMEA Sales Org
    └── APAC Division (sub-tenant)
          └── APAC Trading Org

Each sub-tenant:
  - Inherits parent's SSO + billing
  - Has independent orgs + knowledge + memory
  - Has carved-out budget from parent's quota
  - Can have different data residency region

Endpoints:
  POST /v1/tenants/{id}/sub-tenants
  GET  /v1/tenants/{id}/sub-tenants
  GET  /v1/tenants/{id}/hierarchy
  PATCH /v1/sub-tenants/{id}/budget
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.observability.logging import get_logger

_log = get_logger(__name__)


# ── SQLAlchemy model ───────────────────────────────────────────────────────────

class SubTenantModel(Base):
    """DB model for sub-tenants."""
    __tablename__ = "sub_tenants"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_tenant_id    = Column(String(100), nullable=False, index=True)
    name                = Column(String(200), nullable=False)
    slug                = Column(String(64), nullable=False)
    description         = Column(String(500), default="")
    budget_allocation_usd = Column(Float, default=0.0)
    max_agents          = Column(Integer, default=20)
    max_orgs            = Column(Integer, default=3)
    allowed_channels    = Column(JSONB, default=[])
    data_residency_region = Column(String(20), default="us")   # us | eu | in | ap
    status              = Column(String(20), default="active")
    settings            = Column(JSONB, default={})
    created_at          = Column(DateTime(timezone=True), default=datetime.now(UTC))
    updated_at          = Column(DateTime(timezone=True), default=datetime.now(UTC), onupdate=datetime.now(UTC))


# ── Dataclass for service layer ────────────────────────────────────────────────

@dataclass
class SubTenant:
    """Sub-tenant entity per spec QA4."""
    sub_tenant_id: str
    parent_tenant_id: str
    name: str
    slug: str
    description: str = ""
    budget_allocation_usd: float = 0.0    # carved from parent's budget
    max_agents: int = 20                   # quota from parent
    max_orgs: int = 3
    allowed_channels: list[str] = field(default_factory=list)
    data_residency_region: str = "us"     # us | eu | in | ap
    status: str = "active"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TenantHierarchy:
    """Full tenant hierarchy tree."""
    tenant_id: str
    tenant_name: str
    sub_tenants: list["TenantHierarchyNode"] = field(default_factory=list)


@dataclass
class TenantHierarchyNode:
    sub_tenant: SubTenant
    org_count: int = 0
    agent_count: int = 0


class SubTenantService:
    """
    QA4 — Sub-tenant management service.
    Production: backed by `sub_tenants` DB table.
    Development: in-memory.
    """

    def __init__(self, session: Any | None = None) -> None:
        self._session = session
        self._store: dict[str, SubTenant] = {}   # sub_tenant_id → SubTenant
        self._by_parent: dict[str, list[str]] = {}  # parent_id → [sub_tenant_ids]

    async def create(
        self,
        parent_tenant_id: str,
        name: str,
        budget_allocation_usd: float = 0.0,
        max_agents: int = 20,
        max_orgs: int = 3,
        allowed_channels: list[str] | None = None,
        data_residency_region: str = "us",
    ) -> SubTenant:
        """POST /v1/tenants/{id}/sub-tenants"""
        slug = name.lower().replace(" ", "-").replace("_", "-")[:64]
        sub = SubTenant(
            sub_tenant_id=str(uuid.uuid4()),
            parent_tenant_id=parent_tenant_id,
            name=name,
            slug=slug,
            budget_allocation_usd=budget_allocation_usd,
            max_agents=max_agents,
            max_orgs=max_orgs,
            allowed_channels=allowed_channels or [],
            data_residency_region=data_residency_region,
        )
        self._store[sub.sub_tenant_id] = sub
        self._by_parent.setdefault(parent_tenant_id, []).append(sub.sub_tenant_id)
        _log.info(
            "sub_tenant.created",
            sub_tenant_id=sub.sub_tenant_id,
            parent=parent_tenant_id,
            name=name,
        )
        return sub

    async def list_by_parent(self, parent_tenant_id: str) -> list[SubTenant]:
        """GET /v1/tenants/{id}/sub-tenants"""
        ids = self._by_parent.get(parent_tenant_id, [])
        return [self._store[i] for i in ids if i in self._store]

    async def get(self, sub_tenant_id: str) -> SubTenant | None:
        return self._store.get(sub_tenant_id)

    async def update_budget(self, sub_tenant_id: str, new_budget_usd: float) -> bool:
        """PATCH /v1/sub-tenants/{id}/budget"""
        sub = self._store.get(sub_tenant_id)
        if not sub:
            return False
        sub.budget_allocation_usd = new_budget_usd
        sub.updated_at = datetime.now(UTC)
        _log.info("sub_tenant.budget_updated", sub_tenant_id=sub_tenant_id, budget=new_budget_usd)
        return True

    async def get_hierarchy(self, parent_tenant_id: str) -> TenantHierarchy:
        """GET /v1/tenants/{id}/hierarchy — full tree view"""
        sub_tenants = await self.list_by_parent(parent_tenant_id)
        nodes = [
            TenantHierarchyNode(sub_tenant=s)
            for s in sub_tenants
        ]
        return TenantHierarchy(
            tenant_id=parent_tenant_id,
            tenant_name=parent_tenant_id,   # TODO: join with tenant record
            sub_tenants=nodes,
        )

    async def deactivate(self, sub_tenant_id: str) -> bool:
        sub = self._store.get(sub_tenant_id)
        if not sub:
            return False
        sub.status = "inactive"
        return True


# Global instance
sub_tenant_service = SubTenantService()
