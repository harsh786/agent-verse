"""Regression: SCIM ``GET /Users?filter=`` is applied, not ignored.

The filter was dropped (the route did not even accept it), so an IdP's
``userName eq "alice@corp"`` pre-provisioning lookup got EVERY user back and
linked / updated the wrong account. Unsupported filters are now a 400
``invalidFilter`` rather than "all users".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.auth.scim_handler import SCIMHandler, _parse_scim_eq_filter


class _Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.statements.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar_one.return_value = 0
        return result


def _compiled(stmt: Any) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_username_filter_narrows_the_query_case_insensitively() -> None:
    session = _Session()
    handler = SCIMHandler(tenant_id="t1", config={}, db_factory=lambda: session)
    await handler.list_users(filter_str='userName eq "Alice@Corp.com"')
    selects = [_compiled(s) for s in session.statements if "FROM users" in _compiled(s)]
    assert selects and all("lower(users.email) = 'alice@corp.com'" in sql for sql in selects)


@pytest.mark.asyncio
async def test_unsupported_filter_is_400_before_any_query() -> None:
    session = _Session()
    handler = SCIMHandler(tenant_id="t1", config={}, db_factory=lambda: session)
    with pytest.raises(HTTPException) as exc:
        await handler.list_users(filter_str='userName sw "a"')
    assert exc.value.status_code == 400
    assert exc.value.detail["scimType"] == "invalidFilter"
    assert session.statements == []


def test_parser_forms() -> None:
    assert _parse_scim_eq_filter('id eq "u-1"') == ("id", "u-1")
    assert _parse_scim_eq_filter('emails.value eq "x@y.z"') == ("userName", "x@y.z")
    assert _parse_scim_eq_filter('emails[type eq "work"].value eq "x@y.z"') == (
        "userName",
        "x@y.z",
    )
