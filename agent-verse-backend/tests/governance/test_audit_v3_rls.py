"""AuditV3.append must persist under the tenant's RLS context, never bypass it.

It used ``system_session`` (``SET LOCAL row_security = off``) for a single
tenant's INSERT on the request path: a privilege escalation, and under the API's
NOBYPASSRLS role every statement fails "query would be affected by row-level
security".
"""

from __future__ import annotations

from typing import Any

from app.governance.audit_v3 import AuditV3


class _Session:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, statement: Any, params: Any = None) -> None:
        self._log.append((str(statement), params))


async def test_append_persists_inside_the_tenant_rls_context() -> None:
    log: list[tuple[str, Any]] = []
    audit = AuditV3(db_factory=lambda: _Session(log))

    record = await audit.append(tenant_id="tenant-a", goal_id="g-1", action="tool_call")

    statements = [sql for sql, _ in log]
    assert not any("row_security" in sql for sql in statements), "RLS bypass on request path"
    assert "set_config('app.tenant_id'" in statements[0]
    assert log[0][1] == {"tid": "tenant-a"}
    insert = next(i for i, sql in enumerate(statements) if "INSERT INTO audit_events" in sql)
    assert insert > 0, "the INSERT must run after the tenant GUC is set"
    assert log[insert][1]["tid"] == "tenant-a"
    assert record.tenant_id == "tenant-a"
