"""Redis-backed persistence for user-registered ('configured') model overrides.

The seeder auto-registers models from env at startup; this store persists models
added/overridden via the model-registry UI so they survive restarts and are
visible to every process (API + Celery workers) that seeds the registry.

Key: ``model_registry:configured`` → JSON list of endpoint dicts.
Synchronous Redis is used so the seeder (which runs in sync contexts too) can
load overrides without an event loop.
"""

from __future__ import annotations

import json
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_KEY = "model_registry:configured"

# Whitelisted fields persisted per endpoint (mirrors ModelEndpoint).
_FIELDS = (
    "provider",
    "model_id",
    "display_name",
    "capabilities",
    "context_window",
    "max_output_tokens",
    "cost_per_1k_input",
    "cost_per_1k_output",
    "supports_tools",
    "supports_vision",
    "supports_structured_output",
    "quality_score",
    "avg_latency_ms",
    "is_available",
)


class ModelRegistryStore:
    """Persists user-registered model overrides in Redis (sync client)."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    def list(self) -> list[dict[str, Any]]:
        try:
            raw = self._redis.get(_KEY)
            if not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("model_registry_store_read_failed error=%s", str(exc)[:120])
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self._redis.set(_KEY, json.dumps(items))

    def upsert(self, endpoint: dict[str, Any]) -> None:
        """Add or replace an override, keyed by provider/model_id."""
        clean = {k: endpoint[k] for k in _FIELDS if k in endpoint}
        key = (clean.get("provider"), clean.get("model_id"))
        items = [
            e for e in self.list() if (e.get("provider"), e.get("model_id")) != key
        ]
        items.append(clean)
        self._save(items)

    def remove(self, provider: str, model_id: str) -> bool:
        items = self.list()
        kept = [
            e for e in items if (e.get("provider"), e.get("model_id")) != (provider, model_id)
        ]
        if len(kept) == len(items):
            return False
        self._save(kept)
        return True


# ── Module-level singleton (wired from create_app when Redis is available) ──────
_store: ModelRegistryStore | None = None


def get_model_registry_store() -> ModelRegistryStore | None:
    return _store


def set_model_registry_store(store: ModelRegistryStore) -> None:
    global _store
    _store = store
