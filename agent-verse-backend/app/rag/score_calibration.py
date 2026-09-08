"""Probability calibration for retrieval and rerank scores.

Retrieval and rerank strategies emit *raw* scores — cosine similarities,
Reciprocal-Rank-Fusion weights, or cross-encoder logits — whose scale is
strategy-specific and not comparable across queries. Downstream consumers
(the RAG scorecard, adaptive routing, HITL confidence gating) need a single,
strategy-agnostic notion of "how confident are we in this retrieval?".

This module maps raw scores onto a calibrated ``[0, 1]`` confidence:

* ``minmax``   — relative spread of a single result set (order-preserving).
* ``softmax``  — a probability distribution over candidates (sums to 1).
* ``logistic`` — an *absolute* confidence keyed off score magnitude, so a
  uniformly weak result set stays low even though its members are internally
  ordered. This is the method that lets us tell a strong retrieval from a weak
  one, which pure min-max normalisation cannot.

``retrieval_confidence`` aggregates a result set into one scalar the retrieval
layer can expose.

TODO(row6-wiring): ``app.rag.gateway._canonical_result`` should call
``retrieval_confidence`` over the reranked citation scores and stash the value
on the ``RAGExecutionResult`` (e.g. a ``retrieval_confidence`` field / metadata
key) so ``app.evals.runtime_scorecard.RuntimeScorecard.score`` — which already
reads ``retrieval_result.confidence`` for its ``retrieval_confidence`` scorecard
dimension — is fed a real calibrated value instead of a raw/absent score.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

CalibrationMethod = Literal["minmax", "softmax", "logistic"]

# Logistic calibration defaults. Tuned for scores already normalised to roughly
# [0, 1] (the rerank policy min-max normalises cross-encoder logits before
# blending), so the midpoint sits at 0.5 with a steep slope for clear
# separation. Callers working in a different scale can override both.
_LOGISTIC_MIDPOINT = 0.5
_LOGISTIC_STEEPNESS = 10.0


def _validate(raw_scores: Sequence[float]) -> list[float]:
    values = [float(score) for score in raw_scores]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("calibration inputs must be finite numbers")
    return values


def _minmax(values: list[float]) -> list[float]:
    lowest = min(values)
    highest = max(values)
    span = highest - lowest
    if span <= 0.0:
        # Every score is identical — there is nothing to spread. Return a
        # neutral 0.5 rather than an arbitrary 0.0/1.0 endpoint.
        return [0.5 for _ in values]
    return [(value - lowest) / span for value in values]


def _softmax(values: list[float], temperature: float) -> list[float]:
    if temperature <= 0.0:
        raise ValueError("softmax temperature must be positive")
    # Shift by the max for numerical stability before exponentiating.
    highest = max(values)
    exps = [math.exp((value - highest) / temperature) for value in values]
    total = sum(exps)
    if total <= 0.0:
        return [1.0 / len(values) for _ in values]
    return [value / total for value in exps]


def _logistic(values: list[float], midpoint: float, steepness: float) -> list[float]:
    return [1.0 / (1.0 + math.exp(-steepness * (value - midpoint))) for value in values]


def calibrate_scores(
    raw_scores: Sequence[float],
    *,
    method: CalibrationMethod = "minmax",
    temperature: float = 1.0,
    midpoint: float = _LOGISTIC_MIDPOINT,
    steepness: float = _LOGISTIC_STEEPNESS,
) -> list[float]:
    """Map raw retrieval/rerank scores onto calibrated ``[0, 1]`` confidences.

    The transform is monotonic (order-preserving) for every method and the
    output is always bounded to the unit interval.
    """

    values = _validate(raw_scores)
    if not values:
        return []
    if method == "minmax":
        return _minmax(values)
    if method == "softmax":
        return _softmax(values, temperature)
    if method == "logistic":
        return _logistic(values, midpoint, steepness)
    raise ValueError(f"unknown calibration method: {method!r}")


def retrieval_confidence(
    scores: Sequence[float],
    *,
    method: CalibrationMethod = "logistic",
    top_k: int = 3,
) -> float:
    """Aggregate a retrieval set's scores into one calibrated confidence.

    Defaults to ``logistic`` calibration so the result reflects the *absolute*
    strength of the best hits: a set of strong scores yields a high confidence
    and a set of weak scores a low one. Only the ``top_k`` strongest calibrated
    scores contribute, so a couple of strong hits are not diluted by a long
    weak tail.
    """

    values = _validate(scores)
    if not values:
        return 0.0
    calibrated = calibrate_scores(values, method=method)
    ranked = sorted(calibrated, reverse=True)
    window = ranked[: max(1, top_k)]
    return sum(window) / len(window)


__all__ = [
    "CalibrationMethod",
    "calibrate_scores",
    "retrieval_confidence",
]
