"""
Per-Agent Credential System
============================
Every agent can have its own scoped API key that is separate from the
tenant-wide key. An agent key can ONLY submit goals for its specific agent_id
and ONLY use its configured tool allowlist.

Key structure: av_agent_{agent_id_prefix}_{random}
- Identified by prefix to route through agent-credential validation
- Scoped to one agent_id
- Inherits parent tenant_id and plan
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_AGENT_KEY_PREFIX = "av_agent_"


def generate_agent_api_key(agent_id: str) -> tuple[str, str]:
    """Generate (raw_key, key_hash) for an agent-scoped API key."""
    random_part = secrets.token_urlsafe(32)
    agent_prefix = agent_id[:8]
    raw_key = f"{_AGENT_KEY_PREFIX}{agent_prefix}_{random_part}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


def is_agent_key(raw_key: str) -> bool:
    """Return True if this key is an agent-scoped key."""
    return raw_key.startswith(_AGENT_KEY_PREFIX)


class AgentCredentialStore:
    """
    Per-agent API key store.

    Agent keys:
    - Are scoped to one agent_id
    - Can only submit goals for that agent_id
    - Inherit parent tenant's plan/limits
    - Have their own tool allowlist
    - Expire after configured TTL (default: never; per-key rotation configurable)
    """

    def __init__(self) -> None:
        # key_hash → AgentKeyRecord
        self._keys: dict[str, dict[str, Any]] = {}
        # agent_id → list of key_hashes
        self._agent_keys: dict[str, list[str]] = {}
        # Wired by the app lifespan; when set, keys persist to Postgres (durable +
        # cross-pod) instead of only this process's dicts.
        self._db_factory: Any = None

    def set_db(self, db_factory: Any) -> None:
        self._db_factory = db_factory

    def create_key(
        self,
        *,
        agent_id: str,
        tenant_id: str,
        name: str,
        allowed_tools: list[str] | None = None,  # None = no restriction
        denied_tools: list[str] | None = None,
        allowed_connectors: list[str] | None = None,
        expires_at: float | None = None,
        created_by: str = "",
    ) -> dict[str, Any]:
        """Create a new agent-scoped API key. Returns {key_id, raw_key, ...}."""
        raw_key, key_hash = generate_agent_api_key(agent_id)
        key_id = uuid.uuid4().hex
        record = {
            "key_id": key_id,
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "name": name,
            "key_hash": key_hash,
            "allowed_tools": allowed_tools,
            "denied_tools": denied_tools or [],
            "allowed_connectors": allowed_connectors,
            "expires_at": expires_at,
            "created_by": created_by,
            "created_at": time.time(),
            "last_used_at": None,
            "is_active": True,
            "use_count": 0,
        }
        self._keys[key_hash] = record
        self._agent_keys.setdefault(agent_id, []).append(key_hash)
        logger.info("agent_key_created", key_id=key_id, agent_id=agent_id)
        return {"key_id": key_id, "raw_key": raw_key, "agent_id": agent_id}

    def resolve(self, raw_key: str) -> dict[str, Any] | None:
        """Validate key and return record, or None if invalid/expired."""
        if not is_agent_key(raw_key):
            return None
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        record = self._keys.get(key_hash)
        if record is None or not record["is_active"]:
            return None
        if record["expires_at"] and time.time() > record["expires_at"]:
            record["is_active"] = False
            logger.warning("agent_key_expired", key_id=record["key_id"])
            return None
        record["last_used_at"] = time.time()
        record["use_count"] += 1
        return record

    def revoke(self, key_id: str, agent_id: str) -> bool:
        for record in self._keys.values():
            if record["key_id"] == key_id and record["agent_id"] == agent_id:
                record["is_active"] = False
                logger.info("agent_key_revoked", key_id=key_id)
                return True
        return False

    def list_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        hashes = self._agent_keys.get(agent_id, [])
        return [
            {k: v for k, v in self._keys[h].items() if k != "key_hash"}
            for h in hashes
            if h in self._keys
        ]

    def check_tool_allowed(self, key_record: dict[str, Any], tool_name: str) -> bool:
        """Check if this key's policy allows the given tool."""
        denied = key_record.get("denied_tools") or []
        if any(tool_name.startswith(d.rstrip("*")) or tool_name == d for d in denied):
            return False
        allowed = key_record.get("allowed_tools")
        if allowed is None:
            return True  # no restriction
        return any(
            tool_name == a or (a.endswith("*") and tool_name.startswith(a[:-1])) for a in allowed
        )


    # ── DB-backed async CRUD (durable + cross-pod; used by the REST API) ──────

    async def create_key_async(
        self,
        *,
        agent_id: str,
        tenant_id: str,
        name: str,
        allowed_tools: list[str] | None = None,
        denied_tools: list[str] | None = None,
        allowed_connectors: list[str] | None = None,
        expires_at: float | None = None,
        created_by: str = "",
    ) -> dict[str, Any]:
        if self._db_factory is None:
            return self.create_key(
                agent_id=agent_id, tenant_id=tenant_id, name=name,
                allowed_tools=allowed_tools, denied_tools=denied_tools,
                allowed_connectors=allowed_connectors, expires_at=expires_at,
                created_by=created_by,
            )
        import json as _json

        from sqlalchemy import text as _t

        raw_key, key_hash = generate_agent_api_key(agent_id)
        key_id = uuid.uuid4().hex
        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            await s.execute(
                _t(
                    "INSERT INTO agent_api_keys (key_id, tenant_id, agent_id, name, "
                    "key_hash, allowed_tools, denied_tools, allowed_connectors, "
                    "expires_at, created_by) VALUES (:kid, :tid, :aid, :name, :kh, "
                    "CAST(:at AS jsonb), CAST(:dt AS jsonb), CAST(:ac AS jsonb), :exp, :cb)"
                ),
                {
                    "kid": key_id, "tid": tenant_id, "aid": agent_id, "name": name,
                    "kh": key_hash,
                    "at": _json.dumps(allowed_tools) if allowed_tools is not None else None,
                    "dt": _json.dumps(denied_tools or []),
                    "ac": (
                        _json.dumps(allowed_connectors)
                        if allowed_connectors is not None
                        else None
                    ),
                    "exp": expires_at, "cb": created_by,
                },
            )
        logger.info("agent_key_created", key_id=key_id, agent_id=agent_id)
        return {"key_id": key_id, "raw_key": raw_key, "agent_id": agent_id}

    async def list_for_agent_async(self, agent_id: str, tenant_id: str) -> list[dict[str, Any]]:
        if self._db_factory is None:
            return self.list_for_agent(agent_id)
        from sqlalchemy import text as _t

        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            rows = (
                await s.execute(
                    _t(
                        "SELECT key_id, agent_id, tenant_id, name, allowed_tools, "
                        "denied_tools, allowed_connectors, expires_at, created_by, "
                        "created_at, last_used_at, is_active, use_count "
                        "FROM agent_api_keys WHERE tenant_id = :tid AND agent_id = :aid "
                        "ORDER BY created_at DESC"
                    ),
                    {"tid": tenant_id, "aid": agent_id},
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    async def revoke_async(self, key_id: str, agent_id: str, tenant_id: str) -> bool:
        if self._db_factory is None:
            return self.revoke(key_id, agent_id)
        from sqlalchemy import text as _t

        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            res = await s.execute(
                _t(
                    "UPDATE agent_api_keys SET is_active = FALSE WHERE key_id = :kid "
                    "AND agent_id = :aid AND tenant_id = :tid"
                ),
                {"kid": key_id, "aid": agent_id, "tid": tenant_id},
            )
        if res.rowcount:
            logger.info("agent_key_revoked", key_id=key_id)
        return bool(res.rowcount)


# Module-level singleton
_agent_credential_store = AgentCredentialStore()
