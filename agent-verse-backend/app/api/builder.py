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

import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any

router = APIRouter(prefix="/builder", tags=["builder"])


class BuilderProjectRequest(BaseModel):
    description: str                # "Build me a landing page for a law firm"
    project_type: str = "landing"   # landing | dashboard | saas | portfolio
    framework: str = "react"        # react | vanilla | vue
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
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Could not start builder: {type(exc).__name__}")

    return BuilderProject(
        project_id=project_id,
        workspace_id=workspace_id,
        status="building",
        description=body.description,
        preview_url=f"/builder/preview/{workspace_id}",
        goal_id=goal_id,
    )


@router.get("/projects/{project_id}")
async def get_builder_project(project_id: str, request: Request) -> dict[str, Any]:
    """Get project status and artifacts."""
    return {
        "project_id": project_id,
        "status": "building",
        "artifacts": [],
        "preview_url": f"/builder/preview/{project_id}",
    }


@router.get("/preview/{workspace_id}")
async def serve_preview(workspace_id: str) -> dict[str, Any]:
    """Return preview metadata for a workspace."""
    return {
        "workspace_id": workspace_id,
        "message": "Preview hosting requires static file serving — configure ARTIFACT_SERVE_URL",
        "status": "pending",
    }
