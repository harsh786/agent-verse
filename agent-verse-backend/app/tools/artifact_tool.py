"""General-purpose artifact creation tool — agents can save any file as a downloadable artifact."""
from __future__ import annotations
import uuid
from datetime import UTC, datetime
from typing import Any
from app.observability.logging import get_logger

logger = get_logger(__name__)


class ArtifactTool:
    """Save text/binary content as a downloadable artifact."""
    name = "save_artifact"
    description = (
        "Save content as a downloadable artifact file. "
        "Use for: CSV reports, markdown summaries, JSON exports, generated documents, logs."
    )

    def __init__(self, artifact_store: Any = None) -> None:
        self._store = artifact_store

    async def execute(
        self,
        *,
        name: str,
        content: str | bytes,
        content_type: str = "text/plain",
        tenant_id: str,
        goal_id: str = "",
        expires_hours: int = 168,  # 7 days default
    ) -> dict[str, Any]:
        artifact_id = uuid.uuid4().hex
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        now = datetime.now(UTC)

        artifact_url = f"/artifacts/{tenant_id}/{goal_id}/{artifact_id}/{name}"

        if self._store is not None:
            try:
                artifact_url = await self._store.write_bytes(
                    key=f"{tenant_id}/{goal_id}/{artifact_id}/{name}",
                    data=content_bytes,
                    content_type=content_type,
                )
            except Exception as exc:
                logger.warning("artifact_store_write_failed", error=str(exc))
                # Fall back to local tmp
                import os, pathlib
                local_dir = pathlib.Path(f"/tmp/agentverse-artifacts/{tenant_id}/{goal_id}/{artifact_id}")
                local_dir.mkdir(parents=True, exist_ok=True)
                local_path = local_dir / name
                local_path.write_bytes(content_bytes)
                artifact_url = f"file://{local_path}"

        return {
            "artifact_id": artifact_id,
            "filename": name,
            "content_type": content_type,
            "size_bytes": len(content_bytes),
            "artifact_url": artifact_url,
            "created_at": now.isoformat(),
            "goal_id": goal_id,
            "expires_hours": expires_hours,
        }

    def to_tool_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Filename including extension (e.g. 'report.csv', 'summary.md')"},
                    "content": {"type": "string", "description": "File content as text"},
                    "content_type": {"type": "string", "default": "text/plain",
                                     "description": "MIME type: text/plain, text/csv, application/json, text/markdown"},
                    "expires_hours": {"type": "integer", "default": 168, "description": "Hours until artifact expires"},
                },
                "required": ["name", "content"],
            },
        }
