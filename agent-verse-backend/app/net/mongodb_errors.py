"""Tenant-facing MongoDB driver errors (MDB-20).

pymongo's errors carry the full ``TopologyDescription`` (every member address,
round-trip times, nested socket errors) and server error messages echo the
command document (filter values). Those used to be returned to the tenant
verbatim — including internal member names a hostile server advertised.

:func:`public_mongo_error` maps a driver exception to a short, classified
message with an error id; the full detail is logged server-side under that id.
"""

from __future__ import annotations

import logging
import uuid

_log = logging.getLogger(__name__)

_TLS_MARKERS = ("certificate", "ssl", "tls", "handshake")
_GUARD_MARKER = "is not listed in the connector's uri"


def _classify(exc: BaseException) -> str:
    try:
        from pymongo import errors as pe
    except ImportError:  # pragma: no cover - pymongo is a dependency
        return "The MongoDB call failed"
    detail = str(exc).lower()
    if isinstance(exc, pe.DuplicateKeyError):
        return "Duplicate key: a document with this unique key already exists"
    if isinstance(exc, pe.ExecutionTimeout | pe.NetworkTimeout | pe.WTimeoutError):
        return "The MongoDB operation timed out"
    if isinstance(exc, pe.OperationFailure):
        code = getattr(exc, "code", None)
        details = getattr(exc, "details", None) or {}
        name = str(details.get("codeName") or "") if isinstance(details, dict) else ""
        if code == 18 or name == "AuthenticationFailed":
            return "Authentication failed: check the username, password and auth source"
        if code == 13 or name == "Unauthorized":
            return (
                "Not authorized: the connector's database user lacks the privilege for "
                "this operation on this database"
            )
        if code == 50 or name == "MaxTimeMSExpired":
            return "The MongoDB operation timed out"
        return f"MongoDB refused the operation ({name or f'code {code}'})"
    if isinstance(exc, pe.ConnectionFailure):
        if _GUARD_MARKER in detail:
            return (
                "A replica-set member the server advertised is not listed in the "
                "connector's URI; the egress policy refuses to dial it (list every "
                "member, or use mongodb+srv)"
            )
        if any(marker in detail for marker in _TLS_MARKERS):
            return (
                "The TLS handshake with the MongoDB server failed (certificate not "
                "trusted, hostname mismatch, or TLS required): check tls, tls_ca_pem and "
                "the client certificate"
            )
        if "timed out" in detail or isinstance(exc, pe.ServerSelectionTimeoutError):
            return (
                "Could not reach the MongoDB server (connection refused or timed out, "
                "DNS, firewall, or no primary available)"
            )
        return "The connection to the MongoDB server failed"
    if isinstance(exc, pe.ConfigurationError):
        return "The MongoDB connection settings are invalid"
    if isinstance(exc, pe.PyMongoError):
        return f"MongoDB driver error ({type(exc).__name__})"
    try:
        from bson.errors import BSONError

        if isinstance(exc, BSONError):
            return f"A document could not be encoded or decoded ({type(exc).__name__})"
    except ImportError:  # pragma: no cover
        pass
    return "The MongoDB call failed"


def public_mongo_error(exc: BaseException, *, context: str) -> str:
    """A short classified message for the tenant; the detail is logged by error id."""
    error_id = uuid.uuid4().hex[:12]
    _log.warning(
        "mongodb_error error_id=%s context=%s type=%s detail=%s",
        error_id,
        context,
        type(exc).__name__,
        str(exc)[:4000],
    )
    return f"{_classify(exc)} (error id {error_id})"


__all__ = ["public_mongo_error"]
