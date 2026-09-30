"""Skills CRUD API — create, list, update, delete custom skills."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent.skill_selector import PLATFORM_SKILLS, SkillSelector  # noqa: F401

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    trigger_hints: list[str]
    instructions: str
    allowed_tools: list[str] = []
    token_estimate: int = 100
    visibility: str = "tenant"  # tenant | marketplace


class SkillResponse(BaseModel):
    id: str
    name: str
    description: str
    trigger_hints: list[str]
    instructions: str
    allowed_tools: list[str]
    token_estimate: int
    visibility: str
    is_platform: bool = False


def _db_factory(request: Request) -> Any:
    app_state = request.app.state
    return getattr(app_state, "_db_session_factory", None) or getattr(
        app_state, "db_session_factory", None
    )


def _require_db(request: Request, what: str) -> Any:
    """Custom skills live only in Postgres: without it there is nothing to
    read or write, so answer 503 rather than a fabricated success."""
    db = _db_factory(request)
    if db is None:
        raise HTTPException(status_code=503, detail=f"{what} unavailable (no database configured)")
    return db


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return list(json.loads(value or "[]"))


def _row_to_skill(row: Any) -> dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2] or "",
        "trigger_hints": _json_list(row[3]),
        "instructions": row[4] or "",
        "allowed_tools": _json_list(row[5]),
        "token_estimate": row[6] or 100,
        "visibility": row[7] or "tenant",
        "is_platform": False,
    }


_SKILL_COLUMNS = (
    "id, name, description, trigger_hints, instructions, allowed_tools, token_estimate, visibility"
)


def _platform_skill_to_response(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": s.get("id", ""),
        "name": s.get("name", ""),
        "description": s.get("instructions", "")[:100],
        "trigger_hints": s.get("trigger_hints", []),
        "instructions": s.get("instructions", ""),
        "allowed_tools": s.get("allowed_tools", []),
        "token_estimate": s.get("token_estimate", 100),
        "visibility": "platform",
        "is_platform": True,
    }


@router.get("")
async def list_skills(request: Request) -> dict[str, Any]:
    """List all skills — platform defaults + tenant custom skills."""
    tenant_ctx = getattr(request.state, "tenant", None)

    skills: list[dict[str, Any]] = [_platform_skill_to_response(s) for s in PLATFORM_SKILLS]

    # Add tenant custom skills from DB if available
    app_state = request.app.state
    db_factory = getattr(app_state, "_db_session_factory", None) or getattr(
        app_state, "db_session_factory", None
    )

    if db_factory is not None and tenant_ctx is not None:
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with db_factory() as session, session.begin():  # noqa: SIM117
                async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                    result = await session.execute(
                        text(
                            f"SELECT {_SKILL_COLUMNS}"
                            " FROM skills WHERE is_active = true ORDER BY created_at DESC"
                        ),
                    )
                    skills.extend(_row_to_skill(row) for row in result.fetchall())
        except Exception as exc:
            # Was: swallow and answer platform skills only, so the tenant's
            # custom skills silently vanished during a DB outage.
            raise HTTPException(
                status_code=503, detail="Custom skills could not be loaded; retry"
            ) from exc

    return {"skills": skills, "total": len(skills)}


@router.post("")
async def create_skill(body: SkillCreateRequest, request: Request) -> dict[str, Any]:
    """Create a new custom skill for the tenant."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Check name uniqueness against platform skills
    existing_names = {s["name"] for s in PLATFORM_SKILLS}
    if body.name in existing_names:
        raise HTTPException(
            status_code=409,
            detail=f"Skill name '{body.name}' conflicts with a platform skill",
        )

    skill_id = uuid.uuid4().hex

    # Without a DB the skill would be stored nowhere; this used to answer
    # "created" anyway.
    db_factory = _require_db(request, "Skill store")
    if db_factory is not None:
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with db_factory() as session, session.begin():  # noqa: SIM117
                async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                    await session.execute(
                        text("""
                            INSERT INTO skills (
                                id, tenant_id, name, description, trigger_hints,
                                instructions, allowed_tools, token_estimate,
                                visibility, is_active, created_by
                            )
                            VALUES (
                                :id, :tid, :name, :desc, CAST(:hints AS jsonb),
                                :instructions, CAST(:tools AS jsonb), :tokens,
                                :vis, true, :creator
                            )
                        """),
                        {
                            "id": skill_id,
                            "tid": tenant_ctx.tenant_id,
                            "name": body.name,
                            "desc": body.description,
                            "hints": json.dumps(body.trigger_hints),
                            "instructions": body.instructions,
                            "tools": json.dumps(body.allowed_tools),
                            "tokens": body.token_estimate,
                            "vis": body.visibility,
                            "creator": tenant_ctx.api_key_id,
                        },
                    )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"Failed to save skill: {type(exc).__name__}"
            ) from exc

    return {
        "id": skill_id,
        "name": body.name,
        "description": body.description,
        "status": "created",
    }


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, request: Request) -> dict[str, Any]:
    """Delete a custom skill (cannot delete platform skills)."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Check it's not a platform skill
    platform_ids = {s.get("id") for s in PLATFORM_SKILLS}
    if skill_id in platform_ids:
        raise HTTPException(status_code=403, detail="Cannot delete platform skills")

    db_factory = _require_db(request, "Skill store")
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db_factory() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    text(
                        "UPDATE skills SET is_active = false"
                        " WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"id": skill_id, "tid": tenant_ctx.tenant_id},
                )
    except Exception as exc:
        # Was swallowed, answering "deleted" for a row that survived.
        raise HTTPException(status_code=503, detail="Skill could not be deleted; retry") from exc
    if not int(getattr(result, "rowcount", 0) or 0):
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

    return {"id": skill_id, "status": "deleted"}


class SkillUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    trigger_hints: list[str] | None = None
    instructions: str | None = None
    allowed_tools: list[str] | None = None


_UPDATABLE_SKILL_FIELDS = ("name", "description", "trigger_hints", "instructions", "allowed_tools")
_JSON_SKILL_FIELDS = {"trigger_hints", "allowed_tools"}


@router.put("/{skill_id}")
async def update_skill(skill_id: str, body: SkillUpdateRequest, request: Request) -> dict[str, Any]:
    """Edit one of the tenant's custom skills (only the fields sent change).

    403 for platform skills, 409 for a name that collides with a platform
    skill, 404 when the tenant has no such active skill, 503 without a DB or on
    a DB error.
    """
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if skill_id in {s.get("id") for s in PLATFORM_SKILLS}:
        raise HTTPException(status_code=403, detail="Cannot edit platform skills")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    if changes.get("name") in {s["name"] for s in PLATFORM_SKILLS}:
        raise HTTPException(
            status_code=409,
            detail=f"Skill name {changes['name']!r} conflicts with a platform skill",
        )
    db_factory = _require_db(request, "Skill store")

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    sets: list[str] = []
    params: dict[str, Any] = {"id": skill_id, "tid": tenant_ctx.tenant_id}
    for field in _UPDATABLE_SKILL_FIELDS:  # fixed column list — no user SQL
        if field not in changes:
            continue
        if field in _JSON_SKILL_FIELDS:
            sets.append(f"{field} = CAST(:{field} AS jsonb)")
            params[field] = json.dumps(changes[field])
        else:
            sets.append(f"{field} = :{field}")
            params[field] = changes[field]
    sql = (
        f"UPDATE skills SET {', '.join(sets)}, updated_at = NOW() "
        "WHERE id = :id AND tenant_id = :tid AND is_active = true "
        f"RETURNING {_SKILL_COLUMNS}"
    )
    try:
        async with db_factory() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                row = (await session.execute(text(sql), params)).fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Skill could not be updated; retry") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    return _row_to_skill(row)


class SkillTestRequest(BaseModel):
    input: str = Field(min_length=1, max_length=8000)


async def _find_skill(request: Request, tenant_ctx: Any, skill_id: str) -> dict[str, Any]:
    for s in PLATFORM_SKILLS:
        if s.get("id") == skill_id:
            return _platform_skill_to_response(s)
    db_factory = _require_db(request, "Skill store")
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db_factory() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                row = (
                    await session.execute(
                        text(
                            f"SELECT {_SKILL_COLUMNS} FROM skills "
                            "WHERE id = :id AND tenant_id = :tid AND is_active = true"
                        ),
                        {"id": skill_id, "tid": tenant_ctx.tenant_id},
                    )
                ).fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Skill could not be loaded; retry") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    return _row_to_skill(row)


@router.post("/{skill_id}/test")
async def run_skill_test(skill_id: str, body: SkillTestRequest, request: Request) -> dict[str, Any]:
    """Dry-run a skill: apply its instructions to ``input`` with the tenant's LLM.

    503 when no real LLM provider is configured (never canned output), 502
    when the provider call fails.
    """
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    skill = await _find_skill(request, tenant_ctx, skill_id)
    # app.state.llm_provider is None when only the FakeProvider is available.
    provider = getattr(request.app.state, "llm_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=503, detail="No LLM provider is configured; skills cannot be tested"
        )

    import time

    from app.providers.base import CompletionRequest, Message
    from app.providers.guarded_completion import DecisionBudgetExceededError

    started = time.monotonic()
    try:
        from app.providers.guarded_completion import (
            complete_decision,
            generation_timeout_seconds,
        )

        resp = await complete_decision(
            provider,
            CompletionRequest(
                messages=[
                    Message(role="system", content=skill["instructions"]),
                    Message(role="user", content=body.input),
                ],
                model="",
                max_tokens=1000,
            ),
            role="skill_test",
            tenant_ctx=tenant_ctx,
            timeout_seconds=generation_timeout_seconds(),
        )
    except DecisionBudgetExceededError:
        raise  # 429 via the app's handler: a budget refusal is not an LLM outage
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Skill test failed: {exc}") from exc
    return {
        "skill_id": skill_id,
        "skill_name": skill["name"],
        "output": resp.content,
        "model": getattr(resp, "model", None),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }
