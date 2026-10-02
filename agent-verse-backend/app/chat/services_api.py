"""Connected services panel — REST layer over MCPRegistry.

Provides endpoints to list, connect, and disconnect MCP tool connectors.
  GET    /chat/services         — list all connectors for tenant
  POST   /chat/services         — register a pending service (no OAuth URL here)
  DELETE /chat/services/{id}    — disconnect (revoke credentials)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Where a service is actually authorized: the Connectors PKCE OAuth flow, which
# exchanges the code and stores the token in the vault.
AUTHORIZE_VIA = "POST /connectors/{server_id}/oauth/start"


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
    # pending  → registered; no OAuth token exchange has happened (ORG-35: nothing
    #            in this panel marks a service connected without one)
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
        # Wired by the app lifespan; when set, connected-service records persist to
        # Postgres (durable + cross-pod) instead of only this process's dict.
        self._db_factory: Any = None

    def set_db(self, db_factory: Any) -> None:
        self._db_factory = db_factory

    @staticmethod
    def _row_to_service(row: Any) -> ConnectedService:
        return ConnectedService(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            url=row.url or "",
            scopes=list(row.scopes or []),
            status=row.status,
            created_at=row.created_at,
            connected_at=row.connected_at,
        )

    def list_services(self, tenant_id: str) -> list[ConnectedService]:
        return [s for s in self._services.values() if s.tenant_id == tenant_id]

    async def list_services_async(self, tenant_id: str) -> list[ConnectedService]:
        if self._db_factory is None:
            return self.list_services(tenant_id)
        from sqlalchemy import text as _t

        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            rows = (
                await s.execute(
                    _t(
                        "SELECT id, tenant_id, name, url, scopes, status, created_at, "
                        "connected_at FROM chat_connected_services WHERE tenant_id = :tid "
                        "ORDER BY created_at DESC LIMIT 500"
                    ),
                    {"tid": tenant_id},
                )
            ).fetchall()
        return [self._row_to_service(r) for r in rows]

    async def initiate_connection_async(
        self, tenant_id: str, name: str, url: str, scopes: list[str] | None = None
    ) -> dict:
        if self._db_factory is None:
            return self.initiate_connection(tenant_id, name, url, scopes)
        import json as _json

        from sqlalchemy import text as _t

        svc = ConnectedService(
            id=_hex(), tenant_id=tenant_id, name=name, url=url, scopes=scopes or [],
            status="pending",
        )
        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            await s.execute(
                _t(
                    "INSERT INTO chat_connected_services "
                    "(id, tenant_id, name, url, scopes, status) "
                    "VALUES (:id, :tid, :name, :url, CAST(:scopes AS jsonb), 'pending')"
                ),
                {
                    "id": svc.id, "tid": tenant_id, "name": name, "url": url,
                    "scopes": _json.dumps(svc.scopes),
                },
            )
        return {
            "service_id": svc.id,
            "oauth_url": None,
            "authorize_via": AUTHORIZE_VIA,
            "service": svc,
        }

    async def disconnect_service_async(self, service_id: str, tenant_id: str) -> bool:
        if self._db_factory is None:
            return self.disconnect_service(service_id, tenant_id)
        from sqlalchemy import text as _t

        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            res = await s.execute(
                _t(
                    "DELETE FROM chat_connected_services WHERE id = :id AND tenant_id = :tid"
                ),
                {"id": service_id, "tid": tenant_id},
            )
        return bool(res.rowcount)

    def initiate_connection(
        self,
        tenant_id: str,
        name: str,
        url: str,
        scopes: list[str] | None = None,
    ) -> dict:
        """Register a service in the *pending* state.

        This panel has no OAuth client (authorize/token URL, client id), so it
        returns no authorization URL and nothing here ever marks a service
        connected: authorization happens through the Connectors OAuth flow
        (``AUTHORIZE_VIA``), which exchanges the code and vaults the token.
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
        return {
            "service_id": svc.id,
            "oauth_url": None,
            "authorize_via": AUTHORIZE_VIA,
            "service": svc,
        }

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
