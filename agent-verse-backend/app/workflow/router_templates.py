"""Workflow template marketplace API router.

Endpoints:
  GET    /workflow-templates                       List with filters
  GET    /workflow-templates/categories            Category tree
  GET    /workflow-templates/search                Full-text + tag search
  GET    /workflow-templates/{slug}                Detail + step preview
  GET    /workflow-templates/{slug}/preview-run    Dry-run with sample_input
  POST   /workflow-templates/{slug}/fork           Fork into tenant workspace
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.observability.logging import get_logger
from app.workflow.template_store import SystemTemplateStore, TemplateNotFoundError

_log = get_logger(__name__)

router = APIRouter(prefix="/workflow-templates", tags=["workflow-templates"])

# Module-level store singleton (overridden in tests via app.state)
_default_store = SystemTemplateStore()


def _store(request: Request) -> SystemTemplateStore:
    return getattr(request.app.state, "template_store", _default_store)


def _tenant_id(request: Request) -> str:
    return request.app.state.tenant_context.tenant_id


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ForkRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_templates(
    request: Request,
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List system templates with optional category filter."""
    store = _store(request)
    items, total = store.list(category=category, page=page, per_page=per_page)
    return {
        "items": [t.to_dict() for t in items],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/categories")
async def list_categories(request: Request) -> list[dict[str, Any]]:
    """Return all template categories with item counts."""
    store = _store(request)
    return store.categories()


@router.get("/search")
async def search_templates(
    request: Request,
    q: str = Query(..., min_length=1),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Full-text + tag search across all templates."""
    store = _store(request)
    items, total = store.list(category=category, q=q, page=page, per_page=per_page)
    return {
        "items": [t.to_dict() for t in items],
        "total": total,
        "query": q,
        "page": page,
        "per_page": per_page,
    }


@router.get("/{slug}")
async def get_template(slug: str, request: Request) -> dict[str, Any]:
    """Get template detail including full DSL definition."""
    store = _store(request)
    try:
        t = store.get(slug)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return t.to_dict()


@router.get("/{slug}/preview-run")
async def preview_run(slug: str, request: Request) -> dict[str, Any]:
    """Return a dry-run preview using the template's sample_input.

    This does NOT execute the workflow — it returns what would be triggered.
    """
    store = _store(request)
    try:
        t = store.get(slug)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "slug": slug,
        "sample_input": t.sample_input,
        "trigger_type": t.definition.trigger.type if t.definition.trigger else "manual",
        "step_count": len(t.definition.steps),
        "estimated_duration": "< 30s",  # static estimate for preview
        "dry_run": True,
    }


@router.post("/{slug}/fork", status_code=status.HTTP_201_CREATED)
async def fork_template(
    slug: str, body: ForkRequest, request: Request
) -> dict[str, Any]:
    """Fork a system template into the current tenant's workspace."""
    store = _store(request)
    tenant_id = _tenant_id(request)
    try:
        overrides: dict[str, Any] = {}
        if body.name:
            overrides["name"] = body.name
        if body.description:
            overrides["description"] = body.description
        overrides.update(body.overrides)
        definition = store.fork(slug, tenant_id, overrides or None)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "workflow_id": definition.id,
        "name": definition.name,
        "forked_from": slug,
        "tenant_id": tenant_id,
        "status": "draft",
    }
