"""Standard error response utilities."""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse


def error_response(
    status_code: int,
    message: str,
    request: Request | None = None,
) -> JSONResponse:
    """Return a standardized error response with a correlation_id.

    The correlation_id is taken from ``request.state.correlation_id`` when
    available (set by upstream middleware) or generated as a short UUID fragment
    so operators can cross-reference logs without exposing internal details.
    """
    correlation_id = getattr(getattr(request, "state", None), "correlation_id", None)
    if not correlation_id:
        correlation_id = str(uuid.uuid4())[:8]
        # No upstream middleware has set one yet — mint it here and persist it
        # onto request.state so any *other* error helper called later in the
        # same request (e.g. a second failure during exception handling)
        # reuses this exact id instead of minting a fresh, uncorrelated one.
        if request is not None:
            request.state.correlation_id = correlation_id
    return JSONResponse(
        status_code=status_code,
        content={
            "error": message,
            "correlation_id": correlation_id,
        },
    )
