"""Regression tests: policy reload keeps time windows and never reloads as allow-all.

1. ``allowed_hours_utc`` / ``allowed_weekdays`` were never persisted to
   ``governance_policies``: every reload (other replicas, restarts) enforced a
   time-windowed policy around the clock.
2. The non-strict reload defaulted a missing pattern to ``".*"``, which under
   fnmatch matches only names starting with "." — a deny-all policy reloaded as
   deny-nothing.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.governance.policies import PolicyEngine, PolicyResult
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


class _Session:
    def __init__(self, by_sql: dict[str, Any]) -> None:
        self._by_sql = by_sql

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        result = MagicMock()
        rows: list[Any] = []
        for marker, value in self._by_sql.items():
            if marker in sql:
                rows = value
        result.fetchall.return_value = rows
        result.scalar_one_or_none.return_value = None
        return result


@pytest.mark.asyncio
async def test_strict_reload_restores_the_policy_time_window() -> None:
    rules = [
        {
            "tools_pattern": "deploy*",
            "action": "deny",
            "allowed_hours_utc": [9, 17],
            "allowed_weekdays": [0, 1, 2, 3, 4],
        }
    ]
    session = _Session(
        {
            "FROM governance_policies": [("no-deploys-9-5", "deny", "deploy*", "t1")],
            "FROM policy_versions": [("no-deploys-9-5", json.dumps(rules))],
        }
    )
    engine = PolicyEngine()
    await engine.reload_from_db(lambda: session, tenant_id="t1", strict=True)
    (policy,) = engine._policies
    assert policy.allowed_hours_utc == (9, 17)
    assert policy.allowed_weekdays == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_non_strict_reload_default_pattern_is_a_real_wildcard() -> None:
    session = _Session({"FROM governance_policies": [("deny-all", "deny", None, "t1")]})
    engine = PolicyEngine()
    await engine.reload_from_db(lambda: session)
    assert engine.evaluate("github_create_issue", tenant_ctx=CTX) == PolicyResult.DENY
