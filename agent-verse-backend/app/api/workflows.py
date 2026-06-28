"""Visual workflow builder REST API.

Endpoints
---------
GET    /workflows              list all workflows for the authenticated tenant
POST   /workflows              create a new workflow definition
GET    /workflows/{id}         retrieve a single workflow
PUT    /workflows/{id}         update name / description / definition (version bumped)
DELETE /workflows/{id}         delete a workflow
POST   /workflows/{id}/run     execute a workflow by submitting it as an AgentVerse goal

Design notes
------------
- ``_WorkflowStore`` provides an in-memory implementation that works in tests and
  zero-infra dev mode, and a DB-backed path that is activated by calling
  ``store.set_db(db_session_factory)`` during the FastAPI lifespan startup.
- All DB queries set the ``app.tenant_id`` Postgres GUC so Row-Level Security
  policies on the ``workflows`` table enforce tenant isolation at the DB layer.
- The ``run`` endpoint converts the saved workflow graph into a natural-language
  goal and submits it through ``GoalService``.  When ``dry_run=true`` is passed
  (or no GoalService is wired) the endpoint returns immediately with
  ``status="dry_run"`` — safe for integration tests and canvas previews.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.tenancy.context import TenantContext

router = APIRouter(prefix="/workflows", tags=["workflows"])


# ─── Pydantic schemas ────────────────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    definition: dict[str, Any] = Field(default_factory=dict)


class WorkflowUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    definition: dict[str, Any] = Field(default_factory=dict)


class WorkflowOut(BaseModel):
    id: str
    name: str
    description: str
    definition: dict[str, Any]
    status: str
    version: int
    created_at: str
    updated_at: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    return ctx


def _get_store(request: Request) -> _WorkflowStore:
    store: _WorkflowStore | None = getattr(request.app.state, "workflow_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow store not initialised",
        )
    return store


def _workflow_to_out(w: dict[str, Any]) -> WorkflowOut:
    def _iso(v: Any) -> str:
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v) if v is not None else datetime.now(UTC).isoformat()

    return WorkflowOut(
        id=w["id"],
        name=w["name"],
        description=w.get("description", ""),
        definition=w.get("definition") or {},
        status=w.get("status", "draft"),
        version=w.get("version", 1),
        created_at=_iso(w.get("created_at")),
        updated_at=_iso(w.get("updated_at")),
    )


def _orm_to_dict(wf: Any) -> dict[str, Any]:
    return {
        "id": wf.id,
        "tenant_id": wf.tenant_id,
        "name": wf.name,
        "description": wf.description or "",
        "definition": wf.definition or {},
        "status": wf.status,
        "version": wf.version,
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
    }


# ─── In-memory + optional DB workflow store ──────────────────────────────────

class _WorkflowStore:
    """Workflow persistence store.

    Uses an in-memory dict when no DB session factory is provided (tests, dev
    without running Postgres).  Call ``set_db(factory)`` in the FastAPI lifespan
    to switch to full Postgres-backed persistence with RLS enforcement.
    """

    def __init__(self) -> None:
        self._mem: dict[str, dict[str, Any]] = {}
        self._db: Any = None  # SQLAlchemy async session factory

    def set_db(self, db_factory: Any) -> None:
        """Wire in the async SQLAlchemy session factory (called during lifespan)."""
        self._db = db_factory

    # ── Public CRUD API ───────────────────────────────────────────────────────

    async def list(self, tenant_id: str) -> list[dict[str, Any]]:
        if self._db is not None:
            return await self._list_db(tenant_id)
        rows = [w for w in self._mem.values() if w["tenant_id"] == tenant_id]
        return sorted(rows, key=lambda w: w["created_at"], reverse=True)

    async def get(self, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        if self._db is not None:
            return await self._get_db(tenant_id, workflow_id)
        w = self._mem.get(workflow_id)
        return w if (w and w["tenant_id"] == tenant_id) else None

    async def create(
        self,
        tenant_id: str,
        name: str,
        description: str,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        if self._db is not None:
            return await self._create_db(tenant_id, name, description, definition)
        now = datetime.now(UTC)
        wf: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "name": name,
            "description": description,
            "definition": definition,
            "status": "draft",
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        self._mem[wf["id"]] = wf
        return wf

    async def update(
        self,
        tenant_id: str,
        workflow_id: str,
        name: str,
        description: str,
        definition: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._db is not None:
            return await self._update_db(
                tenant_id, workflow_id, name, description, definition
            )
        w = self._mem.get(workflow_id)
        if not w or w["tenant_id"] != tenant_id:
            return None
        w.update(
            name=name,
            description=description,
            definition=definition,
            version=w["version"] + 1,
            updated_at=datetime.now(UTC),
        )
        return w

    async def delete(self, tenant_id: str, workflow_id: str) -> bool:
        if self._db is not None:
            return await self._delete_db(tenant_id, workflow_id)
        w = self._mem.get(workflow_id)
        if w is None or w["tenant_id"] != tenant_id:
            return False
        del self._mem[workflow_id]
        return True

    # ── DB-backed implementations ─────────────────────────────────────────────

    async def _list_db(self, tenant_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import select
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        async with self._db() as session:
            await session.execute(
                sa_text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            result = await session.execute(
                select(Workflow)
                .where(Workflow.tenant_id == tenant_id)
                .order_by(Workflow.created_at.desc())
            )
            return [_orm_to_dict(r) for r in result.scalars().all()]

    async def _get_db(
        self, tenant_id: str, workflow_id: str
    ) -> dict[str, Any] | None:
        from sqlalchemy import select
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        async with self._db() as session:
            await session.execute(
                sa_text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            result = await session.execute(
                select(Workflow).where(
                    Workflow.id == workflow_id,
                    Workflow.tenant_id == tenant_id,
                )
            )
            row = result.scalar_one_or_none()
            return _orm_to_dict(row) if row else None

    async def _create_db(
        self,
        tenant_id: str,
        name: str,
        description: str,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        now = datetime.now(UTC)
        async with self._db() as session:
            await session.execute(
                sa_text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            wf = Workflow(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                name=name,
                description=description,
                definition=definition,
                status="draft",
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(wf)
            await session.commit()
            await session.refresh(wf)
            return _orm_to_dict(wf)

    async def _update_db(
        self,
        tenant_id: str,
        workflow_id: str,
        name: str,
        description: str,
        definition: dict[str, Any],
    ) -> dict[str, Any] | None:
        from sqlalchemy import select
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        async with self._db() as session:
            await session.execute(
                sa_text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            result = await session.execute(
                select(Workflow).where(
                    Workflow.id == workflow_id,
                    Workflow.tenant_id == tenant_id,
                )
            )
            wf = result.scalar_one_or_none()
            if wf is None:
                return None
            wf.name = name
            wf.description = description
            wf.definition = definition
            wf.version = wf.version + 1
            wf.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(wf)
            return _orm_to_dict(wf)

    async def _delete_db(self, tenant_id: str, workflow_id: str) -> bool:
        from sqlalchemy import select
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        async with self._db() as session:
            await session.execute(
                sa_text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            result = await session.execute(
                select(Workflow).where(
                    Workflow.id == workflow_id,
                    Workflow.tenant_id == tenant_id,
                )
            )
            wf = result.scalar_one_or_none()
            if wf is None:
                return False
            await session.delete(wf)
            await session.commit()
            return True


# ─── Route handlers ───────────────────────────────────────────────────────────

@router.get("", response_model=list[WorkflowOut])
async def list_workflows(request: Request) -> list[WorkflowOut]:
    """List all workflows for the authenticated tenant."""
    tenant = _require_tenant(request)
    store = _get_store(request)
    workflows = await store.list(tenant.tenant_id)
    return [_workflow_to_out(w) for w in workflows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowOut,
)
async def create_workflow(request: Request, body: WorkflowCreate) -> WorkflowOut:
    """Create a new workflow definition."""
    tenant = _require_tenant(request)
    store = _get_store(request)
    wf = await store.create(
        tenant_id=tenant.tenant_id,
        name=body.name,
        description=body.description,
        definition=body.definition,
    )
    return _workflow_to_out(wf)


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: str, request: Request) -> WorkflowOut:
    """Retrieve a single workflow by ID."""
    tenant = _require_tenant(request)
    store = _get_store(request)
    wf = await store.get(tenant.tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
        )
    return _workflow_to_out(wf)


@router.put("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_workflow(
    workflow_id: str,
    request: Request,
    body: WorkflowUpdate,
) -> None:
    """Update an existing workflow.  Increments the version counter."""
    tenant = _require_tenant(request)
    store = _get_store(request)
    result = await store.update(
        tenant_id=tenant.tenant_id,
        workflow_id=workflow_id,
        name=body.name,
        description=body.description,
        definition=body.definition,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
        )


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: str, request: Request) -> None:
    """Permanently delete a workflow."""
    tenant = _require_tenant(request)
    store = _get_store(request)
    deleted = await store.delete(tenant.tenant_id, workflow_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
        )


@router.post(
    "/{workflow_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_workflow(
    workflow_id: str,
    request: Request,
    dry_run: bool = Query(
        default=False,
        description="When true, validates the workflow but does not submit a goal.",
    ),
) -> dict[str, Any]:
    """Execute a saved workflow by converting it to an AgentVerse goal.

    The workflow's name, description, and node count are composed into a
    natural-language goal string that is submitted via ``GoalService``.
    Pass ``dry_run=true`` to validate without actually running anything.
    """
    tenant = _require_tenant(request)
    store = _get_store(request)
    wf = await store.get(tenant.tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
        )

    # Build a goal description from the workflow metadata
    definition = wf.get("definition") or {}
    nodes: list[Any] = definition.get("nodes", [])
    node_count = len(nodes)
    desc = (wf.get("description") or "").strip()
    goal_text = (
        f"Execute workflow '{wf['name']}'"
        + (f": {desc}" if desc else "")
        + f" ({node_count} node{'s' if node_count != 1 else ''})"
    )

    goal_service = getattr(request.app.state, "goal_service", None)

    if dry_run or goal_service is None:
        return {
            "run_id": f"wf-dry-{workflow_id[:8]}",
            "status": "dry_run",
            "workflow_id": workflow_id,
            "goal": goal_text,
        }

    result: dict[str, Any] = await goal_service.submit_goal(
        goal=goal_text,
        tenant_ctx=tenant,
        execution_context={
            "workflow_id": workflow_id,
            "workflow_definition": definition,
        },
    )
    run_id: str = (
        result.get("id") or result.get("goal_id") or f"wf-{workflow_id[:8]}"
    )
    return {
        "run_id": run_id,
        "status": result.get("status", "planning"),
        "workflow_id": workflow_id,
        "goal": goal_text,
    }
