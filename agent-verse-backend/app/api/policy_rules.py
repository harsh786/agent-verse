"""Policy-as-code CRUD — tenant-managed declarative rules."""
from __future__ import annotations
import json
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/governance/policy-rules", tags=["governance"])


class PolicyRuleUpsert(BaseModel):
    name: str
    description: str = ""
    rule_json: dict[str, Any]
    is_active: bool = True


def _req_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


@router.get("")
async def list_policy_rules(request: Request) -> list[dict[str, Any]]:
    tenant = _req_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        return []
    from sqlalchemy import text
    from app.db.rls import sqlalchemy_rls_context
    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        rows = (await session.execute(
            text("SELECT id, name, description, rule_json, is_active, version, created_at "
                 "FROM policy_rules WHERE tenant_id = :tid ORDER BY name"),
            {"tid": tenant.tenant_id},
        )).fetchall()
    return [{"id": r[0], "name": r[1], "description": r[2], "rule_json": r[3],
             "is_active": r[4], "version": r[5], "created_at": str(r[6])} for r in rows]


@router.post("", status_code=201)
async def create_policy_rule(body: PolicyRuleUpsert, request: Request) -> dict[str, Any]:
    tenant = _req_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        raise HTTPException(503, "Database unavailable")
    rule_id = uuid.uuid4().hex
    from sqlalchemy import text
    from app.db.rls import sqlalchemy_rls_context
    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        await session.execute(
            text("INSERT INTO policy_rules (id, tenant_id, name, description, rule_json, is_active) "
                 "VALUES (:id, :tid, :name, :desc, CAST(:rule AS json), :active)"),
            {"id": rule_id, "tid": tenant.tenant_id, "name": body.name,
             "desc": body.description, "rule": json.dumps(body.rule_json), "active": body.is_active},
        )
        await session.commit()
    return {"id": rule_id, "name": body.name, "status": "created"}


@router.delete("/{rule_id}", status_code=204)
async def delete_policy_rule(rule_id: str, request: Request) -> None:
    tenant = _req_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        raise HTTPException(503, "Database unavailable")
    from sqlalchemy import text
    from app.db.rls import sqlalchemy_rls_context
    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        await session.execute(
            text("DELETE FROM policy_rules WHERE id = :id AND tenant_id = :tid"),
            {"id": rule_id, "tid": tenant.tenant_id},
        )
        await session.commit()


@router.post("/evaluate")
async def evaluate_rules_dry_run(
    request: Request,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Dry-run a context against all active tenant rules."""
    tenant = _req_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        return {"allowed": True, "message": "No DB — permissive default"}
    from sqlalchemy import text
    from app.db.rls import sqlalchemy_rls_context
    from app.governance.policy_rules import evaluate_rules
    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        rows = (await session.execute(
            text("SELECT rule_json FROM policy_rules WHERE tenant_id = :tid AND is_active = true"),
            {"tid": tenant.tenant_id},
        )).fetchall()
    rules = [r[0] for r in rows if r[0]]
    result = evaluate_rules(rules, context)
    return {"allowed": result.allowed, "rule_name": result.rule_name, "message": result.message}
