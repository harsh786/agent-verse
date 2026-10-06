"""
Builder API — site and app generation experience.

A builder project is an ordinary code-generation goal: POST /builder/projects
submits it, GET /builder/projects/{id} resolves the project's build goal, and the
goal page shows its progress and output.

There is no live preview / asset serving (owner decision, a10-F229-01): serving
agent-written HTML/JS needs a per-project artifact binding and a separate sandbox
origin, so the always-501 /builder/preview and /builder/assets routes were removed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
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
    # The build is an ordinary goal: track it via GET /builder/projects/{id} or
    # GET /goals/{goal_id}. There is no live preview.
    return BuilderProject(
        project_id=project_id,
        workspace_id=workspace_id,
        status="submitted",
        description=body.description,
        goal_id=goal_id,
    )


@router.get("/projects/{project_id}")
async def get_builder_project(project_id: str, request: Request) -> dict[str, Any]:
    """The project's build goal and its status (a10-F229-02).

    The ``project_id`` POST /builder/projects returns is persisted on the build
    goal (``execution_context.builder_project_id``) and resolved from it, on any
    replica, under the caller's tenant. This route used to be a 501 (and before
    that a stub answering "building" for any id), so the id led nowhere.
    """
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    goal_svc = getattr(request.app.state, "goal_service", None)
    if goal_svc is None:
        raise HTTPException(status_code=503, detail="Builder is unavailable (no goal service)")
    try:
        found = await goal_svc.find_goals_by_context(
            tenant_ctx, "builder_project_id", project_id, limit=10
        )
    except Exception as exc:
        logger.warning("builder_project_lookup_failed error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Builder project status temporarily unavailable; retry"
        ) from exc
    if not found:
        raise HTTPException(status_code=404, detail="Builder project not found")
    build = found[0]
    return {
        "project_id": project_id,
        "goal_id": build["goal_id"],
        "status": build["status"],
        "created_at": build["created_at"],
        # Multi-agent routing can fan one build out to several goals.
        "goal_ids": [g["goal_id"] for g in found],
    }
