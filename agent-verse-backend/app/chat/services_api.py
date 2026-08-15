"""Connected services panel — REST layer over MCPRegistry.

Provides endpoints to list, connect, and disconnect MCP tool connectors.
  GET    /chat/services         — list all connectors for tenant
  POST   /chat/services         — initiate connection (returns OAuth URL)
  DELETE /chat/services/{id}    — disconnect (revoke credentials)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _hex() -> str:
    return uuid.uuid4().hex


@dataclass
class ConnectedService:
    id: str
    tenant_id: str
    name: str
    url: str
    scopes: list[str] = field(default_factory=list)
    status: str = "connected"   # connected | disconnected | error
    connected_at: datetime = field(default_factory=_now)


class ServicesAPI:
    """In-memory store backing the connected services REST layer.

    Production delegates to MCPRegistry (per-tenant connector store).
    """

    def __init__(self) -> None:
        self._services: dict[str, ConnectedService] = {}

    def list_services(self, tenant_id: str) -> list[ConnectedService]:
        return [s for s in self._services.values() if s.tenant_id == tenant_id]

    def initiate_connection(
        self,
        tenant_id: str,
        name: str,
        url: str,
        scopes: list[str] | None = None,
    ) -> dict:
        """Register a service and return an OAuth setup URL."""
        svc = ConnectedService(
            id=_hex(),
            tenant_id=tenant_id,
            name=name,
            url=url,
            scopes=scopes or [],
            status="connected",
        )
        self._services[svc.id] = svc
        # In production: call MCPRegistry.register() then build PKCE OAuth URL
        oauth_url = f"https://agentverse.app/oauth/mcp?service_id={svc.id}"
        return {"service_id": svc.id, "oauth_url": oauth_url, "service": svc}

    def disconnect_service(self, service_id: str, tenant_id: str) -> bool:
        svc = self._services.get(service_id)
        if not svc or svc.tenant_id != tenant_id:
            return False
        del self._services[service_id]
        return True

    def get_service(self, service_id: str, tenant_id: str) -> ConnectedService | None:
        svc = self._services.get(service_id)
        if svc and svc.tenant_id == tenant_id:
            return svc
        return None
