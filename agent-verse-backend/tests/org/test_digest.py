"""Tests for the 'While You Were Away' digest generator — app/org/digest.py"""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.org.digest import (
    DigestCache,
    DigestGenerator,
    DigestItem,
    WhileYouWereAwayDigest,
    _build_summary,
    _fmt_duration,
    get_digest_generator,
)
from app.org.models import OrgTask


@contextlib.contextmanager
def _patched_org_task_department_id():
    """Temporarily alias `OrgTask.department_id` to the real `mission_id`
    column so digest._insights()'s bottleneck-detection query (which
    references a column that doesn't exist on the current schema) can be
    exercised in isolation. SQLAlchemy's declarative metaclass forbids
    `delattr` on mapped attributes, so we restore via `type.__delattr__`
    directly rather than relying on `monkeypatch`'s teardown.
    """
    OrgTask.department_id = OrgTask.mission_id
    try:
        yield
    finally:
        type.__delattr__(OrgTask, "department_id")


def _empty_session() -> AsyncMock:
    """Session double whose execute() always yields an empty result set."""
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all = MagicMock(return_value=[])
    result.scalar_one_or_none = MagicMock(return_value=0)
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    return session


def _result_with_scalars(rows: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all = MagicMock(return_value=rows)
    return result


def _result_scalar(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _result_all(rows: list) -> MagicMock:
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    return result


# ── _fmt_duration ──────────────────────────────────────────────────────────


def test_fmt_duration_hours_and_minutes():
    assert _fmt_duration(3723) == "1h 2m"


def test_fmt_duration_minutes_only():
    assert _fmt_duration(120) == "2m"


# ── DigestCache ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_miss_returns_none():
    cache = DigestCache()
    assert await cache.get("org1", "t1") is None


@pytest.mark.asyncio
async def test_cache_set_then_get_hit():
    cache = DigestCache()
    digest = WhileYouWereAwayDigest(
        org_id="org1", tenant_id="t1", since=datetime.now(UTC), generated_at=datetime.now(UTC)
    )
    await cache.set("org1", "t1", digest)
    cached = await cache.get("org1", "t1")
    assert cached is digest


@pytest.mark.asyncio
async def test_cache_expired_entry_returns_none_and_evicts():
    cache = DigestCache()
    digest = WhileYouWereAwayDigest(
        org_id="org1", tenant_id="t1", since=datetime.now(UTC), generated_at=datetime.now(UTC)
    )
    key = cache._key("org1", "t1")
    # Insert an entry stamped far enough in the past to exceed the TTL.
    cache._local[key] = (digest, datetime.now(UTC) - timedelta(seconds=10_000))
    assert await cache.get("org1", "t1") is None
    assert key not in cache._local


@pytest.mark.asyncio
async def test_cache_set_writes_to_redis_when_present():
    redis = AsyncMock()
    cache = DigestCache(redis=redis)
    digest = WhileYouWereAwayDigest(
        org_id="org1", tenant_id="t1", since=datetime.now(UTC), generated_at=datetime.now(UTC)
    )
    await cache.set("org1", "t1", digest)
    assert redis.setex.await_count == 1


@pytest.mark.asyncio
async def test_cache_set_swallows_redis_errors():
    redis = AsyncMock()
    redis.setex = AsyncMock(side_effect=RuntimeError("boom"))
    cache = DigestCache(redis=redis)
    digest = WhileYouWereAwayDigest(
        org_id="org1", tenant_id="t1", since=datetime.now(UTC), generated_at=datetime.now(UTC)
    )
    # Should not raise even though the redis call fails.
    await cache.set("org1", "t1", digest)


# ── _build_summary ─────────────────────────────────────────────────────────


def _digest(**overrides) -> WhileYouWereAwayDigest:
    base = dict(
        org_id="org1",
        tenant_id="t1",
        since=datetime.now(UTC),
        generated_at=datetime.now(UTC),
    )
    base.update(overrides)
    return WhileYouWereAwayDigest(**base)


def test_build_summary_no_activity():
    d = _digest()
    assert _build_summary(d) == "No significant activity while you were away."


def test_build_summary_singular_mission():
    d = _digest(missions_completed=1)
    text = _build_summary(d)
    assert "1 mission completed" in text


def test_build_summary_plural_missions_and_started():
    d = _digest(missions_completed=3, missions_started=2)
    text = _build_summary(d)
    assert "3 missions completed" in text
    assert "2 new missions started" in text


def test_build_summary_approvals_and_blocked_and_decisions_and_cost():
    item = DigestItem(
        category="needs_attention",
        title="x",
        summary="y",
        icon="⏳",
        priority=1,
        action_required=True,
        action_type="approve",
        related_id=None,
        cost_usd=None,
        duration_str=None,
    )
    d = _digest(
        pending_approvals=[item],
        blocked_items=[item, item],
        decisions_made=4,
        total_cost_usd=12.5,
    )
    text = _build_summary(d)
    assert "1 approval await your review" in text
    assert "2 items blocked" in text
    assert "4 decisions made" in text
    assert "$12.50 spent" in text


# ── DigestGenerator.generate() ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_with_no_activity_returns_empty_digest():
    session = _empty_session()
    gen = DigestGenerator(session=session)
    digest = await gen.generate("org1", "t1")
    assert digest.missions_completed == 0
    assert digest.summary_text == "No significant activity while you were away."
    assert digest.completed_missions == []


@pytest.mark.asyncio
async def test_generate_uses_cache_on_second_call():
    session = _empty_session()
    gen = DigestGenerator(session=session)
    first = await gen.generate("org1", "t1")
    call_count_before = session.execute.await_count
    second = await gen.generate("org1", "t1")
    assert second is first
    # No new DB calls made on cache hit.
    assert session.execute.await_count == call_count_before


@pytest.mark.asyncio
async def test_generate_defaults_since_to_lookback_window():
    session = _empty_session()
    gen = DigestGenerator(session=session)
    before = datetime.now(UTC)
    digest = await gen.generate("org1", "t1")
    assert digest.since < before


@pytest.mark.asyncio
async def test_generate_section_failure_degrades_gracefully():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    gen = DigestGenerator(session=session)
    digest = await gen.generate("org1", "t1")
    assert digest.completed_missions == []
    assert digest.pending_approvals == []
    assert digest.missions_completed == 0


@pytest.mark.asyncio
async def test_generate_handles_a_section_coroutine_raising_directly():
    """Every _<section>() method already has its own broad try/except, so in
    practice asyncio.gather(..., return_exceptions=True) never actually sees a
    raised exception. Patch one section to bypass its own guard and confirm
    generate()'s _safe() helper still degrades that section to its default
    instead of propagating the failure."""
    session = _empty_session()
    gen = DigestGenerator(session=session)
    with patch.object(
        gen, "_completed_missions", AsyncMock(side_effect=RuntimeError("section exploded"))
    ):
        digest = await gen.generate("org1", "t1")
    assert digest.completed_missions == []
    # Other sections still computed normally.
    assert digest.pending_approvals == []


# ── _completed_missions ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_completed_missions_builds_items_with_cost_and_duration():
    started = datetime.now(UTC) - timedelta(hours=1)
    updated = datetime.now(UTC)
    row = SimpleNamespace(
        id="m1", title="Launch feature", updated_at=updated, started_at=started, cost_usd=4.5
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_with_scalars([row]))
    gen = DigestGenerator(session=session)
    items = await gen._completed_missions("org1", "t1", datetime.now(UTC) - timedelta(hours=8))
    assert len(items) == 1
    assert items[0].cost_usd == 4.5
    assert items[0].duration_str is not None
    assert items[0].category == "completed"


@pytest.mark.asyncio
async def test_completed_missions_falls_back_to_spent_usd_and_no_duration():
    row = SimpleNamespace(id="m2", title="No start", updated_at=datetime.now(UTC), spent_usd=2.0)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_with_scalars([row]))
    gen = DigestGenerator(session=session)
    items = await gen._completed_missions("org1", "t1", datetime.now(UTC))
    assert items[0].cost_usd == 2.0
    assert items[0].duration_str is None


@pytest.mark.asyncio
async def test_completed_missions_handles_exception():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("boom"))
    gen = DigestGenerator(session=session)
    items = await gen._completed_missions("org1", "t1", datetime.now(UTC))
    assert items == []


