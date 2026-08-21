"""Skills Runtime API."""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.skills_runtime.executor import (
    SkillExecutor,
    permission_checker,
    trigger_matcher,
)
from app.skills_runtime.models import (
    BUILTIN_SKILLS,
    SkillDefinition,
    SkillScope,
    SkillStatus,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/skills-runtime", tags=["skills-runtime"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


# Platform registry (builtins loaded at startup)
_platform_skills: dict[str, dict] = {}
_tenant_skills: dict[str, list] = {}  # tenant_id → skills
_executions: dict[str, deque] = {}  # tenant_id → bounded deque of executions (maxlen=1000)
_enabled_skills: dict[str, set] = {}  # tenant_id → {skill_ids}
_skill_versions: dict[str, list] = {}  # skill_id → list of archived versions
_loaded_tenants: set[str] = set()  # tenant_ids whose custom skills have been loaded from DB

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


class ExecuteByIdRequest(BaseModel):
    skill_id: str
    input_context: str
    goal_id: str | None = None


class AutoMatchRequest(BaseModel):
    goal: str
    goal_id: str | None = None


class PermissionToggleRequest(BaseModel):
    skill_id: str


# ── Helper: dict → SkillDefinition ────────────────────────────────────────────


def _dict_to_skill_def(skill_dict: dict[str, Any]) -> SkillDefinition:
    """Convert an in-memory skill dict to a SkillDefinition dataclass."""
    return SkillDefinition(
        skill_id=skill_dict["skill_id"],
        name=skill_dict["name"],
        description=skill_dict["description"],
        scope=SkillScope(skill_dict.get("scope", "platform")),
        trigger_hints=skill_dict.get("trigger_hints", []),
        instructions=skill_dict.get("instructions", ""),
        allowed_tools=skill_dict.get("allowed_tools", []),
        permissions_required=skill_dict.get("permissions_required", []),
        output_contract=skill_dict.get("output_contract", {}),
        version=skill_dict.get("version", "1.0.0"),
        status=SkillStatus(skill_dict.get("status", "active")),
        tenant_id=skill_dict.get("tenant_id"),
        author=skill_dict.get("author", "system"),
        is_builtin=skill_dict.get("is_builtin", False),
        metadata=skill_dict.get("metadata", {}),
        created_at=skill_dict.get("created_at"),
    )


# ── DB persistence helpers (write-through cache for custom tenant skills) ──────


async def _db_save_skill(skill_dict: dict[str, Any], db_factory: Any) -> None:
    """Persist a custom tenant skill to the skills table.

    Uses the real 0074 migration schema: id=String(32), visibility, is_active.
    Fails silently — in-memory store is the source of truth.
    """
    if db_factory is None:
        return
    try:
        from sqlalchemy import text

        from app.db.rls import system_session

        # The DB id column is String(32); strip UUID dashes.
        db_id = skill_dict["skill_id"].replace("-", "")
        async with db_factory() as session, session.begin(), system_session(session):
            await session.execute(
                text("""
                    INSERT INTO skills (
                        id, tenant_id, name, version, description,
                        trigger_hints, instructions, few_shot_examples,
                        allowed_tools, required_connectors, token_estimate,
                        visibility, is_active, created_by, created_at, updated_at
                    ) VALUES (
                        :id, :tenant_id, :name, :version, :description,
                        :trigger_hints, :instructions, :few_shot_examples,
                        :allowed_tools, :required_connectors, :token_estimate,
                        :visibility, :is_active, :created_by, :created_at, NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        name         = EXCLUDED.name,
                        description  = EXCLUDED.description,
                        instructions = EXCLUDED.instructions,
                        trigger_hints = EXCLUDED.trigger_hints,
                        allowed_tools = EXCLUDED.allowed_tools,
                        is_active    = EXCLUDED.is_active,
                        updated_at   = NOW()
                """),
                {
                    "id": db_id,
                    "tenant_id": skill_dict.get("tenant_id", ""),
                    "name": skill_dict["name"],
                    "version": skill_dict.get("version", "1.0.0"),
                    "description": skill_dict["description"],
                    "trigger_hints": json.dumps(skill_dict.get("trigger_hints", [])),
                    "instructions": skill_dict.get("instructions", ""),
                    "few_shot_examples": json.dumps([]),
                    "allowed_tools": json.dumps(skill_dict.get("allowed_tools", [])),
                    "required_connectors": json.dumps([]),
                    "token_estimate": 0,
                    "visibility": "tenant",
                    "is_active": True,
                    "created_by": skill_dict.get("tenant_id", ""),
                    "created_at": skill_dict.get("created_at"),
                },
            )
    except Exception as exc:
        _log.warning(
            "skill_db_persist_failed skill_id=%s error=%s",
            skill_dict.get("skill_id"),
            exc,
        )


async def _load_tenant_skills_from_db(tenant_id: str, db_factory: Any) -> None:
    """Load persisted custom skills for a tenant into the in-memory cache.

    Idempotent — skips if the tenant has already been loaded this process lifetime.
    """
    if db_factory is None or tenant_id in _loaded_tenants:
        return
    try:
        from sqlalchemy import text

        async with db_factory() as session, session.begin():
            result = await session.execute(
                text(
                    "SELECT id, tenant_id, name, version, description, "
                    "trigger_hints, instructions, allowed_tools, is_active, created_at "
                    "FROM skills WHERE tenant_id = :tid AND is_active = true"
                ),
                {"tid": tenant_id},
            )
            rows = result.fetchall()
            existing_ids = {s["skill_id"] for s in _tenant_skills.get(tenant_id, [])}
            for row in rows:
                # Convert 32-char hex ID back to standard UUID format
                try:
                    standard_id = str(uuid.UUID(hex=row.id))
                except Exception:
                    standard_id = row.id
                if standard_id in existing_ids:
                    continue
                _tenant_skills.setdefault(tenant_id, []).append(
                    {
                        "skill_id": standard_id,
                        "tenant_id": row.tenant_id,
                        "name": row.name,
                        "description": row.description,
                        "trigger_hints": (
                            row.trigger_hints if isinstance(row.trigger_hints, list) else []
                        ),
                        "instructions": row.instructions or "",
                        "allowed_tools": (
                            row.allowed_tools if isinstance(row.allowed_tools, list) else []
                        ),
                        "permissions_required": [],
                        "version": row.version or "1.0.0",
                        "scope": "tenant",
                        "status": "active",
                        "is_builtin": False,
                        "author": row.tenant_id[:12] if row.tenant_id else "system",
                        "created_at": str(row.created_at) if row.created_at else None,
                    }
                )
        _loaded_tenants.add(tenant_id)
    except Exception as exc:
        _log.debug("skill_db_load_failed tenant=%s error=%s", tenant_id, exc)
        # Mark as loaded anyway so we don't retry on every request
        _loaded_tenants.add(tenant_id)


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
            skills.append(
                {
                    **s,
                    "enabled": s["skill_id"] in _enabled_skills.get(tenant.tenant_id, set()),
                    "is_platform": True,
                }
            )

    # Tenant skills
    for s in _tenant_skills.get(tenant.tenant_id, []):
        if status and s.get("status") != status:
            continue
        skills.append({**s, "enabled": True, "is_platform": False})

    return {"skills": skills, "total": len(skills)}


# ── Executor-backed endpoints (declared before /{skill_id} for routing priority) ──


@router.get("/match")
async def match_skills(
    request: Request,
    goal: str = Query(...),
) -> dict[str, Any]:
    """Get top matching skills for a goal without executing (uses TriggerMatcher)."""
    _require_tenant(request)
    skills = [_dict_to_skill_def(s) for s in _platform_skills.values()]
    ranked = trigger_matcher.rank_skills(goal, skills)
    return {
        "matches": [
            {"skill_id": skill.skill_id, "name": skill.name, "score": round(score, 4)}
            for skill, score in ranked
        ]
    }


@router.post("/execute")
async def execute_skill_by_id(
    request: Request,
    body: ExecuteByIdRequest,
) -> dict[str, Any]:
    """Execute a skill by ID supplied in the request body."""
    tenant = _require_tenant(request)

    skill_dict = _platform_skills.get(body.skill_id)
    if not skill_dict:
        tenant_skills = _tenant_skills.get(tenant.tenant_id, [])
        skill_dict = next((s for s in tenant_skills if s["skill_id"] == body.skill_id), None)
    if not skill_dict:
        raise HTTPException(404, f"Skill {body.skill_id!r} not found")

    provider = getattr(request.app.state, "_app_provider", None)
    executor = SkillExecutor(
        permission_checker=permission_checker,
        trigger_matcher=trigger_matcher,
        provider=provider,
    )
    result = await executor.execute(
        skill=_dict_to_skill_def(skill_dict),
        input_context=body.input_context,
        tenant_id=tenant.tenant_id,
        goal_id=body.goal_id,
    )
    return {
        "execution_id": result.execution_id,
        "skill_id": result.skill_id,
        "output": result.output,
        "success": result.success,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


@router.post("/execute/match")
async def execute_best_match(
    request: Request,
    body: AutoMatchRequest,
) -> dict[str, Any]:
    """Auto-match the best skill for a goal and execute it."""
    tenant = _require_tenant(request)

    skills = [_dict_to_skill_def(s) for s in _platform_skills.values()]
    ranked = trigger_matcher.rank_skills(body.goal, skills)
    if not ranked:
        return {"matched": False}

    best_skill, score = ranked[0]
    provider = getattr(request.app.state, "_app_provider", None)
    executor = SkillExecutor(
        permission_checker=permission_checker,
        trigger_matcher=trigger_matcher,
        provider=provider,
    )
    result = await executor.execute(
        skill=best_skill,
        input_context=body.goal,
        tenant_id=tenant.tenant_id,
        goal_id=body.goal_id,
    )
    return {
        "skill_id": best_skill.skill_id,
        "score": round(score, 4),
        "execution": {
            "execution_id": result.execution_id,
            "output": result.output,
            "success": result.success,
            "error": result.error,
            "duration_ms": result.duration_ms,
        },
    }


@router.post("/permissions/enable")
async def permission_enable_skill(
    request: Request,
    body: PermissionToggleRequest,
) -> dict[str, Any]:
    """Re-enable a skill for the current tenant via ScopedPermissionChecker."""
    tenant = _require_tenant(request)
    permission_checker.enable_for_tenant(tenant.tenant_id, body.skill_id)
    return {"skill_id": body.skill_id, "status": "enabled"}


@router.post("/permissions/disable")
async def permission_disable_skill(
    request: Request,
    body: PermissionToggleRequest,
) -> dict[str, Any]:
    """Disable a skill for the current tenant via ScopedPermissionChecker."""
    tenant = _require_tenant(request)
    permission_checker.disable_for_tenant(tenant.tenant_id, body.skill_id)
    return {"skill_id": body.skill_id, "status": "disabled"}


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
    now = datetime.datetime.now(datetime.UTC).isoformat()
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

    # Write-through: persist to DB (non-blocking, best-effort)
    db_factory = getattr(request.app.state, "db_session_factory", None)
    if db_factory is None:
        try:
            from app.db.session import get_session_factory

            db_factory = get_session_factory()
        except Exception:
            db_factory = None
    import asyncio

    _save_task = asyncio.create_task(_db_save_skill(skill, db_factory))
    # Suppress the task reference warning; fire-and-forget with best-effort error logging.
    _save_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

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
    now = datetime.datetime.now(datetime.UTC).isoformat()

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
            resp = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=1000,
                )
            )
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
    _executions.setdefault(tenant.tenant_id, deque(maxlen=1000)).append(execution)

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
    executions = [e for e in _executions.get(tenant.tenant_id, []) if e["skill_id"] == skill_id]
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
    old_version["archived_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    _skill_versions.setdefault(skill_id, []).append(old_version)

    # Update skill fields
    skill.update(
        {
            k: v
            for k, v in body.items()
            if k in ("name", "description", "trigger_hints", "instructions", "allowed_tools")
        }
    )

    # Increment patch version
    current_version = skill.get("version", "1.0.0")
    parts = current_version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    skill["version"] = ".".join(parts)
    skill["updated_at"] = datetime.datetime.now(datetime.UTC).isoformat()

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
                matches.append(
                    {
                        "skill_id": skill["skill_id"],
                        "name": skill["name"],
                        "matched_hint": hint,
                        "enabled": skill["skill_id"]
                        in _enabled_skills.get(tenant.tenant_id, set()),
                    }
                )
                break

    for skill in _tenant_skills.get(tenant.tenant_id, []):
        for hint in skill.get("trigger_hints", []):
            if hint.lower() in trigger or trigger in hint.lower():
                matches.append(
                    {
                        "skill_id": skill["skill_id"],
                        "name": skill["name"],
                        "matched_hint": hint,
                        "enabled": True,
                    }
                )
                break

    return {"matches": matches, "trigger": trigger}
