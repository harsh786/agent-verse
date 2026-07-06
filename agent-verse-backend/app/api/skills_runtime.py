"""Skills Runtime API."""
from __future__ import annotations
import uuid
import datetime
from typing import Any
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field
from app.skills_runtime.models import BUILTIN_SKILLS

router = APIRouter(prefix="/skills-runtime", tags=["skills-runtime"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


# Platform registry (builtins loaded at startup)
_platform_skills: dict[str, dict] = {}
_tenant_skills: dict[str, list] = {}  # tenant_id → skills
_executions: dict[str, list] = {}  # tenant_id → executions
_enabled_skills: dict[str, set] = {}  # tenant_id → {skill_ids}
_skill_versions: dict[str, list] = {}  # skill_id → list of archived versions

# Load builtins
for _s in BUILTIN_SKILLS:
    _platform_skills[_s["skill_id"]] = {
        **_s,
        "scope": "platform",
        "status": "active",
        "author": "AgentVerse",
        "created_at": "2024-01-01T00:00:00Z",
    }


class CreateSkillRequest(BaseModel):
    name: str
    description: str
    trigger_hints: list[str] = Field(default_factory=list)
    instructions: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    permissions_required: list[str] = Field(default_factory=list)
    version: str = "1.0.0"


class ExecuteSkillRequest(BaseModel):
    input_context: str
    goal_id: str | None = None


@router.get("")
async def list_skills(
    request: Request,
    include_platform: bool = Query(default=True),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """List available skills for the tenant."""
    tenant = _require_tenant(request)
    skills = []

    if include_platform:
        for s in _platform_skills.values():
            if status and s.get("status") != status:
                continue
            skills.append({
                **s,
                "enabled": s["skill_id"] in _enabled_skills.get(tenant.tenant_id, set()),
                "is_platform": True,
            })

    # Tenant skills
    for s in _tenant_skills.get(tenant.tenant_id, []):
        if status and s.get("status") != status:
            continue
        skills.append({**s, "enabled": True, "is_platform": False})

    return {"skills": skills, "total": len(skills)}


@router.get("/{skill_id}")
async def get_skill(request: Request, skill_id: str) -> dict[str, Any]:
    """Get a specific skill."""
    tenant = _require_tenant(request)

    skill = _platform_skills.get(skill_id)
    if not skill:
        tenant_skills = _tenant_skills.get(tenant.tenant_id, [])
        skill = next((s for s in tenant_skills if s["skill_id"] == skill_id), None)

    if not skill:
        raise HTTPException(404, f"Skill {skill_id} not found")

    return {**skill, "enabled": skill_id in _enabled_skills.get(tenant.tenant_id, set())}


@router.post("")
async def create_tenant_skill(request: Request, body: CreateSkillRequest) -> dict[str, Any]:
    """Create a tenant-specific skill."""
    tenant = _require_tenant(request)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    skill_id = str(uuid.uuid4())

    skill = {
        "skill_id": skill_id,
        "name": body.name,
        "description": body.description,
        "trigger_hints": body.trigger_hints,
        "instructions": body.instructions,
        "allowed_tools": body.allowed_tools,
        "permissions_required": body.permissions_required,
        "version": body.version,
        "scope": "tenant",
        "status": "active",
        "tenant_id": tenant.tenant_id,
        "author": tenant.tenant_id[:12],
        "is_builtin": False,
        "created_at": now,
    }
    _tenant_skills.setdefault(tenant.tenant_id, []).append(skill)
    return {"skill_id": skill_id, "status": "created"}


@router.post("/{skill_id}/enable")
async def enable_skill(request: Request, skill_id: str) -> dict[str, Any]:
    """Enable a platform skill for this tenant."""
    tenant = _require_tenant(request)

    if skill_id not in _platform_skills and not any(
        s["skill_id"] == skill_id for s in _tenant_skills.get(tenant.tenant_id, [])
    ):
        raise HTTPException(404, f"Skill {skill_id} not found")

    _enabled_skills.setdefault(tenant.tenant_id, set()).add(skill_id)
    return {"skill_id": skill_id, "status": "enabled"}


@router.post("/{skill_id}/disable")
async def disable_skill(request: Request, skill_id: str) -> dict[str, Any]:
    """Disable a skill for this tenant."""
    tenant = _require_tenant(request)
    _enabled_skills.setdefault(tenant.tenant_id, set()).discard(skill_id)
    return {"skill_id": skill_id, "status": "disabled"}


@router.post("/{skill_id}/execute")
async def execute_skill(
    request: Request,
    skill_id: str,
    body: ExecuteSkillRequest,
) -> dict[str, Any]:
    """Execute a skill against the given input."""
    tenant = _require_tenant(request)

    skill = _platform_skills.get(skill_id)
    if not skill:
        tenant_skills = _tenant_skills.get(tenant.tenant_id, [])
        skill = next((s for s in tenant_skills if s["skill_id"] == skill_id), None)

    if not skill:
        raise HTTPException(404, f"Skill {skill_id} not found")

    import time
    start = time.monotonic()
    execution_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Execute using LLM if available
    provider = getattr(request.app.state, "_app_provider", None)
    output = ""
    success = False
    error = None
    model_used = None

    if provider:
        try:
            from app.providers.base import CompletionRequest, Message
            prompt = (
                f"You are a specialized {skill['name']} skill.\n\n"
                f"Instructions: {skill['instructions']}\n\n"
                f"Input: {body.input_context[:2000]}"
            )
            resp = await provider.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=1000,
            ))
            output = resp.content
            success = True
            model_used = resp.model
        except Exception as exc:
            error = str(exc)
    else:
        # Fallback
        output = (
            f"[{skill['name']} Skill] Processing: {body.input_context[:100]}..."
            " (LLM provider not configured)"
        )
        success = True

    execution = {
        "execution_id": execution_id,
        "skill_id": skill_id,
        "skill_name": skill["name"],
        "tenant_id": tenant.tenant_id,
        "goal_id": body.goal_id,
        "input_preview": body.input_context[:100],
        "output_preview": output[:200],
        "success": success,
        "error": error,
        "duration_ms": round((time.monotonic() - start) * 1000, 1),
        "model_used": model_used,
        "created_at": now,
    }
    _executions.setdefault(tenant.tenant_id, []).append(execution)

    return {
        "execution_id": execution_id,
        "skill_id": skill_id,
        "output": output,
        "success": success,
        "error": error,
        "duration_ms": execution["duration_ms"],
    }


@router.get("/{skill_id}/executions")
async def list_skill_executions(request: Request, skill_id: str) -> dict[str, Any]:
    """List execution history for a skill."""
    tenant = _require_tenant(request)
    executions = [
        e for e in _executions.get(tenant.tenant_id, [])
        if e["skill_id"] == skill_id
    ]
    return {"executions": list(reversed(executions))[:20], "total": len(executions)}


@router.put("/{skill_id}")
async def update_tenant_skill(request: Request, skill_id: str) -> dict[str, Any]:
    """Update a tenant skill and create a new version."""
    import copy

    tenant = _require_tenant(request)
    body = await request.json()

    # Find existing skill
    tenant_skills = _tenant_skills.get(tenant.tenant_id, [])
    skill = next((s for s in tenant_skills if s["skill_id"] == skill_id), None)
    if not skill:
        raise HTTPException(404, "Skill not found")

    # Store old version
    old_version = copy.deepcopy(skill)
    old_version["archived_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _skill_versions.setdefault(skill_id, []).append(old_version)

    # Update skill fields
    skill.update({
        k: v for k, v in body.items()
        if k in ("name", "description", "trigger_hints", "instructions", "allowed_tools")
    })

    # Increment patch version
    current_version = skill.get("version", "1.0.0")
    parts = current_version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    skill["version"] = ".".join(parts)
    skill["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return {"skill_id": skill_id, "version": skill["version"], "status": "updated"}


@router.get("/{skill_id}/versions")
async def get_skill_versions(request: Request, skill_id: str) -> dict[str, Any]:
    """List version history for a skill."""
    tenant = _require_tenant(request)
    tenant_skills = _tenant_skills.get(tenant.tenant_id, [])
    skill = next((s for s in tenant_skills if s["skill_id"] == skill_id), None)
    if not skill:
        # Check platform skills too
        platform = _platform_skills.get(skill_id)
        if not platform:
            raise HTTPException(404, "Skill not found")
        skill = platform

    versions = _skill_versions.get(skill_id, [])
    return {
        "versions": versions,
        "current_version": skill.get("version", "1.0.0"),
    }


@router.post("/match-trigger")
async def match_skill_by_trigger(request: Request) -> dict[str, Any]:
    """Find skills matching a trigger phrase."""
    tenant = _require_tenant(request)
    body = await request.json()
    trigger = body.get("trigger", "").lower()

    matches = []
    for skill in _platform_skills.values():
        for hint in skill.get("trigger_hints", []):
            if hint.lower() in trigger or trigger in hint.lower():
                matches.append({
                    "skill_id": skill["skill_id"],
                    "name": skill["name"],
                    "matched_hint": hint,
                    "enabled": skill["skill_id"] in _enabled_skills.get(tenant.tenant_id, set()),
                })
                break

    for skill in _tenant_skills.get(tenant.tenant_id, []):
        for hint in skill.get("trigger_hints", []):
            if hint.lower() in trigger or trigger in hint.lower():
                matches.append({
                    "skill_id": skill["skill_id"],
                    "name": skill["name"],
                    "matched_hint": hint,
                    "enabled": True,
                })
                break

    return {"matches": matches, "trigger": trigger}
