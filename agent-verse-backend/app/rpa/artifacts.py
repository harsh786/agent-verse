"""Artifact storage for RPA outputs — filesystem fallback and MinIO/S3 backend."""

from __future__ import annotations

import os as _os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ArtifactStoreProtocol(Protocol):
    """Common interface for all artifact backends."""

    async def write_bytes(
        self, *, goal_id: str, name: str, content: bytes
    ) -> RPAArtifact: ...

    async def read_bytes(self, *, artifact_id: str) -> bytes: ...

    async def delete(self, *, artifact_id: str) -> bool: ...


@dataclass
class RPAArtifact:
    """Stored RPA artifact reference."""

    artifact_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    uri: str = ""
    path: str = ""
    name: str = ""
    size_bytes: int = 0


class RPAArtifactStore:
    """Store artifacts under /tmp for CI-safe local RPA workflows."""

    def __init__(self, base_dir: Path | str = "/tmp/agentverse-rpa") -> None:
        self.base_dir = Path(base_dir)

    def write_bytes(self, *, goal_id: str, name: str, content: bytes) -> RPAArtifact:
        safe_goal_id = _safe_path_component(goal_id, default="goal")
        safe_name = _safe_path_component(name, default="artifact.bin")
        base_dir = self.base_dir
        base_dir.mkdir(parents=True, exist_ok=True)
        base_dir_resolved = base_dir.resolve()
        goal_dir = base_dir / safe_goal_id
        goal_dir.mkdir(parents=True, exist_ok=True)
        path = goal_dir / safe_name
        if not path.resolve().is_relative_to(base_dir_resolved):
            raise ValueError("RPA artifact path escapes base directory")
        path.write_bytes(content)
        return RPAArtifact(uri=path.as_uri(), path=str(path), name=safe_name, size_bytes=len(content))


def _safe_path_component(value: str, *, default: str) -> str:
    component = Path(value.replace("\\", "/")).name
    if component in {"", ".", ".."}:
        return default
    return component


# ── MinIO / S3-compatible backend ─────────────────────────────────────────────


class MinIOArtifactStore:
    """S3-compatible artifact store using MinIO (open source, runs locally).

    MinIO is included in docker-compose.yml and is API-compatible with S3.
    This works with AWS S3, GCS (via S3 compat), and MinIO for local dev.

    No proprietary cloud dependency — aioboto3 works with any S3-compatible storage.
    """

    def __init__(
        self,
        bucket: str = "agentverse-artifacts",
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        prefix: str = "",
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url or _os.getenv(
            "MINIO_ENDPOINT", "http://minio:9000"
        )
        self._access_key = access_key or _os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        self._secret_key = secret_key or _os.getenv("MINIO_SECRET_KEY", "minioadmin")
        self._prefix = prefix.rstrip("/")

    def _key(self, artifact_id: str, name: str) -> str:
        parts = [self._prefix, artifact_id, name]
        return "/".join(p for p in parts if p)

    async def _get_client(self) -> Any:
        """Create an aioboto3 S3 client configured for MinIO."""
        try:
            import aioboto3

            session = aioboto3.Session()
            return session.client(
                "s3",
                endpoint_url=self._endpoint_url,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name="us-east-1",  # MinIO ignores region but boto3 requires it
            )
        except ImportError:
            raise RuntimeError(
                "aioboto3 not installed. Run: pip install aioboto3\n"
                "For local development, MinIO is included in docker-compose.yml"
            )

    async def _ensure_bucket(self, client: Any) -> None:
        try:
            await client.head_bucket(Bucket=self._bucket)
        except Exception:
            try:
                await client.create_bucket(Bucket=self._bucket)
            except Exception:
                pass

    async def write_bytes(
        self,
        *,
        goal_id: str,
        name: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> RPAArtifact:
        """Upload bytes to MinIO/S3. Returns RPAArtifact with storage URI."""
        artifact_id = uuid.uuid4().hex
        key = self._key(artifact_id, name)
        try:
            async with await self._get_client() as client:
                await self._ensure_bucket(client)
                await client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=content,
                    ContentType=content_type,
                )
            uri = f"s3://{self._bucket}/{key}"
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("minio_write_failed", error=str(exc))
            # Fallback to /tmp
            fallback = _RPAArtifactStoreFallback()
            return await fallback.write_bytes(goal_id=goal_id, name=name, content=content)

        return RPAArtifact(
            artifact_id=artifact_id,
            uri=uri,
            path=key,
            name=name,
            size_bytes=len(content),
        )

    async def read_bytes(self, *, artifact_id: str, name: str = "") -> bytes:
        key = self._key(artifact_id, name)
        try:
            async with await self._get_client() as client:
                response = await client.get_object(Bucket=self._bucket, Key=key)
                return await response["Body"].read()
        except Exception:
            return b""

    async def presign_url(
        self, *, artifact_id: str, name: str, expires_seconds: int = 3600
    ) -> str:
        """Generate a pre-signed URL for direct download."""
        key = self._key(artifact_id, name)
        try:
            async with await self._get_client() as client:
                url = await client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": key},
                    ExpiresIn=expires_seconds,
                )
                return url
        except Exception:
            return ""


class _RPAArtifactStoreFallback:
    """Fallback to /tmp when MinIO is not available (CI, local dev without Docker)."""

    async def write_bytes(
        self, *, goal_id: str, name: str, content: bytes
    ) -> RPAArtifact:
        artifact_id = uuid.uuid4().hex
        base = Path(f"/tmp/agentverse-rpa/{goal_id}/{artifact_id}")
        base.mkdir(parents=True, exist_ok=True)
        path = base / _safe_name(name)
        path.write_bytes(content)
        return RPAArtifact(
            artifact_id=artifact_id,
            uri=path.as_uri(),
            path=str(path),
            name=name,
            size_bytes=len(content),
        )


def _safe_name(name: str) -> str:
    import re

    return re.sub(r"[^\w.\-]", "_", name)[:100]


def get_artifact_store(
    use_minio: bool | None = None,
) -> Any:
    """Factory: return MinIOArtifactStore if configured, fallback to /tmp."""
    endpoint = _os.getenv("MINIO_ENDPOINT", "")
    if use_minio is True or (use_minio is None and endpoint):
        return MinIOArtifactStore()
    return _RPAArtifactStoreFallback()
