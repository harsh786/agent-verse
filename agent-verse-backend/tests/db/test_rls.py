from __future__ import annotations

from typing import Any

from app.db.rls import sqlalchemy_rls_context


async def test_sqlalchemy_rls_context_uses_parameterizable_set_config() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    class _Session:
        async def execute(self, statement: Any, params: dict[str, str] | None = None) -> None:
            calls.append((str(statement), params))

    async with sqlalchemy_rls_context(_Session(), "tenant-1"):
        pass

    assert calls == [
        ("SELECT set_config('app.tenant_id', :tid, true)", {"tid": "tenant-1"}),
        ("SELECT set_config('app.tenant_id', '', true)", None),
    ]
