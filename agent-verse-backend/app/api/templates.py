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


_BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    # DevOps
    {
        "name": "Deploy Service to Environment",
        "description": "Deploy any service to a target environment with a version tag.",
        "goal_text": "Deploy {{service}} to {{environment}} with version {{tag}}. Verify the deployment succeeds and all health checks pass.",
        "domain": "devops",
    },
    {
        "name": "Create Incident Report",
        "description": "Document a production incident with root cause and action items.",
        "goal_text": "Create an incident report for the {{service}} outage on {{date}}. Include root cause analysis, impact summary, and remediation steps.",
        "domain": "devops",
    },
    {
        "name": "Scale Kubernetes Deployment",
        "description": "Scale a Kubernetes deployment up or down.",
        "goal_text": "Scale the {{deployment}} deployment in the {{namespace}} namespace to {{replicas}} replicas. Confirm rollout completes without errors.",
        "domain": "devops",
    },
    # Engineering
    {
        "name": "Fix Bug and Open PR",
        "description": "Investigate a bug, apply a fix, and open a pull request.",
        "goal_text": "Fix the bug described in {{issue_id}} in the {{repository}} repository. Write a regression test, apply the fix, and open a pull request targeting {{branch}}.",
        "domain": "engineering",
    },
    {
        "name": "Code Review Summary",
        "description": "Summarize a pull request and flag issues.",
        "goal_text": "Review pull request {{pr_url}} in {{repository}}. Summarize the changes, highlight potential issues (security, performance, logic), and post a review comment.",
        "domain": "engineering",
    },
    {
        "name": "Generate API Documentation",
        "description": "Generate OpenAPI documentation for a service.",
        "goal_text": "Generate OpenAPI 3.0 documentation for the {{service_name}} service at {{base_url}}. Include all endpoints, request/response schemas, and authentication details. Save to {{output_path}}.",
        "domain": "engineering",
    },
    # Data
    {
        "name": "Run Data Pipeline",
        "description": "Execute a named data pipeline and report results.",
        "goal_text": "Run the {{pipeline_name}} data pipeline for date range {{start_date}} to {{end_date}}. Report row counts, validation errors, and total processing time.",
        "domain": "data",
    },
    {
        "name": "Generate Analytics Report",
        "description": "Pull metrics from a data source and format a report.",
        "goal_text": "Generate a {{report_type}} analytics report for {{metric_name}} from {{start_date}} to {{end_date}}. Include trend analysis, anomalies, and actionable insights.",
        "domain": "data",
    },
    # Marketing
    {
        "name": "Draft Marketing Campaign",
        "description": "Create a multi-channel marketing campaign brief.",
        "goal_text": "Draft a {{campaign_type}} marketing campaign for {{product_name}} targeting {{audience}}. Include email copy, social media posts, and a landing page headline. Tone: {{tone}}.",
        "domain": "marketing",
    },
    {
        "name": "Competitor Analysis",
        "description": "Research and compare competitors in a market.",
        "goal_text": "Analyze the top 5 competitors of {{company_name}} in the {{market}} market. Compare features, pricing, and positioning. Summarise key differentiators and opportunities.",
        "domain": "marketing",
    },
    # Sales
    {
        "name": "Lead Follow-up Email",
        "description": "Draft a personalised follow-up email for a sales lead.",
        "goal_text": "Write a follow-up email to {{lead_name}} at {{company}} about {{product_name}}. Reference our previous conversation on {{last_contact_date}}. Keep it under 150 words and include a clear CTA.",
        "domain": "sales",
    },
    {
        "name": "Sales Forecast Summary",
        "description": "Summarise pipeline data for a sales forecast.",
        "goal_text": "Summarise the {{team_name}} sales pipeline for Q{{quarter}} {{year}}. List top 10 deals by ARR, probability-weighted total, and forecast vs quota.",
        "domain": "sales",
    },
    # Support
    {
        "name": "Customer Support Triage",
        "description": "Triage and categorise a batch of support tickets.",
        "goal_text": "Triage the open support tickets in {{queue_name}} from {{start_date}} to {{end_date}}. Categorise by priority (P1-P4), assign to the correct team, and flag any SLA breaches.",
        "domain": "support",
    },
    # Legal
    {
        "name": "Contract Review Checklist",
        "description": "Review a contract document for key clauses and risks.",
        "goal_text": "Review the {{contract_type}} contract in {{document_url}}. Flag non-standard clauses, missing boilerplate, liability limits, and termination conditions. Output a risk matrix.",
        "domain": "legal",
    },
    # Finance
    {
        "name": "Expense Report Reconciliation",
        "description": "Reconcile expense reports against budget.",
        "goal_text": "Reconcile expense reports for {{department}} in {{month}} {{year}}. Flag expenses over {{threshold_usd}} USD, duplicate submissions, and missing receipts. Produce a summary CSV.",
        "domain": "finance",
    },
]


