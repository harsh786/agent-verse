"""Tenant-scoped durable artifacts for isolated execution."""

from __future__ import annotations

import hashlib
import inspect
import posixpath
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Protocol

from app.execution_environment.models import ExecutionArtifact


class ExecutionArtifactStore(Protocol):
    async def put(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        workload_id: str,
        name: str,
        content: AsyncIterator[bytes],
        maximum_bytes: int,
    ) -> ExecutionArtifact: ...

    async def delete_workload(self, *, tenant_id: str, workload_id: str) -> None: ...


def validate_artifact_name(name: str) -> str:
    normalized = posixpath.normpath(name.replace("\\", "/"))
    path = PurePosixPath(normalized)
    if (
        not name
        or path.is_absolute()
        or normalized in {".", ".."}
        or ".." in path.parts
        or any(part.startswith(".") for part in path.parts)
    ):
        raise ValueError("artifact name must be a visible relative workspace path")
    return normalized


class DurableExecutionArtifactStore:
    """Adapter over the application's object store; never returns an empty reference."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._workload_artifacts: dict[tuple[str, str], list[str]] = {}

    async def put(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        workload_id: str,
        name: str,
        content: AsyncIterator[bytes],
        maximum_bytes: int,
    ) -> ExecutionArtifact:
        safe_name = validate_artifact_name(name)
        if not tenant_id or not goal_id or not workload_id or maximum_bytes <= 0:
            raise ValueError("artifact identity and maximum_bytes are required")
        chunks: list[bytes] = []
        size = 0
        async for chunk in content:
            size += len(chunk)
            if size > maximum_bytes:
                raise ValueError("artifact size limit exceeded")
            chunks.append(chunk)
        payload = b"".join(chunks)
        object_name = f"{tenant_id}/{workload_id}/{safe_name}"
        stored = self._backend.write_bytes(
            goal_id=goal_id, name=object_name, content=payload
        )
        if inspect.isawaitable(stored):
            stored = await stored
        storage_ref = str(getattr(stored, "uri", "") or getattr(stored, "path", ""))
        if not storage_ref:
            raise RuntimeError("artifact backend returned no durable reference")
        artifact_id = str(getattr(stored, "artifact_id", "") or uuid.uuid4().hex)
        self._workload_artifacts.setdefault((tenant_id, workload_id), []).append(artifact_id)
        return ExecutionArtifact(
            artifact_id=artifact_id,
            goal_id=goal_id,
            tenant_id=tenant_id,
            name=safe_name,
            size_bytes=size,
            storage_url=storage_ref,
            checksum_sha256=hashlib.sha256(payload).hexdigest(),
            created_at=datetime.now(UTC).isoformat(),
        )

    async def delete_workload(self, *, tenant_id: str, workload_id: str) -> None:
        for artifact_id in self._workload_artifacts.pop((tenant_id, workload_id), []):
            delete = getattr(self._backend, "delete", None)
            if delete is None:
                continue
            result = delete(artifact_id=artifact_id)
            if inspect.isawaitable(result):
                await result


def make_artifact(
    *,
    goal_id: str,
    tenant_id: str,
    name: str,
    content: bytes,
    mime_type: str = "application/octet-stream",
    storage_url: str = "",
) -> ExecutionArtifact:
    safe_name = validate_artifact_name(name)
    if not goal_id or not tenant_id or not storage_url:
        raise ValueError("goal_id, tenant_id, and durable storage_url are required")
    return ExecutionArtifact(
        artifact_id=uuid.uuid4().hex,
        goal_id=goal_id,
        tenant_id=tenant_id,
        name=safe_name,
        mime_type=mime_type,
        size_bytes=len(content),
        storage_url=storage_url,
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        created_at=datetime.now(UTC).isoformat(),
    )


def validate_artifact_size(artifact: ExecutionArtifact, limit_bytes: int) -> bool:
    return 0 <= artifact.size_bytes <= limit_bytes


__all__ = [
    "DurableExecutionArtifactStore",
    "ExecutionArtifactStore",
    "make_artifact",
    "validate_artifact_name",
    "validate_artifact_size",
]
