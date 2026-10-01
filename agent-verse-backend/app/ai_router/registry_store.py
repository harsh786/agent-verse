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
_POLICY_KEY_PREFIX = "model_registry:route_policies:"
_HEALTH_KEY_PREFIX = "model_registry:health:"
_CIRCUIT_KEY_PREFIX = "model_registry:provider_circuit:"
_SHADOW_LOG_KEY = "model_registry:shadow_log"
_SHADOW_LOG_MAX = 200
# Bumped on every override change so every replica / worker re-seeds (PROV-17).
_VERSION_KEY = "model_registry:configured:version"

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
        self._redis.incr(_VERSION_KEY)

    def version(self) -> int | None:
        """The override-set version (0 = never changed); ``None`` when unreadable."""
        try:
            raw = self._redis.get(_VERSION_KEY)
            return int(raw or 0)
        except Exception as exc:
            logger.warning("model_registry_version_read_failed error=%s", str(exc)[:120])
            return None

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


    # ── Tenant routing policies (PUT /models/routing-policies/{task_type}) ──────
    # Per-tenant JSON object ``{task_type: policy_dict}``. They used to live only in
    # the API process's ModelRegistry, so another replica / the Celery worker never
    # saw them. Reads and writes raise on a Redis error (callers decide).

    @staticmethod
    def _policy_key(tenant_id: str) -> str:
        return f"{_POLICY_KEY_PREFIX}{tenant_id}"

    def get_route_policies(self, tenant_id: str) -> dict[str, dict[str, Any]]:
        raw = self._redis.get(self._policy_key(tenant_id))
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    def set_route_policy(self, tenant_id: str, task_type: str, policy: dict[str, Any]) -> None:
        policies = self.get_route_policies(tenant_id)
        policies[task_type] = policy
        self._redis.set(self._policy_key(tenant_id), json.dumps(policies))

    # ── Provider health (shared by every API replica and worker) ──────────────

    def get_health(self, provider: str) -> dict[str, Any] | None:
        raw = self._redis.get(f"{_HEALTH_KEY_PREFIX}{provider}")
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None

    def set_health(self, provider: str, health: dict[str, Any]) -> None:
        self._redis.set(f"{_HEALTH_KEY_PREFIX}{provider}", json.dumps(health))

    # ── Failover circuit state (ProviderHealthPolicy), shared across replicas ──

    def get_provider_circuit(self, provider: str) -> dict[str, Any] | None:
        raw = self._redis.get(f"{_CIRCUIT_KEY_PREFIX}{provider}")
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None

    def set_provider_circuit(self, provider: str, state: dict[str, Any]) -> None:
        self._redis.set(f"{_CIRCUIT_KEY_PREFIX}{provider}", json.dumps(state))

    # ── Shadow-evaluation log (PROV-24), newest last, capped ──────────────────

    def list_shadow_results(self) -> list[dict[str, Any]]:
        raw = self._redis.get(_SHADOW_LOG_KEY)
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []

    def push_shadow_result(self, entry: dict[str, Any]) -> None:
        items = [*self.list_shadow_results(), entry][-_SHADOW_LOG_MAX:]
        self._redis.set(_SHADOW_LOG_KEY, json.dumps(items))


# ── Module-level singleton (wired from create_app when Redis is available) ──────
_store: ModelRegistryStore | None = None


def get_model_registry_store() -> ModelRegistryStore | None:
    return _store


def set_model_registry_store(store: ModelRegistryStore) -> None:
    global _store
    _store = store
