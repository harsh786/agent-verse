"""Hallucination T5 — semantic entropy core (pure clustering + entropy)."""
from __future__ import annotations

from app.agent.semantic_entropy import (
    cluster_samples,
    is_high_entropy,
    semantic_entropy,
)


def test_consistent_samples_have_zero_entropy() -> None:
    samples = ["The capital is Paris.", "the capital is paris", "The capital is Paris"]
    assert semantic_entropy(samples) == 0.0
    assert is_high_entropy(samples) is False


def test_fully_split_samples_have_max_entropy() -> None:
    samples = ["Answer A", "Answer B", "Answer C", "Answer D"]
    assert semantic_entropy(samples) == 1.0
    assert is_high_entropy(samples) is True


def test_partial_disagreement_is_between() -> None:
    samples = ["Paris", "Paris", "Paris", "London"]  # 1 dissenter
    e = semantic_entropy(samples)
    assert 0.0 < e < 1.0


def test_fewer_than_two_samples_is_zero() -> None:
    assert semantic_entropy([]) == 0.0
    assert semantic_entropy(["only one"]) == 0.0


def test_custom_equivalence_predicate_clusters_semantically() -> None:
    # A semantic predicate can merge surface-different-but-equivalent answers.
    def same_number(a: str, b: str) -> bool:
        return "".join(c for c in a if c.isdigit()) == "".join(c for c in b if c.isdigit())

    samples = ["The total is 42.", "42 items found.", "There are 42."]
    assert len(cluster_samples(samples, equivalent=same_number)) == 1
    assert semantic_entropy(samples, equivalent=same_number) == 0.0
