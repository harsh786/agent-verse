"""QA-8: a policy's time window is the set of hours it is active in.

The UI sends ``allowed_hours_utc`` as one entry per selected hour (``[3, 4, 5]``)
but the backend only built a window from exactly two values (a ``[start, end]``
pair) and dropped anything else, so the policy applied around the clock. Hours
are now an explicit set (0-23) end to end: validated on create, persisted in the
version snapshot with a format marker, reloaded as a set, and returned by
``GET /governance/policies`` (with the weekdays). A legacy snapshot (no marker)
holding an ascending pair keeps its old ``[start, end)`` meaning; any other
legacy list is read as a set of hours.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.governance import router
from app.governance.policies import (
    HOURS_FORMAT,
    Policy,
    PolicyEngine,
    PolicyResult,
    hours_list,
    parse_hours_window,
)
from app.tenancy.context import PlanTier, TenantContext
from tests.governance._router_app import make_app, tenant
from tests.governance.test_policy_reload_time_windows import _Session
from tests.governance.test_policy_versions_wired import _app, _DB, _Res

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


def _hour() -> int:
    return datetime.now(UTC).hour


def _other_hours(n: int = 3) -> list[int]:
    """Non-contiguous hours that exclude the current one (and its neighbours)."""
    now = _hour()
    return [(now + 3 + 4 * i) % 24 for i in range(n)]


# ── engine ────────────────────────────────────────────────────────────────────


def test_policy_applies_only_inside_its_hour_set() -> None:
    inside = PolicyEngine(
        [
            Policy(
                name="p", denied_tools=["x"], tenant_id="t1", allowed_hours_utc=frozenset({_hour()})
            )
        ]
    )
    outside = PolicyEngine(
        [
            Policy(
                name="p",
                denied_tools=["x"],
                tenant_id="t1",
                allowed_hours_utc=frozenset(_other_hours()),
            )
        ]
    )
    assert inside.evaluate("x", tenant_ctx=CTX) == PolicyResult.DENY
    assert outside.evaluate("x", tenant_ctx=CTX) == PolicyResult.ALLOW


def test_legacy_range_tuple_keeps_its_meaning() -> None:
    now = _hour()
    engine = PolicyEngine(
        [Policy(name="p", denied_tools=["x"], tenant_id="t1", allowed_hours_utc=(now, now + 1))]
    )
    assert engine.evaluate("x", tenant_ctx=CTX) == PolicyResult.DENY


@pytest.mark.parametrize(
    ("raw", "fmt", "expected"),
    [
        ([3, 4, 5], HOURS_FORMAT, frozenset({3, 4, 5})),
        ([9, 17], HOURS_FORMAT, frozenset({9, 17})),
        ([9, 17], None, (9, 17)),  # legacy [start, end) pair
        ([3, 4, 5], None, frozenset({3, 4, 5})),  # legacy UI hour list
        ([17, 9], None, frozenset({9, 17})),  # not an ascending pair → hours
        ([], HOURS_FORMAT, None),
        (None, None, None),
        ("9-17", None, None),
        ([99], HOURS_FORMAT, None),  # nothing valid → no window (enforced always)
    ],
)
def test_parse_hours_window(raw: Any, fmt: Any, expected: Any) -> None:
    assert parse_hours_window(raw, fmt=fmt) == expected


def test_hours_list_expands_a_legacy_range() -> None:
    assert hours_list((9, 12)) == [9, 10, 11]
    assert hours_list(frozenset({5, 3})) == [3, 5]
    assert hours_list(None) is None


@pytest.mark.asyncio
async def test_reload_reads_the_hour_set_and_legacy_pairs() -> None:
    marked = [{"action": "deny", "allowed_hours_utc": [3, 4, 5], "allowed_hours_format": "hours"}]
    legacy_pair = [{"action": "deny", "allowed_hours_utc": [9, 17]}]
    legacy_list = [{"action": "deny", "allowed_hours_utc": [1, 2, 3], "allowed_weekdays": [0]}]
    session = _Session(
        {
            "FROM governance_policies": [
                ("marked", "deny", "a", "t1", 0),
                ("legacy-pair", "deny", "b", "t1", 0),
                ("legacy-list", "deny", "c", "t1", 0),
            ],
            "FROM policy_versions": [
                ("marked", json.dumps(marked)),
                ("legacy-pair", json.dumps(legacy_pair)),
                ("legacy-list", json.dumps(legacy_list)),
            ],
        }
    )
    engine = PolicyEngine()
    await engine.reload_from_db(lambda: session, tenant_id="t1", strict=True)
    by_name = {p.name: p for p in engine._policies}
    assert by_name["marked"].allowed_hours_utc == frozenset({3, 4, 5})
    assert by_name["legacy-pair"].allowed_hours_utc == (9, 17)
    assert by_name["legacy-list"].allowed_hours_utc == frozenset({1, 2, 3})
    assert by_name["legacy-list"].allowed_weekdays == [0]


# ── API ───────────────────────────────────────────────────────────────────────


async def _post(app: Any, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post("/governance/policies", json=body)


@pytest.mark.asyncio
async def test_create_with_an_hour_list_is_active_only_in_those_hours() -> None:
    app = make_app(router)
    engine = PolicyEngine()
    app.state.policy_engine = engine
    r = await _post(
        app,
        {
            "name": "night-only",
            "tools_pattern": "deploy*",
            "action": "deny",
            "allowed_hours_utc": _other_hours(),
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["allowed_hours_utc"] == sorted(_other_hours())
    (policy,) = engine._policies
    assert policy.allowed_hours_utc == frozenset(_other_hours())
    # Outside its hours the policy does not apply (it used to apply 24/7).
    assert engine.evaluate("deploy_prod", tenant_ctx=tenant()) == PolicyResult.ALLOW

    r = await _post(
        app,
        {
            "name": "now",
            "tools_pattern": "deploy*",
            "action": "deny",
            "allowed_hours_utc": [_hour(), (_hour() + 12) % 24],
        },
    )
    assert r.status_code == 201, r.text
    assert engine.evaluate("deploy_prod", tenant_ctx=tenant()) == PolicyResult.DENY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "window",
    [
        {"allowed_hours_utc": [24]},
        {"allowed_hours_utc": [-1]},
        {"allowed_weekdays": [7]},
    ],
)
async def test_out_of_range_window_is_rejected(window: dict[str, Any]) -> None:
    app = make_app(router)
    app.state.policy_engine = PolicyEngine()
    r = await _post(app, {"name": "p", "tools_pattern": "x", "action": "deny", **window})
    assert r.status_code == 422, r.text
    assert app.state.policy_engine._policies == []


@pytest.mark.asyncio
async def test_create_persists_the_hour_set_with_its_format_marker() -> None:
    db = _DB()
    app = _app(db)
    r = await _post(
        app,
        {
            "name": "p",
            "tools_pattern": "x",
            "action": "deny",
            "allowed_hours_utc": [5, 3, 4, 3],
            "allowed_weekdays": [4, 0],
        },
    )
    assert r.status_code == 201, r.text
    (ins,) = db.sql("INSERT INTO policy_versions")
    (rule,) = json.loads(ins[1]["rules"])
    assert rule["allowed_hours_utc"] == [3, 4, 5]
    assert rule["allowed_hours_format"] == HOURS_FORMAT
    assert rule["allowed_weekdays"] == [0, 4]


class _ListDB(_DB):
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self.rows = rows

    async def _execute(self, q: Any, params: Any = None) -> _Res:
        sql = " ".join(str(q).split())
        if "FROM governance_policies gp" in sql:
            self.stmts.append((sql, dict(params or {})))
            return _Res(self.rows)
        return await super()._execute(q, params)


@pytest.mark.asyncio
async def test_list_returns_the_time_window() -> None:
    marked = [
        {"allowed_hours_utc": [3, 5], "allowed_hours_format": "hours", "allowed_weekdays": [0, 1]}
    ]
    legacy = [{"allowed_hours_utc": [9, 12]}]
    db = _ListDB(
        [
            ("p1", "marked", "a*", "deny", 5, "", json.dumps(marked)),
            ("p2", "legacy", "b*", "deny", 0, None, legacy),
            ("p3", "plain", "c*", "require_approval", 0, "", None),
        ]
    )
    app = _app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/governance/policies")
    assert r.status_code == 200, r.text
    by_id = {p["policy_id"]: p for p in r.json()}
    assert by_id["p1"]["allowed_hours_utc"] == [3, 5]
    assert by_id["p1"]["allowed_weekdays"] == [0, 1]
    assert by_id["p2"]["allowed_hours_utc"] == [9, 10, 11]
    assert by_id["p2"]["allowed_weekdays"] is None
    assert by_id["p3"]["allowed_hours_utc"] is None
    (q, params) = db.sql("FROM governance_policies gp")[0]
    # The latest live snapshot, tenant-scoped like the outer query.
    assert "policy_versions" in q and "pv.tenant_id = gp.tenant_id" in q
    assert params == {"tid": "t-gov"}
