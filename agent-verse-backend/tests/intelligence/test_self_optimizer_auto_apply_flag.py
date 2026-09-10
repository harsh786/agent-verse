"""Auto-apply gating for SelfOptimizerV2 — the closed self-improvement loop.

When ``auto_apply`` is on (the default), a winning candidate is written back to
the agent automatically at experiment conclusion. When off (the production
default, sourced from ``enable_self_improvement_auto_apply``), the experiment is
still concluded and its winner recorded, but the config is left *pending* a
manual apply via :meth:`SelfOptimizerV2.apply_pending`.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.intelligence.self_optimizer_v2 import SelfOptimizerV2


def _conclude_db_with_winning_candidate() -> tuple[AsyncMock, dict[str, int]]:
    """A mock async-session factory whose experiment JOIN yields a candidate win."""
    counters = {"experiment_updates": 0}
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=None)
    mock_db.commit = AsyncMock()

    async def execute_side_effect(query, params=None, **kwargs):
        result = MagicMock()
        q = str(query)
        if "improvement_experiments" in q and "JOIN" in q:
            result.fetchone = lambda: MagicMock(
                __getitem__=lambda s, i: [
                    20,  # min_samples_per_arm
                    0.95,  # significance_threshold
                    "eval_score",  # success_metric
                    "agent-1",  # agent_id
                    json.dumps({"system_prompt": "improved"}),  # candidate_config
                    25,  # ctrl_n
                    25,  # cand_n
                    0.7,  # ctrl_mean
                    0.95,  # cand_mean (candidate clearly wins)
                ][i]
            )
        else:
            if "UPDATE improvement_experiments" in q:
                counters["experiment_updates"] += 1
            result.fetchone = lambda: None
        result.scalar = lambda: 0
        return result

    mock_db.execute = AsyncMock(side_effect=execute_side_effect)
    return mock_db, counters


@pytest.mark.asyncio
async def test_auto_apply_off_concludes_but_does_not_apply() -> None:
    """auto_apply=False: winner is recorded, but apply_suggestion is NOT called."""
    mock_db, counters = _conclude_db_with_winning_candidate()
    optimizer = SelfOptimizerV2(AsyncMock(), lambda: mock_db, AsyncMock(), auto_apply=False)

    apply_calls: list[str] = []

    async def spy_apply(tid, aid, eid, cfg):
        apply_calls.append(eid)
        return True

    optimizer.apply_suggestion = spy_apply  # type: ignore[assignment]

    await optimizer._maybe_conclude_experiment("tenant-1", "exp-1")

    assert apply_calls == [], "auto_apply=False must not auto-write the config"
    assert counters["experiment_updates"] >= 1, "experiment should still be concluded"


@pytest.mark.asyncio
async def test_auto_apply_on_applies_the_winner() -> None:
    """auto_apply=True (default): the winning candidate is applied automatically."""
    mock_db, _ = _conclude_db_with_winning_candidate()
    optimizer = SelfOptimizerV2(AsyncMock(), lambda: mock_db, AsyncMock())  # default True

    apply_calls: list[str] = []

    async def spy_apply(tid, aid, eid, cfg):
        apply_calls.append(eid)
        return True

    optimizer.apply_suggestion = spy_apply  # type: ignore[assignment]

    await optimizer._maybe_conclude_experiment("tenant-1", "exp-1")

    assert apply_calls == ["exp-1"], "auto_apply=True must apply the winner"


# ── apply_pending — the human-in-the-loop manual apply path ───────────────────


def _db_returning(row: list | None) -> AsyncMock:
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=None)
    mock_db.commit = AsyncMock()

    async def execute_side_effect(query, params=None, **kwargs):
        result = MagicMock()
        if row is None:
            result.fetchone = lambda: None
        else:
            result.fetchone = lambda: MagicMock(__getitem__=lambda s, i: row[i])
        return result

    mock_db.execute = AsyncMock(side_effect=execute_side_effect)
    return mock_db


@pytest.mark.asyncio
async def test_apply_pending_applies_candidate_winner() -> None:
    mock_db = _db_returning(["agent-9", json.dumps({"system_prompt": "v2"}), "candidate", None])
    optimizer = SelfOptimizerV2(AsyncMock(), lambda: mock_db, AsyncMock(), auto_apply=False)

    applied: list[dict] = []

    async def spy_apply(tid, aid, eid, cfg):
        applied.append({"agent": aid, "cfg": cfg})
        return True

    optimizer.apply_suggestion = spy_apply  # type: ignore[assignment]

    result = await optimizer.apply_pending("tenant-1", "exp-1")

    assert result["applied"] is True
    assert result["agent_id"] == "agent-9"
    assert applied == [{"agent": "agent-9", "cfg": {"system_prompt": "v2"}}]


@pytest.mark.asyncio
async def test_apply_pending_unknown_experiment() -> None:
    optimizer = SelfOptimizerV2(AsyncMock(), lambda: _db_returning(None), AsyncMock())
    result = await optimizer.apply_pending("tenant-1", "missing")
    assert result == {"applied": False, "reason": "not_found"}


@pytest.mark.asyncio
async def test_apply_pending_refuses_non_candidate_winner() -> None:
    mock_db = _db_returning(["agent-9", "{}", "control", None])
    optimizer = SelfOptimizerV2(AsyncMock(), lambda: mock_db, AsyncMock())
    result = await optimizer.apply_pending("tenant-1", "exp-1")
    assert result["applied"] is False
    assert result["reason"] == "winner_is_control"


@pytest.mark.asyncio
async def test_apply_pending_refuses_already_applied() -> None:
    mock_db = _db_returning(["agent-9", "{}", "candidate", "2026-01-01T00:00:00Z"])
    optimizer = SelfOptimizerV2(AsyncMock(), lambda: mock_db, AsyncMock())
    result = await optimizer.apply_pending("tenant-1", "exp-1")
    assert result["applied"] is False
    assert result["reason"] == "already_applied"
