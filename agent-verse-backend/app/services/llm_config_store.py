"""Redis-backed LLM configuration store.

Stores per-tenant LLM provider configuration in Redis so Celery workers
(which have no access to the FastAPI ``app.state``) can read it without an
HTTP round-trip back to the API process.

Key format:
  ``llm_config:{tenant_id}`` → JSON with provider, encrypted_key, model, base_url

The *encrypted_key* field stores the vault-encrypted API key ciphertext — the
raw key is never written to Redis.  Workers decrypt it via
:func:`app.providers.vault.get_vault` before calling the LLM provider.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LLMConfigStore:
    """Reads and writes per-tenant LLM provider config to/from Redis.

    Args:
        redis_client: An ``redis.asyncio.Redis``-compatible async client.
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    def _key(self, tenant_id: str) -> str:
        return f"llm_config:{tenant_id}"

    async def set_config(
        self,
        tenant_id: str,
        provider: str,
        encrypted_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        """Store the LLM config for *tenant_id* in Redis."""
        config = {
            "provider": provider,
            "encrypted_key": encrypted_key,
            "model": model,
            "base_url": base_url,
        }
        try:
            await self._redis.set(self._key(tenant_id), json.dumps(config))
        except Exception as exc:
            logger.warning("Failed to store LLM config in Redis for %s: %s", tenant_id, exc)

    async def get_config(self, tenant_id: str) -> dict[str, Any] | None:
        """Return the LLM config for *tenant_id*, or *None* if not configured."""
        try:
            raw = await self._redis.get(self._key(tenant_id))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Failed to read LLM config from Redis for %s: %s", tenant_id, exc)
            return None

    async def delete_config(self, tenant_id: str) -> None:
        """Remove the LLM config for *tenant_id* from Redis."""
        try:
            await self._redis.delete(self._key(tenant_id))
        except Exception as exc:
            logger.warning("Failed to delete LLM config from Redis for %s: %s", tenant_id, exc)


# ── Module-level singleton ─────────────────────────────────────────────────────
# Set by ``create_app()`` when a real Redis connection is available.
# Workers and endpoints call ``get_llm_config_store()`` to access it.

_llm_config_store: LLMConfigStore | None = None


def get_llm_config_store() -> LLMConfigStore | None:
    """Return the process-wide LLM config store, or *None* if not yet wired."""
    return _llm_config_store


def set_llm_config_store(store: LLMConfigStore) -> None:
    """Wire the process-wide singleton (called once from ``create_app``)."""
    global _llm_config_store
    _llm_config_store = store
