"""Owner decisions are Settings (env-configurable) with today's values as defaults.

Each test flips one setting and shows the behaviour follows it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings, get_settings

BACKEND = Path(__file__).resolve().parents[2]


def test_defaults_match_the_shipped_behaviour() -> None:
    s = Settings()
    assert (
        s.schedule_min_interval_free_s,
        s.schedule_min_interval_starter_s,
        s.schedule_min_interval_professional_s,
        s.schedule_min_interval_enterprise_s,
    ) == (900, 300, 60, 60)
    assert s.repo_ingest_max_concurrent_per_tenant == 2
    assert s.knowledge_federated_max_collections == 20
    assert s.ingestion_egress_strict_pinning is True
    assert s.fully_autonomous_eval_gate_enabled is True
    assert s.subgoals_share_parent_slot is True


def test_every_setting_is_documented_in_env_example() -> None:
    text = (BACKEND / ".env.example").read_text()
    for name in (
        "SCHEDULE_MIN_INTERVAL_FREE_S",
        "SCHEDULE_MIN_INTERVAL_STARTER_S",
        "SCHEDULE_MIN_INTERVAL_PROFESSIONAL_S",
        "SCHEDULE_MIN_INTERVAL_ENTERPRISE_S",
        "REPO_INGEST_MAX_CONCURRENT_PER_TENANT",
        "KNOWLEDGE_FEDERATED_MAX_COLLECTIONS",
        "INGESTION_EGRESS_STRICT_PINNING",
        "FULLY_AUTONOMOUS_EVAL_GATE_ENABLED",
        "SUBGOALS_SHARE_PARENT_SLOT",
    ):
        assert name in text, name


# ── schedule plan minimum intervals ──────────────────────────────────────────


def test_schedule_plan_floor_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.triggers.models import check_plan_interval, plan_min_interval_seconds

    assert plan_min_interval_seconds("free") == 900
    monkeypatch.setattr(get_settings(), "schedule_min_interval_free_s", 120)
    monkeypatch.setattr(get_settings(), "schedule_min_interval_starter_s", 600)
    assert plan_min_interval_seconds("free") == 120
    assert plan_min_interval_seconds("starter") == 600
    assert plan_min_interval_seconds("unknown-plan") == 120  # unknown -> free's floor
    check_plan_interval(120, "free")  # allowed now
    with pytest.raises(ValueError):
        check_plan_interval(300, "starter")


# ── repository ingestion concurrency ─────────────────────────────────────────


def test_repo_ingest_concurrency_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import patch

    from tests.api.test_repo_ingest_durable import _post, _store

    monkeypatch.setattr(get_settings(), "repo_ingest_max_concurrent_per_tenant", 2)
    with patch("app.ingestion.repo_tasks.ingest_repository_task"):
        assert _post(_store(active=3)).status_code == 429
    monkeypatch.setattr(get_settings(), "repo_ingest_max_concurrent_per_tenant", 5)
    with patch("app.ingestion.repo_tasks.ingest_repository_task"):
        assert _post(_store(active=3)).status_code == 202


# ── federated search max collections ─────────────────────────────────────────


async def test_federated_max_collections_follows_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.knowledge import federated_search as fs

    monkeypatch.setattr(get_settings(), "knowledge_federated_max_collections", 3)
    assert fs.max_federated_collections() == 3
    with pytest.raises(ValueError, match="at most 3 collections"):
        await fs.federated_search("q", ["a", "b", "c", "d"], gateway=object(), tenant_ctx=object())


# ── strict egress pinning (Kafka refused) ────────────────────────────────────


def test_strict_pinning_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingestion.connector_egress import (
        ConnectorEgressBlockedError,
        require_pinnable_driver,
    )

    monkeypatch.setattr(get_settings(), "ingestion_egress_strict_pinning", True)
    with pytest.raises(ConnectorEgressBlockedError):
        require_pinnable_driver("confluent-kafka", context="kafka")
    monkeypatch.setattr(get_settings(), "ingestion_egress_strict_pinning", False)
    require_pinnable_driver("confluent-kafka", context="kafka")


# ── fully-autonomous eval rollout gate ───────────────────────────────────────


def test_eval_gate_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.api.test_agent_rollout_gate_enforced import _client, _h, _seed_run

    client, ctx = _client()
    _seed_run(ctx, "suite-weak", passed=1, total=4)
    no_suite = {"name": "a", "autonomy_mode": "fully-autonomous"}
    weak = {**no_suite, "eval_suite_id": "suite-weak"}

    monkeypatch.setattr(get_settings(), "fully_autonomous_eval_gate_enabled", True)
    assert client.post("/agents", json=no_suite, headers=_h()).status_code == 422
    assert client.post("/agents", json=weak, headers=_h()).status_code == 409

    monkeypatch.setattr(get_settings(), "fully_autonomous_eval_gate_enabled", False)
    assert client.post("/agents", json=no_suite, headers=_h()).status_code == 201
    assert client.post("/agents", json=weak, headers=_h()).status_code == 201


# ── sub-goals and the parent's concurrency slot ──────────────────────────────


def test_subgoal_slot_sharing_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.supervisor import SUBGOAL_MARKER
    from app.services.goal_service import _holds_concurrency_slot

    sub = {SUBGOAL_MARKER: True}
    assert _holds_concurrency_slot({}) is True
    assert _holds_concurrency_slot(sub) is False
    monkeypatch.setattr(get_settings(), "subgoals_share_parent_slot", False)
    assert _holds_concurrency_slot(sub) is True


async def test_worker_releases_a_subgoal_slot_only_when_not_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    released: list[str] = []

    async def _decrement(*, tenant_id: str, redis: Any, goal_id: str) -> None:
        released.append(tenant_id)

    class _Redis:
        async def aclose(self) -> None:
            return None

    import redis.asyncio as aioredis

    import app.tenancy.limits as limits

    monkeypatch.setattr(limits, "decrement_concurrent_goals", _decrement)
    monkeypatch.setattr(aioredis, "from_url", lambda *_a, **_k: _Redis())
    token = tasks._SUBGOAL_RUN.set(True)
    try:
        await tasks._decrement_after_completion("t1", "redis://fake/0", "g1")
        assert released == []
        monkeypatch.setattr(get_settings(), "subgoals_share_parent_slot", False)
        await tasks._decrement_after_completion("t1", "redis://fake/0", "g1")
        assert released == ["t1"]
    finally:
        tasks._SUBGOAL_RUN.reset(token)
