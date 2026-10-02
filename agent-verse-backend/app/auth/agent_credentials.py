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

Enforcement (AGKEY-01):
- ``TenantMiddleware`` resolves an ``av_agent_*`` key with :meth:`resolve_context`
  (Postgres is authoritative, a short shared Redis cache sits in front and is
  deleted on revoke) into a TenantContext with ``roles=("agent",)``, goal-only
  scopes and an :class:`~app.tenancy.context.AgentKeyRestriction`.
- :func:`bind_goal_to_agent_key` (GoalService.submit_goal) binds the goal to the
  key's agent and records the restriction on the goal's execution_context.
- :func:`apply_goal_agent_key` (the worker) rebuilds it, and the executor's tool
  gate / the policy engine deny every tool outside it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import secrets
import time
import uuid
from typing import Any

from app.observability.logging import get_logger
from app.tenancy.context import AgentKeyRestriction, PlanTier, TenantContext

logger = get_logger(__name__)

_AGENT_KEY_PREFIX = "av_agent_"

# goals.execution_context key carrying the submitting agent key's restriction.
AGENT_KEY_CONTEXT_KEY = "agent_key_restriction"

# What an agent key may call (TenantMiddleware intersects these with the
# "agent" role's scopes): it submits and reads goals, nothing else.
AGENT_KEY_SCOPES: tuple[str, ...] = ("goals:read", "goals:write")

# Shared resolution cache; deleted on revoke so revocation is cluster-wide.
_CACHE_TTL_S = 60


def _cache_key(key_hash: str) -> str:
    return f"agent_key:{key_hash}"


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


def _restriction(record: dict[str, Any]) -> AgentKeyRestriction:
    def _opt(value: Any) -> tuple[str, ...] | None:
        if isinstance(value, str):
            value = json.loads(value)
        return None if value is None else tuple(str(v) for v in value)

    return AgentKeyRestriction(
        key_id=str(record["key_id"]),
        agent_id=str(record["agent_id"]),
        allowed_tools=_opt(record.get("allowed_tools")),
        denied_tools=_opt(record.get("denied_tools")) or (),
        allowed_connectors=_opt(record.get("allowed_connectors")),
    )


def agent_key_context(record: dict[str, Any], plan: PlanTier) -> TenantContext:
    """The TenantContext an agent key authenticates as."""
    return TenantContext(
        tenant_id=str(record["tenant_id"]),
        plan=plan,
        api_key_id=f"agentkey:{record['key_id']}",
        roles=("agent",),
        scopes=AGENT_KEY_SCOPES,
        agent_key=_restriction(record),
    )


