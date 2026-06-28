"""Goal Template REST API — parameterized reusable goal patterns."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.tenancy.context import TenantContext

router = APIRouter(prefix="/templates", tags=["templates"])


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx


def _extract_parameters(goal_text: str) -> list[dict[str, Any]]:
    """Extract {{parameter_name}} placeholders from goal text."""
    names = re.findall(r"\{\{(\w+)\}\}", goal_text)
    seen: set[str] = set()
    params: list[dict[str, Any]] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            params.append({"name": name, "description": "", "required": True, "default": None})
    return params


def _instantiate_template(goal_text: str, params: dict[str, str]) -> str:
    """Replace {{param}} placeholders with provided values."""
    result = goal_text
    for key, value in params.items():
        result = result.replace(f"{{{{{key}}}}}", str(value))
    return result


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    goal_text: str = Field(..., min_length=1, max_length=10_000)
    domain: str = Field(default="general", max_length=100)
    parameters: list[dict[str, Any]] | None = None  # auto-extracted if omitted


class TemplateUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    goal_text: str = Field(..., min_length=1, max_length=10_000)
    domain: str = Field(default="general", max_length=100)
    parameters: list[dict[str, Any]] | None = None


class InstantiateRequest(BaseModel):
    parameters: dict[str, str] = Field(default_factory=dict)
    submit: bool = Field(default=False, description="If true, submit the instantiated goal immediately")
    agent_id: str | None = None
    priority: str = "normal"


class _TemplateStore:
    """In-memory + optional DB store for goal templates."""

    def __init__(self) -> None:
        self._mem: dict[str, dict[str, Any]] = {}
        self._db: Any = None

    def set_db(self, db_factory: Any) -> None:
        self._db = db_factory

    async def list(self, tenant_id: str, domain: str | None = None) -> list[dict[str, Any]]:
        if self._db:
            return await self._list_db(tenant_id, domain)
        rows = [t for t in self._mem.values() if t["tenant_id"] == tenant_id]
        if domain:
            rows = [t for t in rows if t["domain"] == domain]
        return sorted(rows, key=lambda t: t["created_at"], reverse=True)

    async def get(self, tenant_id: str, template_id: str) -> dict[str, Any] | None:
        if self._db:
            return await self._get_db(tenant_id, template_id)
        t = self._mem.get(template_id)
        return t if t and t["tenant_id"] == tenant_id else None

    async def create(self, tenant_id: str, name: str, description: str, goal_text: str,
                     domain: str, parameters: list[dict[str, Any]]) -> dict[str, Any]:
        if self._db:
            return await self._create_db(tenant_id, name, description, goal_text, domain, parameters)
        now = datetime.now(UTC)
        t: dict[str, Any] = {
            "id": str(uuid.uuid4()), "tenant_id": tenant_id, "name": name,
            "description": description, "goal_text": goal_text, "domain": domain,
            "parameters": parameters, "use_count": 0, "version": 1,
            "created_at": now, "updated_at": now,
        }
        self._mem[t["id"]] = t
        return t

    async def update(self, tenant_id: str, template_id: str, name: str, description: str,
                     goal_text: str, domain: str, parameters: list[dict[str, Any]]) -> dict[str, Any] | None:
        if self._db:
            return await self._update_db(tenant_id, template_id, name, description, goal_text, domain, parameters)
        t = self._mem.get(template_id)
        if not t or t["tenant_id"] != tenant_id:
            return None
        t.update(name=name, description=description, goal_text=goal_text,
                 domain=domain, parameters=parameters, version=t["version"] + 1,
                 updated_at=datetime.now(UTC))
        return t

    async def delete(self, tenant_id: str, template_id: str) -> bool:
        if self._db:
            return await self._delete_db(tenant_id, template_id)
        t = self._mem.get(template_id)
        if not t or t["tenant_id"] != tenant_id:
            return False
        del self._mem[template_id]
        return True

    async def increment_use_count(self, tenant_id: str, template_id: str) -> None:
        if self._db:
            try:
                from sqlalchemy import text as _t
                async with self._db() as session:
                    await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
                    await session.execute(
                        _t("UPDATE goal_templates SET use_count = use_count + 1 WHERE id = :id"),
                        {"id": template_id},
                    )
                    await session.commit()
            except Exception:
                pass
            return
        if template_id in self._mem:
            self._mem[template_id]["use_count"] = self._mem[template_id].get("use_count", 0) + 1

    # DB implementations
    async def _list_db(self, tenant_id: str, domain: str | None) -> list[dict[str, Any]]:
        from sqlalchemy import select
        from sqlalchemy import text as _t

        from app.db.models.template import GoalTemplate
        async with self._db() as session:
            await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            q = select(GoalTemplate).where(GoalTemplate.tenant_id == tenant_id)
            if domain:
                q = q.where(GoalTemplate.domain == domain)
            q = q.order_by(GoalTemplate.created_at.desc())
            return [self._orm_to_dict(r) for r in (await session.execute(q)).scalars().all()]

    async def _get_db(self, tenant_id: str, template_id: str) -> dict[str, Any] | None:
        from sqlalchemy import select
        from sqlalchemy import text as _t

        from app.db.models.template import GoalTemplate
        async with self._db() as session:
            await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            row = (await session.execute(
                select(GoalTemplate).where(GoalTemplate.id == template_id, GoalTemplate.tenant_id == tenant_id)
            )).scalar_one_or_none()
            return self._orm_to_dict(row) if row else None

    async def _create_db(self, tenant_id: str, name: str, description: str, goal_text: str,
                         domain: str, parameters: list[dict[str, Any]]) -> dict[str, Any]:
        from sqlalchemy import text as _t

        from app.db.models.template import GoalTemplate
        now = datetime.now(UTC)
        async with self._db() as session:
            await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            obj = GoalTemplate(id=str(uuid.uuid4()), tenant_id=tenant_id, name=name,
                               description=description, goal_text=goal_text, domain=domain,
                               parameters=parameters, use_count=0, version=1, created_at=now, updated_at=now)
            session.add(obj)
            await session.commit()
            await session.refresh(obj)
            return self._orm_to_dict(obj)

    async def _update_db(self, tenant_id: str, template_id: str, name: str, description: str,
                         goal_text: str, domain: str, parameters: list[dict[str, Any]]) -> dict[str, Any] | None:
        from sqlalchemy import select
        from sqlalchemy import text as _t

        from app.db.models.template import GoalTemplate
        async with self._db() as session:
            await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            obj = (await session.execute(
                select(GoalTemplate).where(GoalTemplate.id == template_id, GoalTemplate.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if obj is None:
                return None
            obj.name = name
            obj.description = description
            obj.goal_text = goal_text
            obj.domain = domain
            obj.parameters = parameters
            obj.version += 1
            obj.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(obj)
            return self._orm_to_dict(obj)

    async def _delete_db(self, tenant_id: str, template_id: str) -> bool:
        from sqlalchemy import select
        from sqlalchemy import text as _t

        from app.db.models.template import GoalTemplate
        async with self._db() as session:
            await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            obj = (await session.execute(
                select(GoalTemplate).where(GoalTemplate.id == template_id, GoalTemplate.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if obj is None:
                return False
            await session.delete(obj)
            await session.commit()
            return True

    @staticmethod
    def _orm_to_dict(obj: Any) -> dict[str, Any]:
        return {
            "id": obj.id, "tenant_id": obj.tenant_id, "name": obj.name,
            "description": obj.description, "goal_text": obj.goal_text,
            "domain": obj.domain, "parameters": obj.parameters or [],
            "use_count": obj.use_count, "version": obj.version,
            "created_at": obj.created_at.isoformat() if isinstance(obj.created_at, datetime) else str(obj.created_at),
            "updated_at": obj.updated_at.isoformat() if isinstance(obj.updated_at, datetime) else str(obj.updated_at),
        }


# Module-level store (wired with DB in lifespan)
template_store = _TemplateStore()


@router.get("")
async def list_templates(
    request: Request,
    domain: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    tenant = _require_tenant(request)
    return await template_store.list(tenant.tenant_id, domain)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(request: Request, body: TemplateCreate) -> dict[str, Any]:
    tenant = _require_tenant(request)
    parameters = body.parameters if body.parameters is not None else _extract_parameters(body.goal_text)
    return await template_store.create(
        tenant_id=tenant.tenant_id, name=body.name, description=body.description,
        goal_text=body.goal_text, domain=body.domain, parameters=parameters,
    )


@router.get("/{template_id}")
async def get_template(template_id: str, request: Request) -> dict[str, Any]:
    tenant = _require_tenant(request)
    t = await template_store.get(tenant.tenant_id, template_id)
    if t is None:
        raise HTTPException(404, "Template not found")
    return t


@router.put("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_template(template_id: str, request: Request, body: TemplateUpdate) -> None:
    tenant = _require_tenant(request)
    parameters = body.parameters if body.parameters is not None else _extract_parameters(body.goal_text)
    result = await template_store.update(
        tenant_id=tenant.tenant_id, template_id=template_id, name=body.name,
        description=body.description, goal_text=body.goal_text, domain=body.domain,
        parameters=parameters,
    )
    if result is None:
        raise HTTPException(404, "Template not found")


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, request: Request) -> None:
    tenant = _require_tenant(request)
    if not await template_store.delete(tenant.tenant_id, template_id):
        raise HTTPException(404, "Template not found")


@router.post("/{template_id}/instantiate")
async def instantiate_template(
    template_id: str, request: Request, body: InstantiateRequest
) -> dict[str, Any]:
    """Fill template parameters and optionally submit as a goal."""
    tenant = _require_tenant(request)
    t = await template_store.get(tenant.tenant_id, template_id)
    if t is None:
        raise HTTPException(404, "Template not found")

    # Check required parameters
    missing = [
        p["name"] for p in (t.get("parameters") or [])
        if p.get("required", True) and p["name"] not in body.parameters and not p.get("default")
    ]
    if missing:
        raise HTTPException(422, f"Missing required parameters: {', '.join(missing)}")

    # Fill defaults for missing optional params
    params = {**{p["name"]: p.get("default", "") for p in (t.get("parameters") or []) if p.get("default")},
              **body.parameters}
    instantiated_goal = _instantiate_template(t["goal_text"], params)

    # Track usage
    await template_store.increment_use_count(tenant.tenant_id, template_id)

    result: dict[str, Any] = {
        "template_id": template_id,
        "instantiated_goal": instantiated_goal,
        "parameters_used": params,
    }

    if body.submit:
        goal_svc = getattr(request.app.state, "goal_service", None)
        if goal_svc is None:
            raise HTTPException(503, "Goal service not available")
        submitted = await goal_svc.submit_goal(
            goal=instantiated_goal,
            tenant_ctx=tenant,
            agent_id=body.agent_id,
            priority=body.priority,
        )
        result["submitted_goal"] = submitted

    return result
