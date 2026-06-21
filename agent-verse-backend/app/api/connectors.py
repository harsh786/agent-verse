"""Connectors API — register, list, and manage MCP server connections."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.mcp.catalog import CONNECTOR_CATALOG
from app.mcp.registry import MCPServerConfig

router = APIRouter(prefix="/connectors", tags=["connectors"])


class RegisterConnectorRequest(BaseModel):
    name: str
    url: str
    auth_type: str
    auth_config: dict[str, Any] = {}
    description: str = ""
    priority: int = 0


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _registry(request: Request) -> Any:
    return request.app.state.mcp_registry


@router.get("/catalog")
async def get_catalog(request: Request) -> list[dict[str, Any]]:
    _require_tenant(request)
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "auth_type": spec.auth_type,
            "default_url": spec.default_url,
        }
        for spec in CONNECTOR_CATALOG
    ]


@router.get("")
async def list_connectors(request: Request) -> list[dict[str, Any]]:
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    servers = await reg.list_servers(tenant_ctx=tenant_ctx)
    return [s.model_dump() for s in servers]


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_connector(
    request: Request, body: RegisterConnectorRequest
) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    cfg = MCPServerConfig(
        name=body.name,
        url=body.url,
        auth_type=body.auth_type,
        auth_config=body.auth_config,
        description=body.description,
        priority=body.priority,
    )
    server_id = await reg.register(cfg, tenant_ctx=tenant_ctx)
    return {"server_id": server_id, "name": body.name, "url": body.url}


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_connector(request: Request, server_id: str) -> None:
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    removed = await reg.unregister(server_id, tenant_ctx=tenant_ctx)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector {server_id} not found",
        )
