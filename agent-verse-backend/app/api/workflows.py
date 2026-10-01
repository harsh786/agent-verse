"""Visual workflow builder REST API.

Endpoints
---------
GET    /workflows              list all workflows for the authenticated tenant
POST   /workflows              create a new workflow definition
GET    /workflows/{id}         retrieve a single workflow
PUT    /workflows/{id}         update name / description / definition (version bumped)
DELETE /workflows/{id}         delete a workflow
POST   /workflows/{id}/run     start a durable workflow-engine run (same path as /trigger)

Design notes
------------
- ``_WorkflowStore`` provides an in-memory implementation that works in tests and
  zero-infra dev mode, and a DB-backed path that is activated by calling
  ``store.set_db(db_session_factory)`` during the FastAPI lifespan startup.
- All DB queries set the ``app.tenant_id`` Postgres GUC so Row-Level Security
  policies on the ``workflows`` table enforce tenant isolation at the DB layer.
- The ``run`` endpoint creates a persisted run and dispatches it through the
  workflow engine (``WorkflowRunner.run``), returning the real run id. With
  ``dry_run=true`` it only validates and returns ``status="dry_run"``. There is
  no goal-submission fallback: engine errors surface as HTTP errors.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.tenancy.context import TenantContext

router = APIRouter(prefix="/workflows", tags=["workflows"])


# Per-workflow ACL — the same levels /api/v1/workflows enforces
# (app/workflow/permissions.py). The visual builder still saves and runs through
# these routes, so they used to be an unchecked side door: a 'viewer' grant
# restricted nothing here.
def _access(level: str) -> Any:
    from app.workflow.permissions import workflow_access

    return [Depends(workflow_access(level))]


# ─── Pydantic schemas ────────────────────────────────────────────────────────


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    definition: dict[str, Any] = Field(default_factory=dict)


class GenerateWorkflowRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)


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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _get_store(request: Request) -> _WorkflowStore:
    store: _WorkflowStore | None = getattr(request.app.state, "workflow_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow store not initialised",
        )
    return store


def _audit(request: Request, action: str, workflow_id: str, note: str = "") -> None:
    """WF-AUDIT: tenant-scoped audit row for a workflow action on this router."""
    from app.workflow.audit_middleware import record_workflow_action

    record_workflow_action(request, action, workflow_id=workflow_id, note=note)


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
        "labels": getattr(wf, "labels", None) or {},
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
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        labels = labels or {}
        if self._db is not None:
            return await self._create_db(tenant_id, name, description, definition, labels)
        now = datetime.now(UTC)
        wf: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "name": name,
            "description": description,
            "definition": definition,
            "labels": labels,
            "status": "draft",
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        self._mem[wf["id"]] = wf
        return wf

    # Fields a partial update may set. ``published_at`` is a service-level
    # convenience (no ORM column) — applied in-memory, dropped on the DB path.
    _UPDATABLE_FIELDS = ("name", "description", "definition", "labels", "status", "published_at")

    async def update(
        self,
        tenant_id: str,
        workflow_id: str,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Partial update — only the provided fields are changed; version bumps."""
        updates = {k: v for k, v in fields.items() if k in self._UPDATABLE_FIELDS}
        if self._db is not None:
            return await self._update_db(tenant_id, workflow_id, updates)
        w = self._mem.get(workflow_id)
        if not w or w["tenant_id"] != tenant_id:
            return None
        w.update(updates)
        w["version"] = w["version"] + 1
        w["updated_at"] = datetime.now(UTC)
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
                sa_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            result = await session.execute(
                select(Workflow)
                .where(Workflow.tenant_id == tenant_id)
                .order_by(Workflow.created_at.desc())
            )
            return [_orm_to_dict(r) for r in result.scalars().all()]

    async def _get_db(self, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        from sqlalchemy import select
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        async with self._db() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
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
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        now = datetime.now(UTC)
        workflow_id = str(uuid.uuid4())
        labels = labels or {}
        async with self._db() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            wf = Workflow(
                id=workflow_id,
                tenant_id=tenant_id,
                name=name,
                description=description,
                definition=definition,
                labels=labels,
                status="draft",
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(wf)
            # Bridge: mirror into the run engine's ``workflow_definitions`` table
            # (same uuid id) so this workflow is triggerable — ``workflow_runs``
            # FK-references it. Same transaction, so the two stay consistent.
            await self._bridge_upsert_definition(
                session,
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                name=name,
                description=description,
                definition=definition,
                status="draft",
            )
            await session.commit()
            # Build the result from local values rather than a post-commit
            # refresh: the tenant GUC set above is transaction-local, so a reload
            # in a fresh transaction would be filtered out by RLS.
            return {
                "id": workflow_id,
                "tenant_id": tenant_id,
                "name": name,
                "description": description,
                "definition": definition,
                "labels": labels,
                "status": "draft",
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }

    # ── workflows → workflow_definitions bridge ────────────────────────────────
    # The visual builder persists to the legacy ``workflows`` table (Text id); the
    # run engine is built around ``workflow_definitions`` (uuid id) and nothing
    # else populates it. These helpers keep a mirror row in sync so an
    # API-created workflow can be triggered and run.

    @staticmethod
    async def _bridge_upsert_definition(
        session: Any,
        *,
        workflow_id: str,
        tenant_id: str,
        name: str,
        description: str,
        definition: dict[str, Any],
        status: str,
    ) -> None:
        """Upsert the run-engine ``workflow_definitions`` mirror row.

        No-op when ``tenant_id`` is not a UUID: the run engine casts tenant_id to
        uuid everywhere, so such tenants cannot use it anyway, and the CAST here
        would abort the surrounding transaction (and the primary create/update).
        """
        from sqlalchemy import text as sa_text

        try:
            uuid.UUID(str(tenant_id))
        except (ValueError, TypeError, AttributeError):
            return
        await session.execute(
            sa_text(
                "INSERT INTO workflow_definitions "
                "(id, tenant_id, name, slug, description, definition_json, status, version) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), :name, :slug, "
                " :description, CAST(:definition AS jsonb), :status, '1.0.0') "
                "ON CONFLICT (id) DO UPDATE SET "
                " name = EXCLUDED.name, description = EXCLUDED.description, "
                " definition_json = EXCLUDED.definition_json, status = EXCLUDED.status, "
                " updated_at = NOW() "
                # Never rewrite another tenant's definition (BYPASSRLS connections).
                "WHERE workflow_definitions.tenant_id = EXCLUDED.tenant_id"
            ),
            {
                "id": workflow_id,
                "tenant_id": str(tenant_id),
                "name": name,
                # slug is UNIQUE(tenant_id, slug); the workflow id guarantees it.
                "slug": workflow_id,
                "description": description or "",
                "definition": json.dumps(definition or {}),
                "status": status,
            },
        )

    @staticmethod
    async def _bridge_delete_definition(
        session: Any, *, workflow_id: str, tenant_id: str
    ) -> None:
        """Delete the mirror row, but only when no runs reference it.

        ``workflow_runs.workflow_id`` FK has no ``ON DELETE CASCADE``; keeping the
        definition when runs exist preserves historical runs' interpretability.
        """
        from sqlalchemy import text as sa_text

        try:
            uuid.UUID(str(tenant_id))
        except (ValueError, TypeError, AttributeError):
            return
        await session.execute(
            sa_text(
                "DELETE FROM workflow_definitions d WHERE d.id = CAST(:id AS uuid) "
                "AND d.tenant_id = CAST(:tid AS uuid) "
                "AND NOT EXISTS (SELECT 1 FROM workflow_runs r WHERE r.workflow_id = d.id)"
            ),
            {"id": workflow_id, "tid": str(tenant_id)},
        )

    async def _update_db(
        self,
        tenant_id: str,
        workflow_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        from sqlalchemy import select
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        async with self._db() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
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
            # Apply only fields that map to a real column (published_at has none).
            for key, value in updates.items():
                if hasattr(wf, key):
                    setattr(wf, key, value)
            wf.version = wf.version + 1
            wf.updated_at = datetime.now(UTC)
            # Snapshot while still inside the tenant-scoped transaction, so the
            # return value never depends on a post-commit reload (RLS-filtered).
            snapshot = _orm_to_dict(wf)
            # Bridge: keep the run-engine mirror row in sync with the edit.
            await self._bridge_upsert_definition(
                session,
                workflow_id=snapshot["id"],
                tenant_id=tenant_id,
                name=snapshot["name"],
                description=snapshot["description"],
                definition=snapshot["definition"],
                status=snapshot["status"],
            )
            await session.commit()
            return snapshot

    async def _delete_db(self, tenant_id: str, workflow_id: str) -> bool:
        from sqlalchemy import select
        from sqlalchemy import text as sa_text

        from app.db.models.workflow import Workflow

        async with self._db() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
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
            # Bridge: drop the run-engine mirror row too (unless runs reference it).
            await self._bridge_delete_definition(
                session, workflow_id=workflow_id, tenant_id=tenant_id
            )
            await session.commit()
            return True


def _plan_to_canvas(plan: Any) -> dict[str, Any]:
    """Convert a WorkflowPlan to canvas-ready ``{nodes, edges}`` format."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    goal_text: str = getattr(plan, "goal", "")
    nodes.append(
        {
            "id": "trigger",
            "type": "trigger",
            "label": "Start",
            "subtitle": (goal_text[:40] + "…") if len(goal_text) > 40 else goal_text,
            "position": {"x": 250, "y": 50},
        }
    )

    steps = getattr(plan, "steps", [])
    for i, step in enumerate(steps):
        node_type = "tool_call" if getattr(step, "tool", "") else "agent_step"
        desc: str = getattr(step, "description", "")
        label = (desc[:40] + "…") if len(desc) > 40 else desc
        nodes.append(
            {
                "id": step.id,
                "type": node_type,
                "label": label,
                "subtitle": getattr(step, "tool", "") or "",
                "position": {"x": 250, "y": 150 + i * 100},
                "tool": getattr(step, "tool", ""),
                "depends_on": list(getattr(step, "depends_on", [])),
                "can_parallel": getattr(step, "can_parallel", True),
            }
        )

    end_y = 200 + len(steps) * 100
    nodes.append(
        {
            "id": "end",
            "type": "end",
            "label": "End",
            "position": {"x": 250, "y": end_y},
        }
    )

    # trigger → root steps (no depends_on)
    root_steps = [s for s in steps if not getattr(s, "depends_on", [])]
    if not root_steps and steps:
        root_steps = [steps[0]]
    for step in root_steps:
        edges.append({"id": f"e_trigger_{step.id}", "source": "trigger", "target": step.id})

    # dependency edges
    for step in steps:
        for dep in getattr(step, "depends_on", []):
            edges.append({"id": f"e_{dep}_{step.id}", "source": dep, "target": step.id})

    # terminal steps (not a dep of any other) → end
    all_dep_targets = {dep for s in steps for dep in getattr(s, "depends_on", [])}
    terminal = [s for s in steps if s.id not in all_dep_targets]
    if not terminal and steps:
        terminal = [steps[-1]]
    for step in terminal:
        edges.append({"id": f"e_{step.id}_end", "source": step.id, "target": "end"})

    return {"nodes": nodes, "edges": edges}


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
    _audit(request, "created", str(wf["id"]), f"name={body.name}")
    return _workflow_to_out(wf)


@router.post("/generate", status_code=status.HTTP_200_OK)
async def generate_workflow(request: Request, body: GenerateWorkflowRequest) -> dict[str, Any]:
    """Generate a workflow canvas (nodes + edges) from a natural-language goal.

    Calls ``WorkflowPlanner.plan()`` directly — no dry-run hack.
    Falls back to a heuristic plan when no LLM provider is configured.
    """
    tenant = _require_tenant(request)
    from app.agent.workflow_planner import WorkflowPlanner

    provider = getattr(request.app.state, "_app_provider", None)
    planner = WorkflowPlanner(provider=provider)
    plan = await planner.plan(goal=body.goal, tenant_ctx=tenant)
    return _plan_to_canvas(plan)


@router.get("/{workflow_id}", response_model=WorkflowOut, dependencies=_access("viewer"))
async def get_workflow(workflow_id: str, request: Request) -> WorkflowOut:
    """Retrieve a single workflow by ID."""
    tenant = _require_tenant(request)
    store = _get_store(request)
    wf = await store.get(tenant.tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return _workflow_to_out(wf)


@router.put(
    "/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_access("editor")
)
async def update_workflow(
    workflow_id: str,
    request: Request,
    body: WorkflowUpdate,
) -> None:
    """Update an existing workflow.  Increments the version counter.

    Refused (409) while the workflow is pending publish approval: the approver
    reviews exactly what was submitted (same rule as PATCH /api/v1/workflows)."""
    tenant = _require_tenant(request)
    store = _get_store(request)
    current = await store.get(tenant.tenant_id, workflow_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    if current.get("status") == "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "workflow is pending publish approval; reject it (or unpublish) "
                "before editing"
            ),
        )
    if current.get("status") == "published":
        # Same rule as PATCH /api/v1/workflows: a live definition is immutable;
        # unpublish, edit, publish (and pass publish approval) for a new version.
        from app.workflow.service import PUBLISHED_EDIT_REFUSED

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PUBLISHED_EDIT_REFUSED)
    result = await store.update(
        tenant_id=tenant.tenant_id,
        workflow_id=workflow_id,
        name=body.name,
        description=body.description,
        definition=body.definition,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    _audit(request, "updated", workflow_id, "changed=name,description,definition")


@router.delete(
    "/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_access("editor")
)
async def delete_workflow(workflow_id: str, request: Request) -> None:
    """Permanently delete a workflow."""
    tenant = _require_tenant(request)
    store = _get_store(request)
    deleted = await store.delete(tenant.tenant_id, workflow_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    _audit(request, "deleted", workflow_id)


class WorkflowRunRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    # Honoured like the ?dry_run query flag: a caller asking for a dry run must
    # never get a real execution.
    dry_run: bool = False


@router.post(
    "/{workflow_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_access("runner"),
)
async def run_workflow(
    workflow_id: str,
    request: Request,
    dry_run: bool = Query(
        default=False,
        description="When true, validates the workflow but does not execute.",
    ),
    body: WorkflowRunRequest | None = None,
) -> dict[str, Any]:
    """Start a durable run of a saved workflow (same path as ``/trigger``).

    Creates a persisted ``workflow_runs`` row and dispatches it through the
    workflow engine (Celery per-plan queue, or inline when no broker is wired).
    Returns the REAL run id; poll ``GET /api/v1/runs/{run_id}`` for progress.

    Old bug: this ran ``WorkflowExecutor`` synchronously inside the request with
    no persistence (a random, unqueryable run id) and, on any error, silently
    fell back to submitting a generic goal "Execute workflow <name>", reporting
    success for something that was never the workflow. Errors are now HTTP
    errors (422 invalid inputs/definition, 503 engine unavailable).

    Pass ``dry_run=true`` to validate and return plan metadata without executing.
    """
    tenant = _require_tenant(request)
    store = _get_store(request)
    wf = await store.get(tenant.tenant_id, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

    definition = wf.get("definition") or {}
    desc = (wf.get("description") or "").strip()
    goal_text = f"Execute workflow '{wf['name']}'" + (f": {desc}" if desc else "")

    if dry_run or (body is not None and body.dry_run):
        return {
            "run_id": f"wf-dry-{workflow_id[:8]}",
            "status": "dry_run",
            "workflow_id": workflow_id,
            "goal": goal_text,
            "definition": definition,
        }

    from app.workflow.runner import WorkflowEngineUnavailableError, WorkflowValidationError

    runner = getattr(request.app.state, "workflow_runner", None)
    if runner is None or getattr(runner, "_run_store", None) is None:
        # The in-memory runner has no run store: it would "execute" a stub
        # definition and nothing could ever be queried. Refuse honestly.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Durable workflow engine is not available",
        )
    try:
        run_id = await runner.run(
            workflow_id=workflow_id,
            tenant_id=tenant.tenant_id,
            inputs=(body.inputs if body else {}),
            trigger_type="api",
        )
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except WorkflowEngineUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except KeyError as exc:  # definition not bridged into workflow_definitions
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Workflow definition is not runnable: {exc}",
        ) from exc
    except (ValueError, TypeError) as exc:  # DSL failed to parse/compile
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid workflow definition: {exc}",
        ) from exc

    run_status = "pending"
    try:
        rec = await runner._run_store.get(tenant.tenant_id, run_id)
        if isinstance(rec, dict) and rec.get("status"):
            run_status = str(rec["status"])
    except Exception:  # pragma: no cover - status read is informational
        pass
    _audit(request, "run_triggered", workflow_id, f"run_id={run_id}; dry_run=False")
    return {
        "run_id": run_id,
        "status": run_status,
        "workflow_id": workflow_id,
        "run_url": f"/api/v1/runs/{run_id}",
    }
