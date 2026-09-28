"""
Builder API — site and app generation experience.

The builder orchestrates a specialized agent with:
- persistent project workspace (artifact store)
- code tools (CodeInterpreter)
- frontend-design skill
- live preview via artifact serving

Phase 9 V1: static sites/SPAs built in sandbox (npm build)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.errors import PlatformError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/builder", tags=["builder"])


class BuilderProjectRequest(BaseModel):
    description: str  # "Build me a landing page for a law firm"
    project_type: str = "landing"  # landing | dashboard | saas | portfolio
    framework: str = "react"  # react | vanilla | vue
    tenant_goal_template: str | None = None


class BuilderProject(BaseModel):
    project_id: str
    workspace_id: str
    status: str
    description: str
    preview_url: str | None = None
    goal_id: str | None = None


@router.post("/projects")
async def create_builder_project(
    body: BuilderProjectRequest,
    request: Request,
) -> BuilderProject:
    """Create a new builder project and submit a code-generation goal."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    project_id = uuid.uuid4().hex[:12]
    workspace_id = uuid.uuid4().hex[:12]

    app_state = request.app.state
    goal_svc = getattr(app_state, "goal_service", None)

    # Build the goal for the builder agent
    goal_text = (
        f"Build a {body.project_type} website using {body.framework}. "
        f"Requirements: {body.description}. "
        f"Project ID: {project_id}. "
        f"Use the frontend-design skill. Write clean, production-quality code. "
        f"Create all necessary files (index.html/App.tsx, styles, components). "
        f"Save all files as artifacts with project_id={project_id}."
    )

    goal_id = None
    if goal_svc is not None:
        try:
            result = await goal_svc.submit_goal(
                goal=goal_text,
                priority="high",
                dry_run=False,
                tenant_ctx=tenant_ctx,
                execution_context={"builder_project_id": project_id, "workspace_id": workspace_id},
            )
            goal_id = result.get("goal_id")
        except (HTTPException, PlatformError):
            # Client-attributable refusals carry their own status: a plan limit is
            # 429, an exhausted budget 402, an unknown agent 404, … (PlatformError
            # is rendered by the app-wide handler). Collapsing them into 503 told a
            # tenant at its concurrent-goal limit that the SERVER was unavailable.
            raise
        except Exception as exc:
            logger.exception("builder_goal_submit_failed project_id=%s", project_id)
            raise HTTPException(
                status_code=503, detail=f"Could not start builder: {type(exc).__name__}"
            ) from exc

    if goal_id is None:
        # No goal service → nothing was started; do not claim "building".
        raise HTTPException(status_code=503, detail="Builder is unavailable (no goal service)")
    # The build is an ordinary goal: track it via GET /goals/{goal_id}. There is
    # no live preview (see serve_preview), so no preview_url is advertised.
    return BuilderProject(
        project_id=project_id,
        workspace_id=workspace_id,
        status="submitted",
        description=body.description,
        preview_url=None,
        goal_id=goal_id,
    )


_NOT_IMPLEMENTED = (
    "Builder project status and live preview are NOT IMPLEMENTED: builds run as "
    "ordinary goals (track them via GET /goals/{goal_id}); nothing records a "
    "project's artifacts per workspace or scopes them to a tenant, so no preview "
    "can be served."
)


@router.get("/projects/{project_id}")
async def get_builder_project(project_id: str, request: Request) -> dict[str, Any]:
    """NOT IMPLEMENTED (501).

    This was a stub that answered ``status: building, artifacts: []`` for any id
    (including ones that never existed), forever.
    """
    if getattr(request.state, "tenant", None) is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/preview/{workspace_id}")
async def serve_preview(workspace_id: str, request: Request) -> Response:
    """NOT IMPLEMENTED (501).

    The old handler listed artifacts by a workspace-id substring across ALL
    tenants (no tenant scoping), called ``read_bytes`` positionally although it
    is keyword-only (a TypeError swallowed by a bare except), and so always
    showed a "Building..." page that refreshed forever.
    """
    if getattr(request.state, "tenant", None) is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/assets/{workspace_id}/{file_path:path}")
async def serve_asset(workspace_id: str, file_path: str, request: Request) -> Response:
    """NOT IMPLEMENTED (501) — same reasons as :func:`serve_preview` (it also
    returned ``str(exc)`` in a 500 body)."""
    if getattr(request.state, "tenant", None) is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
