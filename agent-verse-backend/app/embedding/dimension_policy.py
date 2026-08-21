"""DimensionPolicy — maps model IDs to standard vector dimensions."""

from __future__ import annotations

_DIMENSION_MAP: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "voyage-3-lite": 1024,
    "voyage-code-3": 1024,
    "voyage-multimodal-3": 1024,
    "fake-embedding": 10,
}


class DimensionPolicy:
    def select(self, model_id: str) -> int:
        return _DIMENSION_MAP.get(model_id, 1536)
