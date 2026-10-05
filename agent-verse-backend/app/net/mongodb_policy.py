"""Shared MongoDB safety policy (MCP builtin connector, tool-risk gate, ingestion).

* :func:`pipeline_is_read_only` — an aggregation pipeline with no write stage
  (``$out`` / ``$merge``) at any depth (``$facet``, ``$lookup`` / ``$unionWith``
  sub-pipelines). Anything that cannot be verified is NOT read-only.
"""

from __future__ import annotations

from typing import Any

# Aggregation stages that write data (to this or another database).
WRITE_STAGES = frozenset({"$out", "$merge"})


def _contains_key(value: Any, keys: frozenset[str], depth: int = 0) -> bool:
    if depth > 64:  # pathological nesting: treat as unverifiable
        return True
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.strip() in keys:
                return True
            if _contains_key(item, keys, depth + 1):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(_contains_key(item, keys, depth + 1) for item in value)
    return False


def pipeline_is_read_only(pipeline: object) -> bool:
    """True only for a well-formed pipeline (list of stage dicts) with no write stage."""
    if not isinstance(pipeline, list):
        return False
    if not all(isinstance(stage, dict) for stage in pipeline):
        return False
    return not _contains_key(pipeline, WRITE_STAGES)
