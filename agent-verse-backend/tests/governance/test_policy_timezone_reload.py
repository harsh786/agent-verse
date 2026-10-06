"""a03-F060-03: a policy's timezone survives the version snapshot and every reload.

``Policy.timezone`` existed and the engine evaluated windows in it, but the API
had no field for it and the strict reload rebuilt policies from the snapshot's
(hours, weekdays) only, so every other replica (and this one after a restart)
read a "New York business hours" window in UTC.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.api.governance import CreatePolicyRequest, _policy_rules
from app.governance.policies import PolicyEngine, PolicyResult, timezone_from_rules
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


def _local_hour(tz: str) -> int:
    return datetime.now(ZoneInfo(tz)).hour


@pytest.mark.asyncio
async def test_strict_reload_keeps_the_policy_timezone() -> None:
    tz = "Pacific/Kiritimati"  # UTC+14: its local hour is never the UTC hour
    rules = [
        {
            "tools_pattern": "deploy*",
            "action": "deny",
            "allowed_hours_utc": [_local_hour(tz)],
            "allowed_hours_format": "hours",
            "timezone": tz,
        }
    ]
    session = _Session(
        {
            "FROM governance_policies": [("local-hours", "deny", "deploy*", "t1", 0)],
            "FROM policy_versions": [("local-hours", json.dumps(rules))],
        }
    )
    engine = PolicyEngine()
    await engine.reload_from_db(lambda: session, tenant_id="t1", strict=True)
    (policy,) = engine._policies
    assert policy.timezone == tz
    # Active now in its own timezone (it would be inactive read as UTC).
    assert engine.evaluate("deploy_prod", tenant_ctx=CTX) == PolicyResult.DENY


def test_legacy_snapshot_without_timezone_is_utc() -> None:
    assert timezone_from_rules([{"allowed_hours_utc": [1, 2]}]) == "UTC"
    assert timezone_from_rules(None) == "UTC"
    assert timezone_from_rules([{"timezone": "Not/AZone"}]) == "UTC"


def test_api_accepts_validates_and_snapshots_the_timezone() -> None:
    body = CreatePolicyRequest(
        name="p", tools_pattern="deploy*", allowed_hours_utc=[9], timezone="America/New_York"
    )
    assert body.timezone == "America/New_York"
    rules = _policy_rules({"tools_pattern": "deploy*", "timezone": body.timezone})
    assert rules[0]["timezone"] == "America/New_York"
    assert CreatePolicyRequest(name="p", tools_pattern="x").timezone == "UTC"
    with pytest.raises(ValidationError):
        CreatePolicyRequest(name="p", tools_pattern="x", timezone="Mars/Olympus")
