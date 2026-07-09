"""Artifact management for the execution environment.

STATUS: STUB — actual MinIO/S3 upload not yet implemented.
``make_artifact()`` computes a checksum and returns an ``ExecutionArtifact``
reference, but does NOT upload content to object storage.  The returned
``storage_url`` will be empty until the upload integration is added.

To implement:
1. Inject a MinIO/S3 client (from ``app.rpa.artifacts.get_artifact_store()``).
2. Call ``artifact_store.upload(content, name, goal_id, tenant_id)``.
3. Set ``storage_url`` to the returned presigned or internal URL.
4. Remove this warning comment once implemented.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

from app.execution_environment.models import ExecutionArtifact

logger = logging.getLogger(__name__)


def make_artifact(
    *,
    goal_id: str,
    tenant_id: str,
    name: str,
    content: bytes,
    mime_type: str = "application/octet-stream",
    storage_url: str = "",
) -> ExecutionArtifact:
    """Create an :class:`ExecutionArtifact` reference for a raw content blob.

    .. warning::
        This function does NOT persist ``content`` to object storage.
        ``storage_url`` defaults to ``""`` unless the caller provides one.
        Call ``artifact_store.upload()`` and pass the returned URL explicitly.
    """
    if not name:
        raise ValueError("artifact name must be non-empty")
    if not goal_id or not tenant_id:
        raise ValueError("goal_id and tenant_id must be non-empty")
    if not content:
        logger.warning(
            "make_artifact called with empty content name=%s goal_id=%s", name, goal_id
        )

    checksum = hashlib.sha256(content).hexdigest()

    if not storage_url:
        logger.warning(
            "make_artifact returning stub artifact with empty storage_url "
            "name=%s goal_id=%s — upload not yet implemented",
            name, goal_id,
        )

    return ExecutionArtifact(
        artifact_id=uuid.uuid4().hex,
        goal_id=goal_id,
        tenant_id=tenant_id,
        name=name,
        mime_type=mime_type,
        size_bytes=len(content),
        storage_url=storage_url,
        checksum_sha256=checksum,
        created_at=datetime.now(UTC).isoformat(),
    )


def validate_artifact_size(artifact: ExecutionArtifact, limit_bytes: int) -> bool:
    """Return True if the artifact is within the configured size limit."""
    return artifact.size_bytes <= limit_bytes
