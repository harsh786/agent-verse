"""Shared MongoDB safety policy (MCP builtin connector, tool-risk gate, ingestion).

* :func:`pipeline_is_read_only` — an aggregation pipeline with no write stage
  (``$out`` / ``$merge``) at any depth (``$facet``, ``$lookup`` / ``$unionWith``
  sub-pipelines). Anything that cannot be verified is NOT read-only.
* :func:`assert_safe_mongo_arguments` (MDB-03) — refuses, anywhere in a tool
  call's filters, projections, update / inserted documents and pipelines, the
  stages that write (``$out``, ``$merge``), the operators that run server-side
  JavaScript (``$where``, ``$function``, ``$accumulator``, BSON ``Code`` values)
  and the stages that expose other sessions' operations on a shared cluster.
* :func:`assert_tls_not_weakened` (MDB-07 / C6) — the URI options and connector
  fields that switch TLS verification off, used by the MCP builtin AND the
  ingestion connector. Plain-text transport (``tls=false``) only with the
  dev-only ``MONGODB_ALLOW_NON_TLS`` setting, which production ignores.
"""

from __future__ import annotations

from collections.abc import Mapping
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


# ── TLS policy (MDB-07 / C6) ─────────────────────────────────────────────────

# URI options (lower-cased) that switch certificate / hostname / revocation
# checks off. Refused unless their value is explicitly false.
TLS_WEAKENING_URI_OPTIONS = frozenset(
    {
        "tlsinsecure",
        "tlsallowinvalidcertificates",
        "tlsallowinvalidhostnames",
        "tlsdisableocspendpointcheck",
        "tlsdisablecertificaterevocationcheck",
        "sslallowinvalidcertificates",
        "sslallowinvalidhostnames",
    }
)
# Legacy option whose only purpose is CERT_NONE / CERT_OPTIONAL: refused outright.
_REFUSED_URI_OPTIONS = frozenset({"ssl_cert_reqs"})
# Connector config fields with the same effect.
TLS_WEAKENING_FIELDS = (
    "tls_allow_invalid_certificates",
    "tls_allow_invalid_hostnames",
    "tls_insecure",
)
_FALSE = frozenset({"false", "0", "no", "off"})
_TRUE = frozenset({"true", "1", "yes", "on"})


class MongoTlsPolicyError(ValueError):
    """The connection would run with weakened (or no) TLS."""


def _is_false(value: object) -> bool:
    return value is False or str(value).strip().lower() in _FALSE


def _is_true(value: object) -> bool:
    return value is True or str(value).strip().lower() in _TRUE


def non_tls_allowed() -> bool:
    """The dev-only MONGODB_ALLOW_NON_TLS switch (never in production)."""
    try:
        from app.core.config import get_settings

        settings = get_settings()
    except Exception:
        return False  # unreadable settings: the strict policy
    return bool(settings.mongodb_allow_non_tls) and not settings.is_production


def assert_tls_not_weakened(uri: str, config: Mapping[str, Any] | None = None) -> None:
    """Raise :class:`MongoTlsPolicyError` when ``uri`` / ``config`` weaken TLS."""
    from urllib.parse import parse_qsl, urlsplit

    for key, value in parse_qsl(urlsplit(uri.strip()).query, keep_blank_values=True):
        lowered = key.strip().lower()
        if lowered in _REFUSED_URI_OPTIONS or (
            lowered in TLS_WEAKENING_URI_OPTIONS and not _is_false(value)
        ):
            raise MongoTlsPolicyError(
                f"MongoDB URI option '{key}' is not allowed: TLS certificate verification "
                "cannot be turned off; supply the server's CA as tls_ca_pem instead"
            )
        if lowered in ("tls", "ssl") and _is_false(value) and not non_tls_allowed():
            raise MongoTlsPolicyError(
                f"MongoDB URI option '{key}={value}' is not allowed: connections must use TLS"
            )
    for field in TLS_WEAKENING_FIELDS:
        if config and _is_true(config.get(field)):
            raise MongoTlsPolicyError(
                f"MongoDB setting '{field}' is not allowed: TLS certificate verification "
                "cannot be turned off; supply the server's CA as tls_ca_pem instead"
            )
    for field in ("tls", "ssl"):
        value = (config or {}).get(field)
        explicit_off = value is not None and str(value).strip() != "" and _is_false(value)
        if explicit_off and not non_tls_allowed():
            raise MongoTlsPolicyError(
                f"MongoDB setting '{field}: false' is not allowed: connections must use TLS"
            )
