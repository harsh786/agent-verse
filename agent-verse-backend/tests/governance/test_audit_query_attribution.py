"""Regression: audit_log's SOC2 attribution columns are read back by query_db.

``record`` wrote ip_address / user_agent / api_key_id / request_id /
connector_id, but ``query_db`` never selected them, so every read (the audit
API, exports) dropped who/where a call came from.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


class _Session:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.sql.append(str(stmt))
        result = MagicMock()
        result.fetchall.return_value = [
            (
                "e1",
                "g1",
                "jira_create",
                "allow",
                "success",
                "s1",
                None,
                "",
                datetime.now(UTC),
                "203.0.113.5",
                "curl/8",
                "key-9",
                "req-1",
                "conn-2",
            )
        ]
        return result


@pytest.mark.asyncio
async def test_query_db_returns_attribution_columns() -> None:
    session = _Session()
    audit = AuditLog(db_session_factory=lambda: session)
    (event,) = await audit.query_db(tenant_ctx=CTX)
    assert any("ip_address" in sql and "FROM audit_log" in sql for sql in session.sql)
    assert (event.ip_address, event.user_agent, event.api_key_id) == (
        "203.0.113.5",
        "curl/8",
        "key-9",
    )
    assert (event.request_id, event.connector_id) == ("req-1", "conn-2")
