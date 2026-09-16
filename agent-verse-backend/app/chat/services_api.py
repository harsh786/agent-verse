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
    # pending  → connection initiated, OAuth token exchange NOT yet completed
    # connected → OAuth completed (complete_connection called by the callback)
    # error / disconnected
    status: str = "pending"  # pending | connected | disconnected | error
    created_at: datetime = field(default_factory=_now)
    # Only set once the OAuth flow actually completes — None while pending, so the
    # UI never shows a "connected since" time for a connection that never finished.
    connected_at: datetime | None = None


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
        """Register a service in the *pending* state and return an OAuth setup URL.

        The connector is NOT usable until the OAuth flow completes and
        ``complete_connection`` is called by the callback — status stays
        ``pending`` until then rather than falsely reporting ``connected``.
        """
        svc = ConnectedService(
            id=_hex(),
            tenant_id=tenant_id,
            name=name,
            url=url,
            scopes=scopes or [],
            status="pending",
        )
        self._services[svc.id] = svc
        oauth_url = f"https://agentverse.app/oauth/mcp?service_id={svc.id}"
        return {"service_id": svc.id, "oauth_url": oauth_url, "service": svc}

    def complete_connection(self, service_id: str, tenant_id: str) -> ConnectedService | None:
        """Mark a pending connection connected — called after OAuth token exchange."""
        svc = self._services.get(service_id)
        if not svc or svc.tenant_id != tenant_id:
            return None
        svc.status = "connected"
        svc.connected_at = _now()
        return svc

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
