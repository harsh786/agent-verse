"""In-memory binary artifact store for chat-generated files (Phase 4).

Holds generated documents (PDF/doc/sheet/…) so a chat turn can hand back a
downloadable file. Tenant-scoped. (A durable object-store backend can replace the
dict later without changing callers.)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredArtifact:
    id: str
    tenant_id: str
    filename: str
    mime: str
    content: bytes


class ChatArtifactStore:
    def __init__(self) -> None:
        self._items: dict[str, StoredArtifact] = {}

    def put(self, *, tenant_id: str, content: bytes, mime: str, filename: str) -> str:
        artifact_id = uuid.uuid4().hex
        self._items[artifact_id] = StoredArtifact(
            id=artifact_id, tenant_id=tenant_id, filename=filename, mime=mime, content=content
        )
        return artifact_id

    def get(self, artifact_id: str, tenant_id: str) -> StoredArtifact | None:
        art = self._items.get(artifact_id)
        return art if art is not None and art.tenant_id == tenant_id else None
