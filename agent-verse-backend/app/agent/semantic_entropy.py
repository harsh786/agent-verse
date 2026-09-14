"""Semantic-entropy hallucination signal (Hallucination T5).

Kuhn et al.: sample an answer N times at T>0, cluster the samples by *meaning*
(not surface form), and measure the entropy over meaning-clusters. Low entropy =
the model consistently says the same thing (confident); high entropy = it
disagrees with itself across samples (a hallucination signal). The LLM sampling
is cost-gated and lives at the call site; this module is the pure, deterministic
core: cluster equivalent answers and compute normalized entropy.

Clustering uses a pluggable equivalence predicate (default: normalized-text
equality). A semantic predicate (embedding cosine, or an NLI "mutually entails"
check) can be injected without changing the entropy math.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable

EquivFn = Callable[[str, str], bool]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?")


def default_equivalence(a: str, b: str) -> bool:
    return _normalize(a) == _normalize(b)


def cluster_samples(samples: list[str], *, equivalent: EquivFn | None = None) -> list[list[str]]:
    """Group samples into meaning-clusters using the equivalence predicate."""
    eq = equivalent or default_equivalence
    clusters: list[list[str]] = []
    for sample in samples:
        placed = False
        for cluster in clusters:
            if eq(sample, cluster[0]):
                cluster.append(sample)
                placed = True
                break
        if not placed:
            clusters.append([sample])
    return clusters


def semantic_entropy(samples: list[str], *, equivalent: EquivFn | None = None) -> float:
    """Normalized semantic entropy in [0, 1] over meaning-clusters.

    0.0 = all samples mean the same thing (confident); 1.0 = maximally split.
    Fewer than 2 samples → 0.0 (no disagreement measurable).
    """
    usable = [s for s in samples if s and s.strip()]
    if len(usable) < 2:
        return 0.0
    clusters = cluster_samples(usable, equivalent=equivalent)
    if len(clusters) <= 1:
        return 0.0
    total = len(usable)
    entropy = -sum(
        (len(c) / total) * math.log(len(c) / total) for c in clusters
    )
    # Normalize by the maximum possible entropy (all samples distinct).
    max_entropy = math.log(total)
    return round(entropy / max_entropy, 4) if max_entropy > 0 else 0.0


def is_high_entropy(
    samples: list[str],
    *,
    threshold: float = 0.5,
    equivalent: EquivFn | None = None,
) -> bool:
    """True when semantic entropy exceeds ``threshold`` — a hallucination signal."""
    return semantic_entropy(samples, equivalent=equivalent) > threshold
