"""P4-2 against real Postgres: a dashed-UUID tenant id never overflows an audit column.

Live TRIGGER-CHAIN: a resumed workflow run carried the tenant id as a dashed
36-char UUID; ``audit_log.tenant_id`` was ``VARCHAR(32)``, the governed code
step's audit insert raised ``StringDataRightTruncationError`` and the step
failed "execution could not be audited".

Runs as a least-privilege (NOSUPERUSER, NOBYPASSRLS) role so RLS binds.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest

from app.governance.audit import (
    AuditEvent,
    AuditFieldTooLongError,
    AuditLog,
    AuditPersistenceError,
)
from app.governance.permissions import ActionLevel
from app.tenancy.context import PlanTier, TenantContext
from app.tools.code_interpreter import CodeResult

pytestmark = pytest.mark.integration

_WIDENED = (
    "audit_log",
    "goal_events",
    "event_inbox",
    "coordination_events",
    "coordination_outbox",
    "coordination_dead_letters",
)


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _exec(url: str, *stmts: str) -> None:
    conn = await asyncpg.connect(_plain(url))
    try:
        for stmt in stmts:
            await conn.execute(stmt)
    finally:
        await conn.close()


@pytest.fixture
def app_role_url(pg_url: str) -> Iterator[str]:
    role = f"app_wide_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(16)
    asyncio.run(
        _exec(
            pg_url,
            f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS",
            f"GRANT SELECT, INSERT ON audit_log TO {role}",
        )
    )
    head, tail = pg_url.split("://", 1)
    yield f"{head}://{role}:{password}@{tail.split('@', 1)[1]}"
    asyncio.run(_exec(pg_url, f"REVOKE ALL ON audit_log FROM {role}", f"DROP ROLE {role}"))


def _factory(url: str) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    return async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)


async def _rows(url: str, tenant: str) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(_plain(url))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant)
            recs = await conn.fetch("SELECT tenant_id, goal_id, tool_name FROM audit_log")
    finally:
        await conn.close()
    return [dict(r) for r in recs]


def test_audit_and_event_id_columns_are_at_least_uuid_wide(pg_url: str) -> None:
    async def _check() -> tuple[list[Any], list[Any]]:
        conn = await asyncpg.connect(_plain(pg_url))
        try:
            narrow = await conn.fetch(
                "SELECT c.table_name, c.column_name, c.character_maximum_length "
                "FROM information_schema.columns c "
                "JOIN pg_class k ON k.relname = c.table_name "
                "LEFT JOIN pg_inherits i ON i.inhrelid = k.oid "
                "LEFT JOIN pg_class p ON p.oid = i.inhparent "
                "WHERE c.table_schema = 'public' AND c.column_name IN ('id', 'tenant_id') "
                "AND c.data_type = 'character varying' AND c.character_maximum_length < 36 "
                "AND (c.table_name = ANY($1::text[]) OR p.relname = ANY($1::text[]))",
                list(_WIDENED),
            )
            policies = await conn.fetch(
                "SELECT tablename FROM pg_policies WHERE tablename = ANY($1::text[])",
                list(_WIDENED),
            )
        finally:
            await conn.close()
        return list(narrow), list(policies)

    narrow, policies = asyncio.run(_check())
    assert narrow == []
    # The RLS policies dropped around the ALTER are back on every table.
    assert {r["tablename"] for r in policies} == set(_WIDENED)


def test_dashed_uuid_tenant_audit_row_is_stored_whole(app_role_url: str) -> None:
    tenant = str(uuid.uuid4())  # 36 chars, dashed
    log = AuditLog(db_session_factory=_factory(app_role_url))
    event = AuditEvent(
        goal_id=str(uuid.uuid4()),
        tool_name="code_interpreter.python",
        action_level=ActionLevel.ALLOW_LOG,
        outcome="success",
    )
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.ENTERPRISE, api_key_id="k")
    asyncio.run(log.record_durable(event, tenant_ctx=ctx))
    rows = asyncio.run(_rows(app_role_url, tenant))
    assert [(r["tenant_id"], r["goal_id"]) for r in rows] == [(tenant, event.goal_id)]


def test_overflowing_tenant_is_refused_loudly_not_truncated(app_role_url: str) -> None:
    tenant = "t" * 65
    log = AuditLog(db_session_factory=_factory(app_role_url), retry_base_delay=0)
    event = AuditEvent(
        goal_id="g", tool_name="x", action_level=ActionLevel.ALLOW_LOG, outcome="success"
    )
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.FREE, api_key_id="k")
    with pytest.raises(AuditPersistenceError) as info:
        asyncio.run(log.record_durable(event, tenant_ctx=ctx))
    assert isinstance(info.value.__cause__, AuditFieldTooLongError)
    assert "audit_log.tenant_id" in str(info.value)
    assert asyncio.run(_rows(app_role_url, tenant[:32])) == []
    assert asyncio.run(_rows(app_role_url, tenant)) == []


class _Interp:
    def __init__(self, **_kw: Any) -> None:
        pass

    async def execute(self, **_kw: Any) -> CodeResult:
        from app.workflow.steps.code_step import _OUTPUT_MARKER

        return CodeResult(stdout=f"{_OUTPUT_MARKER}42\n", stderr="", exit_code=0)


def test_resumed_run_code_step_with_dashed_tenant_is_audited(
    app_role_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact live failure: the code step of a run whose state carries the
    dashed tenant id commits its audit row (under the tenant's hex id, next to
    the engine's own workflow audit rows) instead of failing the step."""
    from app.workflow.steps.code_step import _run_in_code_sandbox

    monkeypatch.setattr("app.tools.code_execution.CodeInterpreter", _Interp)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(
        "app.tools.code_execution._default_audit_log",
        AuditLog(db_session_factory=_factory(app_role_url)),
    )
    tenant = uuid.uuid4()
    run_id = str(uuid.uuid4())
    out = asyncio.run(
        _run_in_code_sandbox(
            "book_dispatch", "python", "output = 42", {}, tenant_id=str(tenant), run_id=run_id
        )
    )
    assert out == 42
    rows = asyncio.run(_rows(app_role_url, tenant.hex))
    assert [(r["tenant_id"], r["goal_id"], r["tool_name"]) for r in rows] == [
        (tenant.hex, run_id, "code_interpreter.python")
    ]