# ── _completed_tasks ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_completed_tasks_builds_items():
    row = SimpleNamespace(id="t1", title="Write report", updated_at=datetime.now(UTC))
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_with_scalars([row]))
    gen = DigestGenerator(session=session)
    items = await gen._completed_tasks("org1", "t1", datetime.now(UTC))
    assert len(items) == 1
    assert items[0].title == "Write report"
    assert items[0].cost_usd is None


@pytest.mark.asyncio
async def test_completed_tasks_handles_exception():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("boom"))
    gen = DigestGenerator(session=session)
    items = await gen._completed_tasks("org1", "t1", datetime.now(UTC))
    assert items == []


# ── _pending_approvals ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_approvals_uses_payload_fields():
    row = SimpleNamespace(
        id="ev1",
        payload={"title": "Approve deploy", "description": "Needs sign-off", "approval_id": "a1"},
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_with_scalars([row]))
    gen = DigestGenerator(session=session)
    items = await gen._pending_approvals("org1", "t1")
    assert items[0].title == "Approve deploy"
    assert items[0].related_id == "a1"
    assert items[0].action_type == "approve"


@pytest.mark.asyncio
async def test_pending_approvals_defaults_when_payload_missing():
    row = SimpleNamespace(id="ev2", payload=None)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_with_scalars([row]))
    gen = DigestGenerator(session=session)
    items = await gen._pending_approvals("org1", "t1")
    assert items[0].title == "Approval needed"
    assert items[0].related_id == "ev2"


@pytest.mark.asyncio
async def test_pending_approvals_handles_exception():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("boom"))
    gen = DigestGenerator(session=session)
    items = await gen._pending_approvals("org1", "t1")
    assert items == []