class _TemplateStore:
    """In-memory + optional DB store for goal templates."""

    def __init__(self, *, seed_builtins: bool = True) -> None:
        self._mem: dict[str, dict[str, Any]] = {}
        self._db: Any = None
        # Track which tenants have had built-ins seeded (in-memory mode only)
        self._seeded_tenants: set[str] = set()
        # Allow tests to opt out of seeding to preserve pre-existing assertions
        self._seed_builtins = seed_builtins

    def set_db(self, db_factory: Any) -> None:
        self._db = db_factory

    def _seed_builtins_for_tenant(self, tenant_id: str) -> None:
        """Seed read-only starter templates for a tenant (in-memory mode only)."""
        if not self._seed_builtins:
            return
        if tenant_id in self._seeded_tenants:
            return
        self._seeded_tenants.add(tenant_id)
        now = datetime.now(UTC)
        for tpl in _BUILTIN_TEMPLATES:
            t: dict[str, Any] = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}:{tpl['name']}")),
                "tenant_id": tenant_id,
                "name": tpl["name"],
                "description": tpl["description"],
                "goal_text": tpl["goal_text"],
                "domain": tpl["domain"],
                "parameters": _extract_parameters(tpl["goal_text"]),
                "use_count": 0,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
            self._mem[t["id"]] = t

    async def _seed_builtins_db(self, tenant_id: str) -> None:
        """Idempotently seed starter templates in DB mode (INSERT ... ON CONFLICT DO NOTHING)."""
        if not self._seed_builtins:
            return
        if tenant_id in self._seeded_tenants:
            return
        self._seeded_tenants.add(tenant_id)
        try:
            from sqlalchemy import text as _t
            now = datetime.now(UTC)
            async with self._db() as session:
                await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
                for tpl in _BUILTIN_TEMPLATES:
                    tpl_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}:{tpl['name']}"))
                    params = _extract_parameters(tpl["goal_text"])
                    import json
                    await session.execute(
                        _t("""
                            INSERT INTO goal_templates
                                (id, tenant_id, name, description, goal_text, domain, parameters,
                                 use_count, version, created_at, updated_at)
                            VALUES
                                (:id, :tenant_id, :name, :description, :goal_text, :domain,
                                 :parameters::jsonb, 0, 1, :now, :now)
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": tpl_id, "tenant_id": tenant_id, "name": tpl["name"],
                            "description": tpl["description"], "goal_text": tpl["goal_text"],
                            "domain": tpl["domain"], "parameters": json.dumps(params), "now": now,
                        },
                    )
                await session.commit()
        except Exception:
            pass  # Seeding is best-effort; templates can still be created manually

    async def list(self, tenant_id: str, domain: str | None = None) -> list[dict[str, Any]]:
        if self._db:
            # Seed built-ins on first request per tenant (idempotent via ON CONFLICT)
            await self._seed_builtins_db(tenant_id)
            return await self._list_db(tenant_id, domain)
        # In-memory mode: seed built-ins on first request per tenant
        self._seed_builtins_for_tenant(tenant_id)
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