def bind_goal_to_agent_key(
    tenant_ctx: TenantContext,
    agent_id: str | None,
    execution_context: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Bind a goal submitted with an agent key to that key's agent.

    Returns the (possibly defaulted) agent id and the execution context carrying
    the key's restriction for the worker. A goal for any other agent raises
    :class:`AuthorizationError` (403). Non-agent callers pass through unchanged.
    """
    # Internal callers (trigger dispatcher, workers) may pass a minimal context.
    restriction = getattr(tenant_ctx, "agent_key", None)
    if restriction is None:
        return agent_id, execution_context
    from app.core.errors import AuthorizationError

    if agent_id and agent_id != restriction.agent_id:
        raise AuthorizationError(
            "This agent-scoped API key may only submit goals for its own agent."
        )
    ctx = dict(execution_context or {})
    ctx[AGENT_KEY_CONTEXT_KEY] = restriction.to_dict()
    return restriction.agent_id, ctx


def apply_goal_agent_key(
    tenant_ctx: TenantContext, execution_context: dict[str, Any] | None, *, unreadable: bool
) -> TenantContext:
    """The worker's tenant context with the goal submitter's agent-key restriction.

    An execution context that could not be read leaves the submitter unknown, so
    every tool is denied (fail closed) rather than run unrestricted.
    """
    if unreadable:
        return dataclasses.replace(
            tenant_ctx,
            agent_key=AgentKeyRestriction(key_id="unknown", agent_id="unknown", deny_all=True),
        )
    data = (execution_context or {}).get(AGENT_KEY_CONTEXT_KEY)
    if data is None:
        return tenant_ctx
    return dataclasses.replace(tenant_ctx, agent_key=AgentKeyRestriction.from_dict(data))


def agent_key_tool_denial(tenant_ctx: Any, tool_name: str) -> str | None:
    """The agent-key denial reason for *tool_name* under *tenant_ctx*, or None."""
    restriction = getattr(tenant_ctx, "agent_key", None)
    if restriction is None:
        return None
    return restriction.tool_denial(str(tool_name))  # type: ignore[no-any-return]


class AgentKeyStoreUnavailableError(Exception):
    """The agent-key store could not complete an operation (fail closed)."""


class AgentCredentialStore:
    """
    Per-agent API key store.

    Agent keys:
    - Are scoped to one agent_id
    - Can only submit goals for that agent_id
    - Inherit parent tenant's plan/limits
    - Have their own tool allowlist
    - Expire after configured TTL (default: never; per-key rotation configurable)

    With a DB wired (production) Postgres is the only source of truth; the
    in-process dicts serve the no-DB build (unit tests / local dev) only.
    """

    def __init__(self) -> None:
        # key_hash → AgentKeyRecord (no-DB build only)
        self._keys: dict[str, dict[str, Any]] = {}
        # agent_id → list of key_hashes (no-DB build only)
        self._agent_keys: dict[str, list[str]] = {}
        # Wired by the app lifespan; when set, keys persist to Postgres (durable +
        # cross-pod) instead of only this process's dicts.
        self._db_factory: Any = None
        self._redis: Any = None

    def set_db(self, db_factory: Any) -> None:
        self._db_factory = db_factory

    def set_redis(self, redis: Any) -> None:
        self._redis = redis

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
        """Validate key and return record, or None if invalid/expired (no-DB build)."""
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
        return _restriction(key_record).tool_denial(tool_name) is None

    # ── Authentication (TenantMiddleware) ─────────────────────────────────────

    async def resolve_context(
        self, raw_key: str, *, tenant_service: Any = None
    ) -> TenantContext | None:
        """Authenticate an ``av_agent_*`` key; None when it is unknown/revoked/expired.

        DB-authoritative behind a 60 s shared Redis cache (deleted on revoke). A
        DB error raises :class:`AgentKeyStoreUnavailableError`: the caller answers
        401 — never an unrestricted context.
        """
        if not is_agent_key(raw_key):
            return None
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        if self._redis is not None:
            try:
                cached = await self._redis.get(_cache_key(key_hash))
            except Exception:
                cached = None  # cache trouble falls through to the DB
            if cached:
                record = json.loads(cached)
                exp = record.get("expires_at")
                if exp and time.time() > float(exp):
                    return None
                return agent_key_context(record, PlanTier(record["plan"]))

        if self._db_factory is None:
            mem = self.resolve(raw_key)
            if mem is None or tenant_service is None:
                return None
            try:
                profile = await tenant_service.get_tenant(mem["tenant_id"])
            except Exception:
                return None
            return agent_key_context(mem, PlanTier(str(profile.get("plan", "free"))))

        record = await self._db_resolve(key_hash)
        if record is None:
            return None
        if self._redis is not None:
            try:
                await self._redis.setex(
                    _cache_key(key_hash), _CACHE_TTL_S, json.dumps(record, default=str)
                )
            except Exception as exc:  # caching is best-effort; the DB answered
                logger.debug("agent_key_cache_write_failed", error=str(exc)[:120])
        return agent_key_context(record, PlanTier(record["plan"]))

    async def _db_resolve(self, key_hash: str) -> dict[str, Any] | None:
        """One active, unexpired key whose agent still exists, with the tenant plan.

        Presents the hash as ``app.agent_key_hash`` (policy
        agent_api_keys_by_presented_hash) — the only row a NOBYPASSRLS role can
        then see — and, once the tenant is known, checks the agent under its RLS.
        """
        from sqlalchemy import text as _t

        try:
            async with self._db_factory() as s, s.begin():
                await s.execute(
                    _t("SELECT set_config('app.agent_key_hash', :h, true)"), {"h": key_hash}
                )
                row = (
                    (
                        await s.execute(
                            _t(
                                "SELECT k.key_id, k.tenant_id, k.agent_id, k.allowed_tools, "
                                "k.denied_tools, k.allowed_connectors, k.expires_at, "
                                "t.plan_tier AS plan "
                                "FROM agent_api_keys k JOIN tenants t ON t.id = k.tenant_id "
                                "WHERE k.key_hash = :h AND k.is_active AND t.is_active"
                            ),
                            {"h": key_hash},
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
                record = dict(row)
                if record["expires_at"] and time.time() > float(record["expires_at"]):
                    return None
                await s.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": record["tenant_id"]},
                )
                agent_ok = (
                    await s.execute(
                        _t(
                            "SELECT 1 FROM agents WHERE id = :aid AND tenant_id = :tid "
                            "AND is_active"
                        ),
                        {"aid": record["agent_id"], "tid": record["tenant_id"]},
                    )
                ).first()
                if agent_ok is None:
                    return None
                await s.execute(
                    _t(
                        "UPDATE agent_api_keys SET last_used_at = extract(epoch FROM now()), "
                        "use_count = use_count + 1 WHERE key_hash = :h AND tenant_id = :tid"
                    ),
                    {"h": key_hash, "tid": record["tenant_id"]},
                )
        except Exception as exc:
            logger.warning("agent_key_resolve_failed", error=str(exc)[:200])
            raise AgentKeyStoreUnavailableError(str(exc)) from exc
        for col in ("allowed_tools", "denied_tools", "allowed_connectors"):
            if isinstance(record.get(col), str):
                record[col] = json.loads(record[col])
        return record

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
                agent_id=agent_id,
                tenant_id=tenant_id,
                name=name,
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                allowed_connectors=allowed_connectors,
                expires_at=expires_at,
                created_by=created_by,
            )
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
                    "kid": key_id,
                    "tid": tenant_id,
                    "aid": agent_id,
                    "name": name,
                    "kh": key_hash,
                    "at": json.dumps(allowed_tools) if allowed_tools is not None else None,
                    "dt": json.dumps(denied_tools or []),
                    "ac": (
                        json.dumps(allowed_connectors) if allowed_connectors is not None else None
                    ),
                    "exp": expires_at,
                    "cb": created_by,
                },
            )
        logger.info("agent_key_created", key_id=key_id, agent_id=agent_id)
        return {"key_id": key_id, "raw_key": raw_key, "agent_id": agent_id}

    async def list_for_agent_async(self, agent_id: str, tenant_id: str) -> list[dict[str, Any]]:
        if self._db_factory is None:
            return [k for k in self.list_for_agent(agent_id) if k["tenant_id"] == tenant_id]
        from sqlalchemy import text as _t

        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            rows = (
                (
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
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def revoke_async(self, key_id: str, agent_id: str, tenant_id: str) -> bool:
        """Deactivate a key and drop its shared cache entry.

        A cache delete that fails raises :class:`AgentKeyStoreUnavailableError`
        (the DB revoke is idempotent; retry): the key would otherwise stay usable
        on every pod for the cache TTL while the API answered "revoked".
        """
        if self._db_factory is None:
            key_hash = next(
                (
                    h
                    for h, r in self._keys.items()
                    if r["key_id"] == key_id
                    and r["agent_id"] == agent_id
                    and r["tenant_id"] == tenant_id
                ),
                None,
            )
            if key_hash is None or not self.revoke(key_id, agent_id):
                return False
            await self._invalidate(key_hash)
            return True
        from sqlalchemy import text as _t

        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            hashes = (
                (
                    await s.execute(
                        _t(
                            "UPDATE agent_api_keys SET is_active = FALSE WHERE key_id = :kid "
                            "AND agent_id = :aid AND tenant_id = :tid RETURNING key_hash"
                        ),
                        {"kid": key_id, "aid": agent_id, "tid": tenant_id},
                    )
                )
                .scalars()
                .all()
            )
        for key_hash in hashes:
            await self._invalidate(str(key_hash))
        if hashes:
            logger.info("agent_key_revoked", key_id=key_id)
        return bool(hashes)

    async def _invalidate(self, key_hash: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(_cache_key(key_hash))
        except Exception as exc:
            logger.error("agent_key_revoke_cache_invalidation_failed", error=str(exc)[:200])
            raise AgentKeyStoreUnavailableError(
                "Agent key revoked in the database but the shared auth cache could not be "
                "invalidated; retry the revoke."
            ) from exc


# Module-level singleton
_agent_credential_store = AgentCredentialStore()
