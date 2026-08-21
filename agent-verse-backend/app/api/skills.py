"""Skills CRUD API — create, list, update, delete custom skills."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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
                            "SELECT id, name, description, trigger_hints, instructions,"
                            " allowed_tools, token_estimate, visibility"
                            " FROM skills WHERE is_active = true ORDER BY created_at DESC"
                        ),
                    )
                    for row in result.fetchall():
                        skills.append(
                            {
                                "id": row[0],
                                "name": row[1],
                                "description": row[2] or "",
                                "trigger_hints": (
                                    row[3]
                                    if isinstance(row[3], list)
                                    else json.loads(row[3] or "[]")
                                ),
                                "instructions": row[4] or "",
                                "allowed_tools": (
                                    row[5]
                                    if isinstance(row[5], list)
                                    else json.loads(row[5] or "[]")
                                ),
                                "token_estimate": row[6] or 100,
                                "visibility": row[7] or "tenant",
                                "is_platform": False,
                            }
                        )
        except Exception:
            pass  # DB unavailable — return platform skills only

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

    # Persist to DB if available
    app_state = request.app.state
    db_factory = getattr(app_state, "_db_session_factory", None) or getattr(
        app_state, "db_session_factory", None
    )

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

    app_state = request.app.state
    db_factory = getattr(app_state, "_db_session_factory", None) or getattr(
        app_state, "db_session_factory", None
    )

    if db_factory is not None:
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with db_factory() as session, session.begin():  # noqa: SIM117
                async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                    await session.execute(
                        text(
                            "UPDATE skills SET is_active = false"
                            " WHERE id = :id AND tenant_id = :tid"
                        ),
                        {"id": skill_id, "tid": tenant_ctx.tenant_id},
                    )
        except Exception:
            pass

    return {"id": skill_id, "status": "deleted"}
