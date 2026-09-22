"""Deepened coverage for app/embedding/reembedding_policy.py.

The prior suite only exercised DIMENSION_MISMATCH (and the NONE no-op case).
This adds MODEL_CHANGED, DRIFT_DETECTED, and STALE, plus trigger-priority
ordering when multiple conditions fire simultaneously — the policy checks
triggers in a fixed order (model change > dimension mismatch > drift > stale)
and returns the first that matches.
"""
from __future__ import annotations

import pytest

from app.embedding.reembedding_policy import ReembeddingPolicy, ReembeddingTrigger


@pytest.fixture
def policy() -> ReembeddingPolicy:
    return ReembeddingPolicy()


# ── Individual triggers ───────────────────────────────────────────────────────


class TestModelChangedTrigger:
    def test_different_model_triggers_model_changed(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="text-embedding-3-small",
            new_model="voyage-3-lite",
            collection_size=10,
        )
        assert trigger == ReembeddingTrigger.MODEL_CHANGED

    def test_model_changed_even_with_matching_dims(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="model-a",
            new_model="model-b",
            collection_size=10,
            old_dim=1536,
            new_dim=1536,
        )
        assert trigger == ReembeddingTrigger.MODEL_CHANGED


class TestDriftDetectedTrigger:
    def test_drift_above_threshold_triggers(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=10, drift_score=0.5
        )
        assert trigger == ReembeddingTrigger.DRIFT_DETECTED

    def test_drift_at_threshold_does_not_trigger(self, policy: ReembeddingPolicy) -> None:
        """The check is strictly '>', so exactly the threshold does not fire."""
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=10, drift_score=0.25
        )
        assert trigger == ReembeddingTrigger.NONE

    def test_drift_just_above_threshold_triggers(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=10, drift_score=0.2501
        )
        assert trigger == ReembeddingTrigger.DRIFT_DETECTED

    def test_zero_drift_never_triggers(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=10, drift_score=0.0
        )
        assert trigger == ReembeddingTrigger.NONE


class TestStaleTrigger:
    def test_stale_when_old_and_large(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=200, age_days=100
        )
        assert trigger == ReembeddingTrigger.STALE

    def test_not_stale_when_small_collection(self, policy: ReembeddingPolicy) -> None:
        """Even a very old collection is not worth re-embedding if it's tiny."""
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=50, age_days=1000
        )
        assert trigger == ReembeddingTrigger.NONE

    def test_not_stale_when_young(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=10_000, age_days=1
        )
        assert trigger == ReembeddingTrigger.NONE

    def test_age_at_threshold_does_not_trigger(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m",
            new_model="m",
            collection_size=200,
            age_days=90,
            staleness_threshold_days=90,
        )
        assert trigger == ReembeddingTrigger.NONE

    def test_age_just_above_threshold_triggers(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m",
            new_model="m",
            collection_size=200,
            age_days=91,
            staleness_threshold_days=90,
        )
        assert trigger == ReembeddingTrigger.STALE

    def test_collection_size_at_threshold_does_not_trigger(
        self, policy: ReembeddingPolicy
    ) -> None:
        """The check is 'collection_size > 100', so exactly 100 does not fire."""
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=100, age_days=200
        )
        assert trigger == ReembeddingTrigger.NONE

    def test_collection_size_just_above_threshold_triggers(
        self, policy: ReembeddingPolicy
    ) -> None:
        trigger = policy.should_reembed(
            current_model="m", new_model="m", collection_size=101, age_days=200
        )
        assert trigger == ReembeddingTrigger.STALE

    def test_custom_staleness_threshold_honoured(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m",
            new_model="m",
            collection_size=200,
            age_days=10,
            staleness_threshold_days=5,
        )
        assert trigger == ReembeddingTrigger.STALE


class TestNoTrigger:
    def test_nothing_fires_returns_none(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m",
            new_model="m",
            collection_size=1000,
            drift_score=0.0,
            age_days=1,
            old_dim=1536,
            new_dim=1536,
        )
        assert trigger == ReembeddingTrigger.NONE

    def test_all_defaults_returns_none(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(current_model="m", new_model="m", collection_size=0)
        assert trigger == ReembeddingTrigger.NONE


# ── Trigger priority when multiple conditions fire simultaneously ────────────


class TestTriggerPriorityOrdering:
    def test_model_changed_wins_over_dimension_mismatch(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="old-model",
            new_model="new-model",
            collection_size=10,
            old_dim=1536,
            new_dim=3072,
        )
        assert trigger == ReembeddingTrigger.MODEL_CHANGED

    def test_model_changed_wins_over_drift_and_stale(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="old-model",
            new_model="new-model",
            collection_size=500,
            drift_score=0.9,
            age_days=365,
        )
        assert trigger == ReembeddingTrigger.MODEL_CHANGED

    def test_dimension_mismatch_wins_over_drift(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m",
            new_model="m",
            collection_size=500,
            old_dim=1536,
            new_dim=3072,
            drift_score=0.9,
        )
        assert trigger == ReembeddingTrigger.DIMENSION_MISMATCH

    def test_dimension_mismatch_wins_over_stale(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m",
            new_model="m",
            collection_size=500,
            old_dim=1536,
            new_dim=3072,
            age_days=365,
        )
        assert trigger == ReembeddingTrigger.DIMENSION_MISMATCH

    def test_drift_wins_over_stale(self, policy: ReembeddingPolicy) -> None:
        trigger = policy.should_reembed(
            current_model="m",
            new_model="m",
            collection_size=500,
            drift_score=0.9,
            age_days=365,
        )
        assert trigger == ReembeddingTrigger.DRIFT_DETECTED

    def test_all_four_triggers_simultaneously_model_changed_wins(
        self, policy: ReembeddingPolicy
    ) -> None:
        trigger = policy.should_reembed(
            current_model="old-model",
            new_model="new-model",
            collection_size=500,
            old_dim=1536,
            new_dim=3072,
            drift_score=0.9,
            age_days=365,
        )
        assert trigger == ReembeddingTrigger.MODEL_CHANGED
