"""LargePayloadStore — offloads step outputs > threshold to object storage.

Keeps the LangGraph state slim (< 64 KB by default) by writing large outputs
to S3-compatible storage (MinIO in dev, S3 in prod) and storing only a
``{"__ref__": "s3://bucket/key"}`` pointer in the state.

When the workflow engine needs to read the payload back it calls
``LargePayloadStore.resolve_ref(value)`` which fetches from S3 transparently.

Falls back to in-memory dict when no S3 config is present (unit tests / dev).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

_DEFAULT_THRESHOLD = 32 * 1024  # 32 KB
_REF_KEY = "__ref__"


class LargePayloadStore:
    """Store large step outputs outside of LangGraph state."""

    def __init__(
        self,
        s3_client: Any | None = None,
        bucket: str = "agentverse-workflow-payloads",
        threshold_bytes: int = _DEFAULT_THRESHOLD,
    ) -> None:
        self._s3 = s3_client
        self._bucket = bucket
        self._threshold = threshold_bytes
        # In-memory fallback for tests / dev
        self._mem: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def store_if_large(
        self,
        run_id: str,
        step_id: str,
        value: Any,
    ) -> Any:
        """Return *value* unchanged if small, else store and return a ref pointer."""
        raw = self._serialize(value)
        if len(raw) <= self._threshold:
            return value

        key = f"runs/{run_id}/steps/{step_id}/{uuid.uuid4().hex}.json"
        await self._put(key, raw)
        return {_REF_KEY: f"s3://{self._bucket}/{key}"}

    async def resolve_ref(self, value: Any) -> Any:
        """If *value* is a ref pointer, fetch and deserialise; else return as-is."""
        if not isinstance(value, dict) or _REF_KEY not in value:
            return value
        ref: str = value[_REF_KEY]
        # Expected format: s3://bucket/key
        without_scheme = ref.removeprefix("s3://")
        parts = without_scheme.split("/", 1)
        if len(parts) != 2:
            _log.warning("large_payload_bad_ref", ref=ref)
            return value
        _bucket, key = parts
        raw = await self._get(key)
        return json.loads(raw)

    # ------------------------------------------------------------------
    # S3 / in-memory backend
    # ------------------------------------------------------------------

    async def _put(self, key: str, raw: bytes) -> None:
        if self._s3 is not None:
            try:
                await self._s3.put_object(Bucket=self._bucket, Key=key, Body=raw)
                _log.debug("large_payload_stored", key=key, size=len(raw))
                return
            except Exception as exc:
                _log.warning("large_payload_s3_write_failed", key=key, error=str(exc))
        # Fallback: in-memory
        self._mem[key] = raw

    async def _get(self, key: str) -> bytes:
        if self._s3 is not None:
            try:
                resp = await self._s3.get_object(Bucket=self._bucket, Key=key)
                body = resp["Body"]
                if hasattr(body, "read"):
                    data: bytes = (
                        await body.read()  # type: ignore[misc,no-any-return]
                        if hasattr(body.read, "__await__")
                        else body.read()  # type: ignore[no-any-return]
                    )
                    return data
            except Exception as exc:
                _log.warning("large_payload_s3_read_failed", key=key, error=str(exc))
        return self._mem.get(key, b"null") or b"null"

    @staticmethod
    def _serialize(value: Any) -> bytes:
        try:
            return json.dumps(value, default=str).encode()
        except Exception:
            return str(value).encode()
