"""Skills Runtime API."""

from __future__ import annotations

import datetime
import logging
import uuid
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.skills_runtime import tenant_store
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
from app.skills_runtime.state_store import (
    SkillStateUnavailableError,
    disabled_skills,
    set_skill_enabled,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/skills-runtime", tags=["skills-runtime"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


async def _disabled_for(request: Request, tenant_id: str) -> set[str]:
    """The tenant's disabled skills from the durable store; 503 when unreadable."""
    try:
        return await disabled_skills(_state_db(request), tenant_id)
    except SkillStateUnavailableError as exc:
        _log.warning("skill_state_read_failed tenant=%s: %s", tenant_id, exc)
        raise HTTPException(503, "Skill state unavailable; please retry") from exc


async def _set_enabled(request: Request, tenant_id: str, skill_id: str, enabled: bool) -> None:
    try:
        await set_skill_enabled(_state_db(request), tenant_id, skill_id, enabled)
    except SkillStateUnavailableError as exc:
        _log.warning("skill_state_write_failed tenant=%s: %s", tenant_id, exc)
        raise HTTPException(503, "Skill state unavailable; please retry") from exc


def _state_db(request: Request) -> Any:
    return getattr(request.app.state, "db_session_factory", None)


async def _executor(request: Request, tenant: Any) -> SkillExecutor:
    from app.api.llm_access import tenant_llm_provider

    return SkillExecutor(
        permission_checker=permission_checker,
        trigger_matcher=trigger_matcher,
        provider=await tenant_llm_provider(request, tenant),
        state_db_factory=_state_db(request),
    )


# Platform registry (builtins loaded at startup)
_platform_skills: dict[str, dict] = {}
# Tenant custom skills live in app.skills_runtime.tenant_store (Postgres; OPS-34).
_executions: dict[str, deque] = {}  # tenant_id → bounded deque of executions (maxlen=1000)
# Legacy per-process map — no longer read or written. Enable/disable state lives
# in app.skills_runtime.state_store (Postgres when wired; OPS-03).
_enabled_skills: dict[str, set] = {}
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


# ── Tenant custom skills: Postgres is the source of truth (OPS-34) ────────────


def _request_db_factory(request: Request) -> Any:
    """The request-path session factory (the NOBYPASSRLS application role).

    Deliberately no fallback that builds a second engine: a request path must
    use the app's own pool, and must never reach for the maintenance role.
    """
    return getattr(request.app.state, "db_session_factory", None)


def _store_unavailable(tenant_id: str, exc: Exception) -> HTTPException:
    _log.warning("skill_store_unavailable tenant=%s: %s", tenant_id, exc)
    return HTTPException(503, "Skill store unavailable; please retry")


async def _tenant_skill_list(request: Request, tenant_id: str) -> list[dict[str, Any]]:
    """This tenant's custom skills, read from Postgres on every call.

    No per-process cache: a skill created or edited on another replica is
    visible here immediately. An unreadable store is a 503, never "no skills".
    """
    try:
        return await tenant_store.list_tenant_skills(_request_db_factory(request), tenant_id)
    except tenant_store.SkillStoreUnavailableError as exc:
        raise _store_unavailable(tenant_id, exc) from exc


async def _tenant_skill(request: Request, tenant_id: str, skill_id: str) -> dict[str, Any] | None:
    try:
        return await tenant_store.get_tenant_skill(
            _request_db_factory(request), tenant_id, skill_id
        )
    except tenant_store.SkillStoreUnavailableError as exc:
        raise _store_unavailable(tenant_id, exc) from exc


async def _find_skill(request: Request, tenant_id: str, skill_id: str) -> dict[str, Any] | None:
    """A platform builtin or one of the tenant's custom skills."""
    return _platform_skills.get(skill_id) or await _tenant_skill(request, tenant_id, skill_id)


@router.get("")
async def list_skills(
    request: Request,
    include_platform: bool = Query(default=True),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """List available skills for the tenant."""
    tenant = _require_tenant(request)
    skills = []
    disabled = await _disabled_for(request, tenant.tenant_id)

    if include_platform:
        for s in _platform_skills.values():
            if status and s.get("status") != status:
                continue
            skills.append(
                {
                    **s,
                    "enabled": s["skill_id"] not in disabled,
                    "is_platform": True,
                }
            )

    # Tenant skills
    for s in await _tenant_skill_list(request, tenant.tenant_id):
        if status and s.get("status") != status:
            continue
        skills.append({**s, "enabled": s["skill_id"] not in disabled, "is_platform": False})

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

    skill_dict = await _find_skill(request, tenant.tenant_id, body.skill_id)
    if not skill_dict:
        raise HTTPException(404, f"Skill {body.skill_id!r} not found")

    result = await (await _executor(request, tenant)).execute(
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

    # Disabled skills are never candidates (OPS-03).
    disabled = await _disabled_for(request, tenant.tenant_id)
    skills = [
        _dict_to_skill_def(s) for s in _platform_skills.values() if s["skill_id"] not in disabled
    ]
    ranked = trigger_matcher.rank_skills(body.goal, skills)
    if not ranked:
        return {"matched": False}

    best_skill, score = ranked[0]
    result = await (await _executor(request, tenant)).execute(
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
    await _set_enabled(request, tenant.tenant_id, body.skill_id, True)
    return {"skill_id": body.skill_id, "status": "enabled"}


@router.post("/permissions/disable")
async def permission_disable_skill(
    request: Request,
    body: PermissionToggleRequest,
) -> dict[str, Any]:
    """Disable a skill for the current tenant via ScopedPermissionChecker."""
    tenant = _require_tenant(request)
    await _set_enabled(request, tenant.tenant_id, body.skill_id, False)
    return {"skill_id": body.skill_id, "status": "disabled"}


@router.get("/{skill_id}")
async def get_skill(request: Request, skill_id: str) -> dict[str, Any]:
    """Get a specific skill."""
    tenant = _require_tenant(request)

    skill = await _find_skill(request, tenant.tenant_id, skill_id)

    if not skill:
        raise HTTPException(404, f"Skill {skill_id} not found")

    disabled = await _disabled_for(request, tenant.tenant_id)
    return {**skill, "enabled": skill_id not in disabled}


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
    # Postgres is the source of truth (OPS-34): the 2xx is sent only after the
    # row every replica reads has committed; a failed write is a 503, never a
    # "created" that nothing persisted.
    try:
        await tenant_store.create_tenant_skill(_request_db_factory(request), skill)
    except tenant_store.SkillStoreUnavailableError as exc:
        raise _store_unavailable(tenant.tenant_id, exc) from exc

    return {"skill_id": skill_id, "status": "created"}


@router.post("/{skill_id}/enable")
async def enable_skill(request: Request, skill_id: str) -> dict[str, Any]:
    """Enable a platform skill for this tenant."""
    tenant = _require_tenant(request)

    if await _find_skill(request, tenant.tenant_id, skill_id) is None:
        raise HTTPException(404, f"Skill {skill_id} not found")

    await _set_enabled(request, tenant.tenant_id, skill_id, True)
    return {"skill_id": skill_id, "status": "enabled"}


@router.post("/{skill_id}/disable")
async def disable_skill(request: Request, skill_id: str) -> dict[str, Any]:
    """Disable a skill for this tenant."""
    tenant = _require_tenant(request)
    await _set_enabled(request, tenant.tenant_id, skill_id, False)
    return {"skill_id": skill_id, "status": "disabled"}


@router.post("/{skill_id}/execute")
async def execute_skill(
    request: Request,
    skill_id: str,
    body: ExecuteSkillRequest,
) -> dict[str, Any]:
    """Execute a skill against the given input."""
    tenant = _require_tenant(request)

    skill = await _find_skill(request, tenant.tenant_id, skill_id)

    if not skill:
        raise HTTPException(404, f"Skill {skill_id} not found")

    # OPS-03: this route never checked the tenant's disable state or the skill
    # permission scope. Fail closed (503) when the state cannot be read.
    try:
        allowed = await permission_checker.ais_allowed(
            _dict_to_skill_def(skill), tenant.tenant_id, db_factory=_state_db(request)
        )
    except SkillStateUnavailableError as exc:
        _log.warning("skill_state_read_failed tenant=%s: %s", tenant.tenant_id, exc)
        raise HTTPException(503, "Skill state unavailable; please retry") from exc
    if not allowed:
        raise HTTPException(403, f"Skill {skill_id} is disabled for this tenant")

    import time

    start = time.monotonic()
    execution_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.UTC).isoformat()

    # Execute using the tenant's LLM (BYOK) or the platform one
    from app.api.llm_access import tenant_llm_provider
    from app.providers.guarded_completion import DecisionBudgetExceededError

    provider = await tenant_llm_provider(request, tenant)
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
            from app.providers.guarded_completion import (
                complete_decision,
                generation_timeout_seconds,
            )

            resp = await complete_decision(
                provider,
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=1000,
                ),
                role="skill",
                tenant_ctx=tenant,
                timeout_seconds=generation_timeout_seconds(),
            )
            output = resp.content
            success = True
            model_used = resp.model
        except DecisionBudgetExceededError:
            raise  # 429 via the app's handler: a budget refusal is not an LLM outage
        except Exception as exc:
            error = str(exc)
    else:
        # No provider: nothing can execute the skill. This used to answer
        # success=True with a canned "[X Skill] Processing: ..." output.
        raise HTTPException(503, "No LLM provider is configured; the skill cannot be executed")

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
    tenant = _require_tenant(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, "Body must be a JSON object")

    async def _archive(_session: Any, previous: dict[str, Any]) -> None:
        old_version = {
            **previous,
            "archived_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        _skill_versions.setdefault(skill_id, []).append(old_version)

    # One locked read-modify-write in Postgres (OPS-34): the version bump is
    # atomic across replicas and a failed write is a 503, not "updated".
    try:
        skill = await tenant_store.update_tenant_skill(
            _request_db_factory(request), tenant.tenant_id, skill_id, body, archive=_archive
        )
    except tenant_store.SkillStoreUnavailableError as exc:
        raise _store_unavailable(tenant.tenant_id, exc) from exc
    if skill is None:
        raise HTTPException(404, "Skill not found")

    return {"skill_id": skill_id, "version": skill["version"], "status": "updated"}


@router.get("/{skill_id}/versions")
async def get_skill_versions(request: Request, skill_id: str) -> dict[str, Any]:
    """List version history for a skill."""
    tenant = _require_tenant(request)
    skill = await _find_skill(request, tenant.tenant_id, skill_id)
    if not skill:
        raise HTTPException(404, "Skill not found")

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
    disabled = await _disabled_for(request, tenant.tenant_id)

    matches = []
    for skill in _platform_skills.values():
        for hint in skill.get("trigger_hints", []):
            if hint.lower() in trigger or trigger in hint.lower():
                matches.append(
                    {
                        "skill_id": skill["skill_id"],
                        "name": skill["name"],
                        "matched_hint": hint,
                        "enabled": skill["skill_id"] not in disabled,
                    }
                )
                break

    for skill in await _tenant_skill_list(request, tenant.tenant_id):
        for hint in skill.get("trigger_hints", []):
            if hint.lower() in trigger or trigger in hint.lower():
                matches.append(
                    {
                        "skill_id": skill["skill_id"],
                        "name": skill["name"],
                        "matched_hint": hint,
                        "enabled": skill["skill_id"] not in disabled,
                    }
                )
                break

    return {"matches": matches, "trigger": trigger}