# ── _blocked_items ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocked_items_builds_items():
    row = SimpleNamespace(id="t9", title="Stuck task", updated_at=datetime.now(UTC))
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_with_scalars([row]))
    gen = DigestGenerator(session=session)
    items = await gen._blocked_items("org1", "t1")
    assert items[0].icon == "🚫"
    assert items[0].action_required is True


@pytest.mark.asyncio
async def test_blocked_items_handles_exception():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("boom"))
    gen = DigestGenerator(session=session)
    items = await gen._blocked_items("org1", "t1")
    assert items == []


# ── _budget_alerts ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_alerts_with_cost_in_payload():
    row = SimpleNamespace(
        id="ev5", payload={"title": "80% used", "description": "watch spend", "cost_usd": 80.0}
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_with_scalars([row]))
    gen = DigestGenerator(session=session)
    items = await gen._budget_alerts("org1", "t1")
    assert items[0].cost_usd == 80.0


@pytest.mark.asyncio
async def test_budget_alerts_without_cost_in_payload():
    row = SimpleNamespace(id="ev6", payload={})
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_with_scalars([row]))
    gen = DigestGenerator(session=session)
    items = await gen._budget_alerts("org1", "t1")
    assert items[0].cost_usd is None
    assert items[0].title == "Budget alert"


@pytest.mark.asyncio
async def test_budget_alerts_handles_exception():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("boom"))
    gen = DigestGenerator(session=session)
    items = await gen._budget_alerts("org1", "t1")
    assert items == []


# ── _insights ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insights_bottleneck_query_degrades_when_column_missing():
    """`OrgTask` has no `department_id` column in the current schema, so the
    bottleneck sub-query always raises AttributeError before ever calling
    `session.execute`. That failure must be isolated so it doesn't suppress
    the unrelated high-velocity insight (see the digest.py fix)."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar(7))  # high-velocity count
    gen = DigestGenerator(session=session)
    insights = await gen._insights("org1", "t1", datetime.now(UTC) - timedelta(hours=8))
    titles = [i.title for i in insights]
    assert "High productivity period" in titles
    assert not any("Bottleneck" in t for t in titles)
    # Only the high-velocity query actually reaches the session.
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_insights_detects_bottleneck_and_high_velocity():
    """With a real (existing) column standing in for the missing
    `department_id`, the bottleneck-detection logic itself works correctly."""
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result_all([("dept-123456789", 5)]),  # blocked-per-dept grouping
            _result_scalar("Engineering"),  # dept name lookup
            _result_scalar(7),  # completed missions count
        ]
    )
    gen = DigestGenerator(session=session)
    with _patched_org_task_department_id():
        insights = await gen._insights("org1", "t1", datetime.now(UTC) - timedelta(hours=8))
    titles = [i.title for i in insights]
    assert any("Bottleneck in Engineering" in t for t in titles)
    assert any("High productivity period" in t for t in titles)


@pytest.mark.asyncio
async def test_insights_dept_name_falls_back_when_missing():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result_all([("deadbeef-0000", 4)]),
            _result_scalar(None),  # no department name found
            _result_scalar(0),  # not enough completions for high-velocity insight
        ]
    )
    gen = DigestGenerator(session=session)
    with _patched_org_task_department_id():
        insights = await gen._insights("org1", "t1", datetime.now(UTC))
    assert len(insights) == 1
    assert "Dept deadbeef" in insights[0].title


@pytest.mark.asyncio
async def test_insights_no_bottleneck_no_velocity():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result_all([]),
            _result_scalar(2),
        ]
    )
    gen = DigestGenerator(session=session)
    with _patched_org_task_department_id():
        insights = await gen._insights("org1", "t1", datetime.now(UTC))
    assert insights == []


@pytest.mark.asyncio
async def test_insights_handles_exception():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("boom"))
    gen = DigestGenerator(session=session)
    insights = await gen._insights("org1", "t1", datetime.now(UTC))
    assert insights == []


# ── _stats ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_returns_counts():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_result_scalar(3), _result_scalar(5), _result_scalar(2)]
    )
    gen = DigestGenerator(session=session)
    stats = await gen._stats("org1", "t1", datetime.now(UTC))
    assert stats["missions_completed"] == 3
    assert stats["missions_started"] == 5
    assert stats["decisions_made"] == 2
    assert stats["agents_active"] == 0


@pytest.mark.asyncio
async def test_stats_handles_exception():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("boom"))
    gen = DigestGenerator(session=session)
    stats = await gen._stats("org1", "t1", datetime.now(UTC))
    assert stats == {
        "missions_completed": 0,
        "missions_started": 0,
        "agents_active": 0,
        "decisions_made": 0,
    }


# ── factory ────────────────────────────────────────────────────────────────


def test_get_digest_generator_factory():
    session = MagicMock()
    gen = get_digest_generator(session)
    assert isinstance(gen, DigestGenerator)
    assert gen._s is session
