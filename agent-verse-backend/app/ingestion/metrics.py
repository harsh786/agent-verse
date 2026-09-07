"""Prometheus metrics for the ingestion pipeline (LAW-12).

Mirrors the graceful-degradation pattern in app/triggers/metrics.py: when
prometheus_client is unavailable the counters become no-ops so the pipeline
never fails on a missing optional dependency.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter

    INGEST_DOCS_TOTAL = Counter(
        "agentverse_ingest_docs_total",
        "Documents processed by the ingestion pipeline",
        ["source_type", "status"],
    )

    INGEST_CHUNKS_TOTAL = Counter(
        "agentverse_ingest_chunks_total",
        "Chunks created and indexed by the ingestion pipeline",
        ["source_type"],
    )

    METRICS_AVAILABLE = True

except ImportError:
    METRICS_AVAILABLE = False

    class _Noop:
        def labels(self, **_: object) -> _Noop:
            return self

        def inc(self, *_: object) -> None: ...

    INGEST_DOCS_TOTAL = _Noop()  # type: ignore[assignment]
    INGEST_CHUNKS_TOTAL = _Noop()  # type: ignore[assignment]
