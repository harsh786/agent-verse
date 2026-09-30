"""TRG-06: a tenant's plan comes from the tenant record, never from a guess."""

from __future__ import annotations

from typing import Any

from app.tenancy.context import PlanTier
from app.tenancy.plan_resolver import resolve_tenant_plan


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _Session:
    def __init__(self, plans: dict[str, str], fail: bool) -> None:
        self._plans = plans
        self._fail = fail
        self.queries: list[dict[str, Any]] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def execute(self, _stmt: Any, params: dict[str, Any]) -> _Result:
        if self._fail:
            raise RuntimeError("db down")
        self.queries.append(params)
        return _Result(self._plans.get(params["t"]))


def plan_db(plans: dict[str, str], *, fail: bool = False) -> Any:
    """A session factory whose ``tenants`` table holds *plans*."""

    def factory() -> _Session:
        return _Session(plans, fail)

    return factory


class _TenantService:
    def __init__(self, plans: dict[str, str]) -> None:
        self._plans = plans

    async def get_tenant(self, tenant_id: str) -> dict[str, Any]:
        if tenant_id not in self._plans:
            raise LookupError(tenant_id)
        return {"tenant_id": tenant_id, "plan": self._plans[tenant_id]}


async def test_reads_plan_from_tenant_table() -> None:
    plan = await resolve_tenant_plan("t-ent", db_factory=plan_db({"t-ent": "enterprise"}))
    assert plan is PlanTier.ENTERPRISE


async def test_tenant_service_answer_wins() -> None:
    plan = await resolve_tenant_plan(
        "t-1",
        tenant_service=_TenantService({"t-1": "starter"}),
        db_factory=plan_db({"t-1": "enterprise"}),
    )
    assert plan is PlanTier.STARTER


async def test_falls_through_to_db_when_service_cannot_answer() -> None:
    plan = await resolve_tenant_plan(
        "t-2",
        tenant_service=_TenantService({}),
        db_factory=plan_db({"t-2": "professional"}),
    )
    assert plan is PlanTier.PROFESSIONAL


async def test_unresolvable_plan_is_the_most_restrictive_tier() -> None:
    assert await resolve_tenant_plan("t-3", db_factory=plan_db({}, fail=True)) is PlanTier.FREE
    assert await resolve_tenant_plan("t-3", db_factory=plan_db({"t-3": "gold"})) is PlanTier.FREE
    assert await resolve_tenant_plan("t-3") is PlanTier.FREE
    assert await resolve_tenant_plan("") is PlanTier.FREE
