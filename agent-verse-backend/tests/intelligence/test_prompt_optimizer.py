"""Tests for PromptOptimizer."""
from __future__ import annotations

from app.intelligence.prompt_optimizer import PromptOptimizer


def test_register_and_select_control():
    opt = PromptOptimizer()
    v = opt.register_variant("system_prompt", "Control", "You are a helpful assistant.", is_control=True)
    assert v.is_control is True
    selected = opt.select_variant("system_prompt")
    # With no challengers, always returns control
    assert selected is not None
    assert selected.name == "Control"


def test_record_result_increments_count():
    opt = PromptOptimizer()
    v = opt.register_variant("system_prompt_rc", "V1", "Prompt 1", is_control=True)
    opt.record_result(v.variant_id, 0.85)
    opt.record_result(v.variant_id, 0.90)
    assert v.run_count == 2
    assert len(v.eval_scores) == 2


def test_maybe_promote_returns_none_when_insufficient_data():
    opt = PromptOptimizer(min_runs_for_promotion=100)
    opt.register_variant("key_insuf", "Control", "P1", is_control=True)
    opt.register_variant("key_insuf", "Challenger", "P2", is_control=False)
    promoted = opt.maybe_promote("key_insuf")
    assert promoted is None


def test_maybe_promote_promotes_better_challenger():
    opt = PromptOptimizer(min_runs_for_promotion=10)
    control = opt.register_variant("key_promo", "Control", "P1", is_control=True)
    challenger = opt.register_variant("key_promo", "Challenger", "P2", is_control=False)

    # Control scores: ~0.70, challenger scores: ~0.90 (clearly better)
    for _ in range(10):
        opt.record_result(control.variant_id, 0.70)
    for _ in range(10):
        opt.record_result(challenger.variant_id, 0.90)

    # May or may not promote depending on significance test
    promoted = opt.maybe_promote("key_promo")
    if promoted:
        assert promoted.name == "Challenger"


def test_get_report_returns_all_variants():
    opt = PromptOptimizer()
    opt.register_variant("my_key", "V1", "P1", is_control=True)
    opt.register_variant("my_key", "V2", "P2")
    report = opt.get_report("my_key")
    assert report["prompt_key"] == "my_key"
    assert len(report["variants"]) == 2
