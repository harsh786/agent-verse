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
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

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
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"Could not start builder: {type(exc).__name__}"
            ) from exc

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


@router.get("/preview/{workspace_id}", response_class=HTMLResponse)
async def serve_preview(workspace_id: str, request: Request) -> HTMLResponse:
    """Serve a preview of the built site for this workspace.

    Tries to load index.html from the artifact store for the project.
    Falls back to a status page showing build progress.
    """
    artifact_store = getattr(request.app.state, "artifact_store", None)

    # Try to find the built index.html in artifacts
    index_content: str | None = None
    if artifact_store is not None:
        try:
            # List artifacts for this workspace
            artifacts = await artifact_store.list_artifacts(workspace_id=workspace_id)
            index_artifact = next(
                (a for a in (artifacts or []) if a.get("name", "").endswith("index.html")),
                None,
            )
            if index_artifact:
                raw = await artifact_store.read_bytes(index_artifact.get("id", ""))
                if raw:
                    index_content = raw.decode("utf-8", errors="replace")
        except Exception:
            pass

    if index_content:
        # Inject a base tag so relative paths resolve correctly
        preview_html = index_content.replace(
            "<head>",
            f'<head><base href="/builder/assets/{workspace_id}/" />'
            '<meta name="robots" content="noindex" />',
        )
        return HTMLResponse(content=preview_html, status_code=200)

    # No built content yet — return a status page
    status_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Preview — Building…</title>
  <meta http-equiv="refresh" content="5" />
  <style>
    body {{ font-family: system-ui, sans-serif; display: flex; align-items: center;
           justify-content: center; height: 100vh; margin: 0;
           background: #0f172a; color: #e2e8f0; }}
    .card {{ text-align: center; padding: 2rem; border-radius: 1rem;
             background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); }}
    .spinner {{ width: 40px; height: 40px; border: 3px solid rgba(255,255,255,0.1);
                border-top-color: #6366f1; border-radius: 50%;
                animation: spin 1s linear infinite; margin: 1rem auto; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  </style>
</head>
<body>
  <div class="card">
    <div class="spinner"></div>
    <h2>Building your site…</h2>
    <p style="color:#94a3b8">Workspace: {workspace_id}</p>
    <p style="color:#64748b;font-size:0.8rem">Auto-refreshes every 5 seconds</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=status_html, status_code=202)


@router.get("/assets/{workspace_id}/{file_path:path}")
async def serve_asset(workspace_id: str, file_path: str, request: Request) -> Response:
    """Serve a static asset (CSS, JS, image) for a workspace preview."""
    artifact_store = getattr(request.app.state, "artifact_store", None)
    if artifact_store is None:
        return Response(status_code=404, content="Artifact store unavailable")

    try:
        artifacts = await artifact_store.list_artifacts(workspace_id=workspace_id)
        target = next(
            (a for a in (artifacts or []) if a.get("name", "") == file_path),
            None,
        )
        if not target:
            return Response(status_code=404, content=f"Asset {file_path} not found")

        raw = await artifact_store.read_bytes(target.get("id", ""))
        if not raw:
            return Response(status_code=404)

        # Determine content type
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        mime_map = {
            "html": "text/html",
            "css": "text/css",
            "js": "application/javascript",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "svg": "image/svg+xml",
            "ico": "image/x-icon",
            "json": "application/json",
            "woff": "font/woff",
            "woff2": "font/woff2",
            "ttf": "font/ttf",
        }
        content_type = mime_map.get(ext, "application/octet-stream")
        return Response(content=raw, media_type=content_type)
    except Exception as exc:
        return Response(status_code=500, content=str(exc))
