"""Dedicated unit tests for PromptVariantSelector's own weighting/selection
logic (app/context/prompt_variant_selector.py).

Previously this class was only exercised indirectly through
tests/intelligence/test_prompt_variants_api.py, which actually tests a
different, unrelated variant CRUD/promotion system (challenger variants
registered with the self-optimizer) -- it never imports or calls
PromptVariantSelector.select() at all. This file is the first direct unit
coverage of the selector itself: its deterministic md5-hash-based selection,
tie-breaking (or rather, lack of ties -- selection is a pure function of
goal_id), and the no-variants-available fallback.
"""
from __future__ import annotations

import hashlib

from app.context.prompt_variant_selector import PromptVariant, PromptVariantSelector


class TestPromptVariantSelectorFallback:
    def test_empty_pool_returns_default_variant(self) -> None:
        selector = PromptVariantSelector()
        variant = selector.select("goal-1", [])
        assert isinstance(variant, PromptVariant)
        assert variant.variant_id == "default"
        assert variant.description == ""

    def test_empty_pool_fallback_is_independent_of_goal_id(self) -> None:
        """No variants available -> always 'default', regardless of goal_id."""
        selector = PromptVariantSelector()
        for goal_id in ["", "a", "goal-xyz", "🚀unicode-goal"]:
            variant = selector.select(goal_id, [])
            assert variant.variant_id == "default"


class TestPromptVariantSelectorWeighting:
    """The selector has no explicit weighting field per variant -- selection
    is a uniform hash-bucket over the pool (idx = md5(goal_id) % len(pool)).
    These tests pin down that actual mechanism directly."""

    def test_selection_matches_md5_modulo_formula(self) -> None:
        """The selector's index computation must match its documented
        deterministic md5-hash-mod-pool-size algorithm exactly."""
        selector = PromptVariantSelector()
        pool = ["variant-a", "variant-b", "variant-c"]
        for goal_id in ["goal-1", "another-goal", "z" * 40, ""]:
            expected_idx = int(hashlib.md5(goal_id.encode()).hexdigest(), 16) % len(pool)
            variant = selector.select(goal_id, pool)
            assert variant.variant_id == pool[expected_idx]

    def test_selection_is_deterministic_for_same_goal_id(self) -> None:
        """Same goal_id + same pool must always select the same variant --
        this is what makes it safe for sticky A/B assignment across
        replans/retries of the same goal."""
        selector = PromptVariantSelector()
        pool = ["control", "challenger-1", "challenger-2"]
        first = selector.select("goal-42", pool)
        for _ in range(10):
            again = selector.select("goal-42", pool)
            assert again.variant_id == first.variant_id

    def test_single_variant_pool_always_selects_that_variant(self) -> None:
        """A pool of exactly one variant must select it regardless of
        goal_id (idx = hash % 1 == 0 always)."""
        selector = PromptVariantSelector()
        for goal_id in ["a", "b", "totally-different-goal", ""]:
            variant = selector.select(goal_id, ["only-variant"])
            assert variant.variant_id == "only-variant"

    def test_distribution_covers_multiple_buckets_across_many_goal_ids(self) -> None:
        """Sanity check that selection isn't secretly constant/broken --
        across enough distinct goal_ids, more than one bucket of a
        multi-variant pool should get selected (true uniform-hash coverage,
        not weighted toward one variant)."""
        selector = PromptVariantSelector()
        pool = ["v0", "v1", "v2", "v3"]
        selected = {selector.select(f"goal-{i}", pool).variant_id for i in range(200)}
        assert len(selected) > 1, "hash-based selection should spread across buckets"
        assert selected <= set(pool)

    def test_duplicate_pool_entries_are_indexable_like_any_list(self) -> None:
        """The selector does no deduplication -- it is a plain index into
        whatever pool is handed to it, duplicates included."""
        selector = PromptVariantSelector()
        pool = ["dup", "dup", "unique"]
        variant = selector.select("goal-1", pool)
        assert variant.variant_id in pool

    def test_no_explicit_tie_breaking_needed_selection_is_a_pure_function(self) -> None:
        """There is no separate 'tie-break' branch in the implementation --
        each goal_id deterministically maps to exactly one index, so two
        different goal_ids landing on the same bucket both just resolve to
        that variant (not a special-cased tie)."""
        selector = PromptVariantSelector()
        pool = ["only-variant"]  # every goal_id collides into idx 0
        v1 = selector.select("goal-alpha", pool)
        v2 = selector.select("goal-beta", pool)
        assert v1.variant_id == v2.variant_id == "only-variant"


class TestPromptVariantDataclass:
    def test_variant_description_defaults_to_empty_string(self) -> None:
        variant = PromptVariant(variant_id="v1")
        assert variant.description == ""

    def test_selected_variant_from_pool_has_no_description(self) -> None:
        """select() never populates description -- only variant_id from the
        pool. Callers wanting richer variant metadata must look it up
        separately by variant_id."""
        selector = PromptVariantSelector()
        variant = selector.select("goal-1", ["v1", "v2"])
        assert variant.description == ""
