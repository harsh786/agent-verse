"""P1-3: the named CORRECTIVE_RELEVANCE_THRESHOLD constant must be authoritative."""

from __future__ import annotations

import inspect

from app.rag.agentic.patterns.corrective import (
    CORRECTIVE_RELEVANCE_THRESHOLD,
    CorrectiveRAGPattern,
)


def test_execute_default_confidence_uses_named_constant():
    """FAILS TODAY: execute() defaults confidence_threshold to a divergent literal 0.5."""
    sig = inspect.signature(CorrectiveRAGPattern.execute)
    default = sig.parameters["confidence_threshold"].default
    assert default == CORRECTIVE_RELEVANCE_THRESHOLD, (
        "the execute() default must be the named constant, not a divergent literal"
    )
