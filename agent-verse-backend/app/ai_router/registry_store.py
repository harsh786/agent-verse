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
# Per-capability preference order: JSON object {capability: ["provider/model_id", ...]}.
_PREFERENCE_KEY = "model_registry:preferences"

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
    "origin",  # "catalog" for catalog imports; absent = added by the operator
    # Optional OpenAI-compatible server for this model (vLLM / Ollama / on-prem),
    # e.g. http://192.168.63.104:30080/v1 — see app.ai_router.model_endpoints.
    "base_url",
    # The endpoint's credential, encrypted by the credential vault
    # (app.providers.vault) — never stored or returned in plaintext.
    "api_key_encrypted",
    # An embedding model's REAL output width, probed by "Test connection" or on
    # first use (app.providers.registry_embedder) — the dimension-safety input.
    "dimensions",
    # The output width the operator REQUESTS from an embedding model that can
    # shorten its vectors (OpenAI text-embedding-3-*, Gemini gemini-embedding-001):
    # sent as ``dimensions`` on /embeddings. Absent = the model's native width.
    "output_dimensions",
    # Thinking-model control (app.providers.openai_compatible): "auto" (default
    # when absent), "off" or "on", plus an optional reasoning-token budget used
    # with "on".
    "thinking",
    "thinking_budget_tokens",
)
# Embedding widths probed before the model was saved: {"model_id|base_url": dims}.
_PROBED_DIMS_KEY = "model_registry:probed_dimensions"


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

    def upsert_many(self, endpoints: list[dict[str, Any]], *, overwrite: bool = False) -> int:
        """Add several overrides in one write; returns how many were added.

        Existing provider/model_id entries are kept unless *overwrite* (an
        operator's edited price or capabilities survive a catalog re-import).
        """
        items = self.list()
        index = {(e.get("provider"), e.get("model_id")): i for i, e in enumerate(items)}
        added = 0
        for endpoint in endpoints:
            clean = {k: endpoint[k] for k in _FIELDS if k in endpoint}
            key = (clean.get("provider"), clean.get("model_id"))
            if key in index:
                if overwrite:
                    items[index[key]] = clean
                continue
            index[key] = len(items)
            items.append(clean)
            added += 1
        if added or overwrite:
            self._save(items)
        return added

    def get(self, provider: str, model_id: str) -> dict[str, Any] | None:
        """The persisted override for provider/model_id, or ``None``."""
        for e in self.list():
            if (e.get("provider"), e.get("model_id")) == (provider, model_id):
                return e
        return None

    # ── Probed embedding dimensions ───────────────────────────────────────────

    @staticmethod
    def _probe_key(model_id: str, base_url: str | None) -> str:
        return f"{model_id}|{(base_url or '').strip().rstrip('/')}"

    def probed_dimension(self, model_id: str, base_url: str | None) -> int | None:
        """The width a probe measured for *model_id* at *base_url*, or ``None``."""
        try:
            raw = self._redis.get(_PROBED_DIMS_KEY)
            data = json.loads(raw) if raw else {}
        except Exception as exc:
            logger.warning("model_registry_probed_dims_read_failed error=%s", str(exc)[:120])
            return None
        value = data.get(self._probe_key(model_id, base_url)) if isinstance(data, dict) else None
        return value if isinstance(value, int) and value > 0 else None

    def record_dimension(self, model_id: str, base_url: str | None, dimensions: int) -> bool:
        """Remember a measured embedding width for *model_id* at *base_url*.

        Kept in the probe map (a model tested before it is saved picks it up on
        save) and written onto every saved override of that model at that
        endpoint. Returns True when an override changed (the shared version is
        bumped, so every replica / worker re-seeds with the new width).
        """
        if not isinstance(dimensions, int) or dimensions <= 0:
            return False
        try:
            raw = self._redis.get(_PROBED_DIMS_KEY)
            data = json.loads(raw) if raw else {}
            data = data if isinstance(data, dict) else {}
            data[self._probe_key(model_id, base_url)] = dimensions
            self._redis.set(_PROBED_DIMS_KEY, json.dumps(data))
        except Exception as exc:
            logger.warning("model_registry_probed_dims_write_failed error=%s", str(exc)[:120])
        wanted = (base_url or "").strip().rstrip("/")
        items = self.list()
        changed = False
        for e in items:
            if e.get("model_id") != model_id:
                continue
            if (str(e.get("base_url") or "").strip().rstrip("/")) != wanted:
                continue
            if e.get("dimensions") != dimensions:
                e["dimensions"] = dimensions
                changed = True
        if changed:
            self._save(items)
        return changed

    # ── Per-capability preference order (deployment-wide) ─────────────────────
    # Bumps the shared version too, so every replica / worker re-reads it.

    def get_preferences(self) -> dict[str, list[str]]:
        try:
            raw = self._redis.get(_PREFERENCE_KEY)
            data = json.loads(raw) if raw else {}
        except Exception as exc:
            logger.warning("model_registry_preferences_read_failed error=%s", str(exc)[:120])
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(cap): [str(k) for k in order if isinstance(k, str)]
            for cap, order in data.items()
            if isinstance(order, list)
        }

    def set_preference(self, capability: str, order: list[str]) -> None:
        prefs = self.get_preferences()
        if order:
            prefs[capability] = list(order)
        else:
            prefs.pop(capability, None)
        self._redis.set(_PREFERENCE_KEY, json.dumps(prefs))
        self._redis.incr(_VERSION_KEY)

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
