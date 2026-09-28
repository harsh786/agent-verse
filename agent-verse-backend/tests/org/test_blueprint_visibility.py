"""OrgService blueprint reads are limited to global + the caller's own blueprints.

``org_blueprints.tenant_id`` is NULL for a global template and a tenant UUID for
a private one. ``list_blueprints`` / ``get_blueprint`` / ``get_blueprint_by_slug``
selected from the table with no tenant predicate at all, so they returned every
tenant's private blueprints. RLS now enforces the rule at the database; these
tests pin the explicit app-side predicate (defense in depth).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.org.service import OrgService


class _Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> Any:
        self.statements.append(stmt)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect())).replace("\n", " ")


TENANT = str(uuid.uuid4())


@pytest.mark.parametrize(
    "call",
    [
        lambda svc: svc.list_blueprints(),
        lambda svc: svc.list_blueprints(domain="tech"),
        lambda svc: svc.get_blueprint(str(uuid.uuid4())),
        lambda svc: svc.get_blueprint_by_slug("starter"),
    ],
    ids=["list", "list-domain", "by-id", "by-slug"],
)
@pytest.mark.asyncio
async def test_blueprint_reads_filter_to_global_or_own(call: Any) -> None:
    session = _Session()
    svc = OrgService(session=session, tenant_id=TENANT)  # type: ignore[arg-type]
    await call(svc)
    (stmt,) = session.statements
    sql = _sql(stmt)
    assert "org_blueprints.tenant_id IS NULL OR org_blueprints.tenant_id = " in sql, sql
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert TENANT in params.values()
