"""Task 8 — wiring ``OrgBrain`` into the ``org_brain_loop`` Celery beat task.

Exercises ``_brain_tick_for_org`` directly (no Celery, no real DB/Redis):
monkeypatches ``OrgBrain.run_tick`` to record calls and ``is_feature_enabled``
(as imported into ``app.scaling.tasks``) to control the feature-flag gate,
and feeds fake ``db_factory``/``redis`` doubles. Proves the short-circuit
gate (flag off / autonomy < 3 / tick lock held -> ``run_tick`` NOT called)
and the real wiring path (flag on + L4 + lock acquired -> ``run_tick``
called once with the org's settings/budget/goals/mission).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.scaling.tasks import _brain_tick_for_org


class _FakeResult:
    def __init__(self, org):
        self._org = org

    def scalar_one_or_none(self):
        return self._org


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Minimal stand-in for an AsyncSession: supports the exact usage shape
    ``async with db_factory() as session, session.begin(), sqlalchemy_rls_context(...)``
    plus ``OrgService.get_organization``'s ``session.execute(...).scalar_one_or_none()``.
    """

    def __init__(self, org):
        self._org = org

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _NullCtx()

    async def execute(self, *a, **kw):
        return _FakeResult(self._org)


def _fake_org():
    return SimpleNamespace(
        settings={"foo": "bar"},
        monthly_budget_usd=1500.0,
        goals=["grow revenue"],
        mission="grow the org",
    )


def _fake_db_factory(org):
    def factory():
        return _FakeSession(org)

    return factory


def _raising_db_factory():
    def factory():
        raise AssertionError("db_factory must not be touched when the tick is skipped")

    return factory


class _FakeRedis:
    def __init__(self, lock_ok: bool):
        self._lock_ok = lock_ok

    async def set(self, *a, **kw):
        return "OK" if self._lock_ok else None


class _RaisingRedis:
    async def set(self, *a, **kw):
        raise AssertionError("redis must not be touched when the flag/level gate skips")


@pytest.fixture
def run_tick_calls(monkeypatch):
    """Monkeypatch OrgBrain.run_tick to record calls instead of running the
    real SENSE/DECIDE/GUARD/ACT/NARRATE pipeline."""
    calls = []

    async def _fake_run_tick(self, **kwargs):
        calls.append(kwargs)
        return {"proposed": 1, "executed": 0, "blocked": 0}

    monkeypatch.setattr("app.org.brain.OrgBrain.run_tick", _fake_run_tick)
    return calls


@pytest.mark.asyncio
async def test_l1_org_is_skipped_run_tick_not_called(monkeypatch, run_tick_calls):
    monkeypatch.setattr(
        "app.scaling.tasks.is_feature_enabled", lambda flag, tenant_id=None: True
    )
    result = await _brain_tick_for_org(
        db_factory=_raising_db_factory(),
        redis=_RaisingRedis(),
        org_id="o1",
        tenant_id="t1",
        autonomy_level=1,
    )
    assert result == {"proposed": 0, "executed": 0, "blocked": 0}
    assert run_tick_calls == []


@pytest.mark.asyncio
async def test_l4_org_flag_on_lock_acquired_calls_run_tick_once(monkeypatch, run_tick_calls):
    monkeypatch.setattr(
        "app.scaling.tasks.is_feature_enabled", lambda flag, tenant_id=None: True
    )
    org = _fake_org()
    org_id = "11111111-1111-1111-1111-111111111111"
    result = await _brain_tick_for_org(
        db_factory=_fake_db_factory(org),
        redis=_FakeRedis(lock_ok=True),
        org_id=org_id,
        tenant_id="t1",
        autonomy_level=4,
    )
    assert len(run_tick_calls) == 1
    call = run_tick_calls[0]
    assert call["org_id"] == org_id
    assert call["tenant_id"] == "t1"
    assert call["autonomy_level"] == 4
    assert call["org_settings"] == {"foo": "bar"}
    assert call["monthly_budget_usd"] == 1500.0
    assert call["org_goals"] == ["grow revenue"]
    assert call["org_mission"] == "grow the org"
    assert result == {"proposed": 1, "executed": 0, "blocked": 0}


@pytest.mark.asyncio
async def test_flag_off_tenant_is_skipped_run_tick_not_called(monkeypatch, run_tick_calls):
    monkeypatch.setattr(
        "app.scaling.tasks.is_feature_enabled", lambda flag, tenant_id=None: False
    )
    result = await _brain_tick_for_org(
        db_factory=_raising_db_factory(),
        redis=_RaisingRedis(),
        org_id="o1",
        tenant_id="t1",
        autonomy_level=4,
    )
    assert result == {"proposed": 0, "executed": 0, "blocked": 0}
    assert run_tick_calls == []


@pytest.mark.asyncio
async def test_tick_lock_held_is_skipped_run_tick_not_called(monkeypatch, run_tick_calls):
    monkeypatch.setattr(
        "app.scaling.tasks.is_feature_enabled", lambda flag, tenant_id=None: True
    )
    result = await _brain_tick_for_org(
        db_factory=_raising_db_factory(),
        redis=_FakeRedis(lock_ok=False),
        org_id="o1",
        tenant_id="t1",
        autonomy_level=4,
    )
    assert result == {"proposed": 0, "executed": 0, "blocked": 0}
    assert run_tick_calls == []
