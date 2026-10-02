"""Retrieval / answer quality and latency metrics for the real-world scenarios.

Pure functions (no HTTP, no ``app``), unit-tested offline in
``tests/real_world_harness``. Scenario tests put their numbers under
``evidence["metrics"]`` via :func:`record`; the report collects them per scenario.
"""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

_SPACES = (" ", " ", " ", " ")
_DASHES = ("‑", "‐", "–", "—")


def norm(text: Any) -> str:
    """Lower-case, typographic spaces/hyphens folded, whitespace collapsed."""
    s = str(text or "")
    for ch in _SPACES:
        s = s.replace(ch, " ")
    for ch in _DASHES:
        s = s.replace(ch, "-")
    s = s.replace("’", "'").replace("**", "")
    return " ".join(s.lower().split())


def source_of(hit: dict[str, Any]) -> str:
    """The document a search hit / citation came from (file name, URL or title)."""
    meta = hit.get("metadata") or {}
    for value in (hit.get("source_file"), meta.get("source_file"), meta.get("filename"),
                  hit.get("source"), meta.get("source"), hit.get("source_url"),
                  meta.get("source_url"), meta.get("title")):
        if value:
            return str(value)
    return ""


def source_matches(source: str, expected: Sequence[str]) -> bool:
    """``source`` names one of ``expected`` (exact, path suffix or archive member)."""
    s = source.lower()
    for name in expected:
        n = name.lower()
        if s == n or s.endswith("/" + n) or s.endswith(n) or (n in s and len(n) > 6):
            return True
    return False


def hit_is_correct(hit: dict[str, Any], expected_sources: Sequence[str],
                   must_contain: str) -> bool:
    """Right document AND right chunk (the chunk holds the planted fact)."""
    content = norm(hit.get("content"))
    return source_matches(source_of(hit), expected_sources) and norm(must_contain) in content


def rank_of(hits: Sequence[dict[str, Any]], expected_sources: Sequence[str],
            must_contain: str) -> int | None:
    """1-based rank of the first correct hit, or None."""
    for i, h in enumerate(hits):
        if hit_is_correct(h, expected_sources, must_contain):
            return i + 1
    return None


def hit_at_k(ranks: Sequence[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mrr(ranks: Sequence[int | None]) -> float:
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r) / len(ranks)


def answer_correct(answer: str, question: dict[str, Any]) -> bool:
    """The answer states the known fact (any accepted variant; all of ``answer_all``)
    and none of ``answer_forbidden``."""
    a = norm(answer)
    if not a:
        return False
    if any(norm(f) in a for f in question.get("answer_forbidden", [])):
        return False
    if not all(norm(x) in a for x in question.get("answer_all", [])):
        return False
    return any(norm(x) in a for x in question.get("answer_any", []))


def percentiles(values: Sequence[float]) -> dict[str, float]:
    """n / mean / p50 / p95 / max of a latency sample (seconds or ms, as given)."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return {"n": 0}

    def pct(p: float) -> float:
        idx = max(0, min(len(vals) - 1, math.ceil(p / 100 * len(vals)) - 1))
        return round(vals[idx], 3)

    return {"n": len(vals), "mean": round(statistics.fmean(vals), 3), "p50": pct(50),
            "p95": pct(95), "max": round(vals[-1], 3)}


class Stopwatch:
    """Collects named latency samples (ms)."""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = {}

    @contextmanager
    def time(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            self.samples.setdefault(name, []).append((time.monotonic() - started) * 1000)

    def add(self, name: str, ms: float) -> None:
        self.samples.setdefault(name, []).append(ms)

    def summary(self) -> dict[str, dict[str, float]]:
        return {k: percentiles(v) for k, v in self.samples.items()}


def record(evidence: dict[str, Any], **metrics: Any) -> None:
    """Merge numbers into ``evidence["metrics"]`` (rounded floats)."""
    bucket = evidence.setdefault("metrics", {})
    for k, v in metrics.items():
        bucket[k] = round(v, 4) if isinstance(v, float) else v


def throughput(count: int, seconds: float) -> float:
    return round(count / seconds, 3) if seconds > 0 else 0.0
