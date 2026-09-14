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


# ── Publish-before-commit race regression ────────────────────────────────
#
# A prior fix made OrgBrain.run_tick's execute branch dispatch the created
# mission via an injected dispatcher wired (in ``_brain_tick_for_org``) to
# ``execute_org_mission.apply_async`` directly. Because the ENTIRE tick runs
# inside one outer transaction that only commits when the ``async with``
# block exits, a fire-and-forget broker publish issued from inside that
# block could reach a Celery worker before the mission row was visible —
# the worker's ``SELECT ... FOR UPDATE`` claim would then see no row, log
# ``mission_missing``, and no-op (silently deferring to the 3-minute
# resweep). The fix buffers dispatches raised during the tick and flushes
# them only after the transaction block exits cleanly (i.e. only once
# committed) — mirroring the reference pattern in
# ``fire_due_org_mission_schedules`` ("create + commit ... then dispatch
# after commit"). These two tests pin that ordering at the seam
# ``_brain_tick_for_org`` owns (the dispatcher it hands to ``OrgBrain``),
# without needing a real database/transaction.


@pytest.mark.asyncio
async def test_execute_dispatch_happens_only_after_commit(monkeypatch):
    """The buffered dispatch must flush strictly AFTER the tick's session
    exits (i.e. after the outer transaction would have committed), never
    while the block recording the mission write is still open."""
    monkeypatch.setattr(
        "app.scaling.tasks.is_feature_enabled", lambda flag, tenant_id=None: True
    )

    events: list[str] = []

    class _EventedSession(_FakeSession):
        async def __aexit__(self, *exc):
            # Stands in for ``session.begin()``'s real commit-on-clean-exit:
            # record that the transaction's scope has closed.
            events.append("session_exit")
            return await super().__aexit__(*exc)

    org = _fake_org()

    async def _fake_run_tick_dispatches(self, **kwargs):
        # Simulate OrgBrain.run_tick's execute branch: call the injected
        # dispatcher (self._dispatch, set in __init__ and NOT monkeypatched
        # here) while the outer transaction is still open. In the fixed
        # code this only appends to the pending-dispatch buffer — it must
        # not itself cause a broker publish.
        self._dispatch(
            {
                "mission_id": "m1",
                "tenant_id": kwargs["tenant_id"],
                "org_id": kwargs["org_id"],
                "objective": "do the thing",
                "title": "do the thing",
                "autonomy_level": kwargs["autonomy_level"],
                "priority": "medium",
            }
        )
        assert events == [], "dispatch call must not publish before the session exits"
        return {"proposed": 0, "executed": 1, "blocked": 0}

    monkeypatch.setattr("app.org.brain.OrgBrain.run_tick", _fake_run_tick_dispatches)

    def _fake_apply_async(*, kwargs):
        events.append("dispatch")
        return SimpleNamespace(id="task-1", kwargs=kwargs)

    monkeypatch.setattr("app.scaling.tasks.execute_org_mission.apply_async", _fake_apply_async)

    def _factory():
        return _EventedSession(org)

    result = await _brain_tick_for_org(
        db_factory=_factory,
        redis=_FakeRedis(lock_ok=True),
        org_id="11111111-1111-1111-1111-111111111111",
        tenant_id="t1",
        autonomy_level=4,
    )

    assert result == {"proposed": 0, "executed": 1, "blocked": 0}
    assert events == ["session_exit", "dispatch"], (
        "dispatch must be flushed strictly after the tick's transaction "
        "exits (commits), never before or interleaved with it"
    )


@pytest.mark.asyncio
async def test_execute_dispatch_not_fired_if_tick_raises(monkeypatch):
    """If the tick transaction raises (and therefore rolls back instead of
    committing), a dispatch buffered during that tick must NOT be flushed —
    the mission row it refers to was never persisted."""
    monkeypatch.setattr(
        "app.scaling.tasks.is_feature_enabled", lambda flag, tenant_id=None: True
    )
    org = _fake_org()
    apply_async_calls: list[dict] = []

    async def _fake_run_tick_dispatches_then_raises(self, **kwargs):
        self._dispatch({"mission_id": "m1", "tenant_id": kwargs["tenant_id"]})
        raise RuntimeError("boom mid-tick")

    monkeypatch.setattr(
        "app.org.brain.OrgBrain.run_tick", _fake_run_tick_dispatches_then_raises
    )

    def _fake_apply_async(*, kwargs):
        apply_async_calls.append(kwargs)

    monkeypatch.setattr("app.scaling.tasks.execute_org_mission.apply_async", _fake_apply_async)

    with pytest.raises(RuntimeError, match="boom mid-tick"):
        await _brain_tick_for_org(
            db_factory=_fake_db_factory(org),
            redis=_FakeRedis(lock_ok=True),
            org_id="11111111-1111-1111-1111-111111111111",
            tenant_id="t1",
            autonomy_level=4,
        )

    assert apply_async_calls == []
