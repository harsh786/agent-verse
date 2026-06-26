"""Artifact REST API — list, get, download, delete agent-produced files."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx


@router.get("")
async def list_artifacts(
    request: Request,
    goal_id: str | None = None,
    artifact_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List artifacts for this tenant."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import select

        from app.db.models.artifacts import Artifact
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, sqlalchemy_rls_context(
            session, tenant.tenant_id
        ):
            q = select(Artifact).where(Artifact.tenant_id == tenant.tenant_id)
            if goal_id:
                q = q.where(Artifact.goal_id == goal_id)
            if artifact_type:
                q = q.where(Artifact.artifact_type == artifact_type)
            q = q.order_by(Artifact.created_at.desc()).limit(limit)
            result = await session.execute(q)
            rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "artifact_type": r.artifact_type,
                "storage_uri": r.storage_uri,
                "content_type": r.content_type,
                "size_bytes": r.size_bytes,
                "goal_id": r.goal_id,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    except Exception:
        return []


@router.get("/{artifact_id}")
async def get_artifact(request: Request, artifact_id: str) -> dict[str, Any]:
    """Get artifact metadata."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(404, "Artifact not found")
    try:
        from sqlalchemy import select

        from app.db.models.artifacts import Artifact
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, sqlalchemy_rls_context(
            session, tenant.tenant_id
        ):
            result = await session.execute(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.tenant_id == tenant.tenant_id,
                )
            )
            row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Artifact not found")
        return {
            "id": row.id,
            "name": row.name,
            "artifact_type": row.artifact_type,
            "storage_uri": row.storage_uri,
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "goal_id": row.goal_id,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "Failed to fetch artifact")


@router.delete("/{artifact_id}", status_code=204)
async def delete_artifact(request: Request, artifact_id: str) -> None:
    """Delete an artifact."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(404, "Artifact not found")
    try:
        from sqlalchemy import delete

        from app.db.models.artifacts import Artifact
        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant.tenant_id
        ):
            result = await session.execute(
                delete(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.tenant_id == tenant.tenant_id,
                )
            )
            if result.rowcount == 0:
                raise HTTPException(404, "Artifact not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "Failed to delete artifact")
