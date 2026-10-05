"""Shared MongoDB safety policy (MCP builtin connector, tool-risk gate, ingestion).

* :func:`pipeline_is_read_only` — an aggregation pipeline with no write stage
  (``$out`` / ``$merge``) at any depth (``$facet``, ``$lookup`` / ``$unionWith``
  sub-pipelines). Anything that cannot be verified is NOT read-only.
* :func:`assert_safe_mongo_arguments` (MDB-03) — refuses, anywhere in a tool
  call's filters, projections, update / inserted documents and pipelines, the
  stages that write (``$out``, ``$merge``), the operators that run server-side
  JavaScript (``$where``, ``$function``, ``$accumulator``, BSON ``Code`` values)
  and the stages that expose other sessions' operations on a shared cluster.
"""

from __future__ import annotations

from typing import Any

try:
    from bson.code import Code as _Code
except ImportError:  # pragma: no cover - pymongo ships bson
    _Code = None  # type: ignore[assignment,misc]

# Aggregation stages that write data (to this or another database).
WRITE_STAGES = frozenset({"$out", "$merge"})
# Operators that execute JavaScript on the server.
JAVASCRIPT_OPERATORS = frozenset({"$where", "$function", "$accumulator", "$code"})
# Stages that list other users' operations / sessions on the cluster.
CLUSTER_INTROSPECTION_STAGES = frozenset({"$currentOp", "$listSessions", "$listLocalSessions"})
FORBIDDEN_OPERATORS = WRITE_STAGES | JAVASCRIPT_OPERATORS | CLUSTER_INTROSPECTION_STAGES

_MAX_DEPTH = 64


class MongoOperatorError(ValueError):
    """A MongoDB tool call carries an operator the platform refuses to run."""


def _why(operator: str) -> str:
    if operator in WRITE_STAGES:
        return "it writes data from an aggregation"
    if operator in JAVASCRIPT_OPERATORS:
        return "it runs server-side JavaScript"
    return "it exposes other sessions on the cluster"


def _walk(value: Any, path: str, depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise MongoOperatorError(
            f"MongoDB tool arguments are nested too deeply at '{path}' (max {_MAX_DEPTH})"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if name.strip() in FORBIDDEN_OPERATORS:
                op = name.strip()
                raise MongoOperatorError(
                    f"MongoDB operator '{op}' is not allowed ({_why(op)}); found at '{path}.{name}'"
                )
            _walk(item, f"{path}.{name}", depth + 1)
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", depth + 1)
        return
    if _Code is not None and isinstance(value, _Code):
        raise MongoOperatorError(
            f"MongoDB JavaScript code values are not allowed (server-side JavaScript); "
            f"found at '{path}'"
        )


def assert_safe_mongo_arguments(arguments: dict[str, Any]) -> None:
    """Raise :class:`MongoOperatorError` for a refused operator anywhere in the call."""
    _walk(arguments, "arguments", 0)


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
