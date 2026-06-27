"""Policy engine — evaluates tool calls against named policies.

Policies layer on top of the permission matrix:
  - A policy declares lists of denied tools.
  - Multiple policies stack (most restrictive wins).
  - Tool matching supports exact names or glob prefixes.
"""

from __future__ import annotations

import enum
import fnmatch
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    import asyncio


class PolicyResult(enum.StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class Policy:
    name: str
    description: str = ""
    denied_tools: list[str] = field(default_factory=list)
    approval_tools: list[str] = field(default_factory=list)
    scope: str = "global"
    allowed_hours_utc: tuple[int, int] | None = None  # (start_hour, end_hour) UTC
    allowed_weekdays: list[int] | None = None  # 0=Monday ... 6=Sunday
    tenant_id: str = ""
    # Supplementary fields populated when reloading from DB
    action: str = ""
    tool_pattern: str = ""


@dataclass
class GovernancePolicy:
    """Lightweight policy record used for tenant-scoped policy isolation.

    This is distinct from :class:`Policy` (which drives the engine evaluation
    logic) and serves as a simple data holder for per-tenant policy records.
    """

    name: str
    action: str
    tool_pattern: str = ""
    tenant_id: str = ""


class PolicyEngine:
    """Evaluates tool calls against a set of policies.

    Policies are intentionally stateless — they don't reference tenant context
    directly; the caller scopes which policies apply to a tenant.
    """

    def __init__(self, policies: list[Policy] | None = None) -> None:
        self._policies: list[Policy] = policies or []

    def add_policy(self, policy: Policy) -> None:
        self._policies.append(policy)

    def _is_within_time_window(self, policy: Policy) -> bool:
        """Returns True if current UTC time is within policy's allowed window."""
        from datetime import UTC, datetime
        if policy.allowed_hours_utc is None and policy.allowed_weekdays is None:
            return True  # No time restriction
        now = datetime.now(UTC)
        if policy.allowed_weekdays is not None:
            if now.weekday() not in policy.allowed_weekdays:
                return False
        if policy.allowed_hours_utc is not None:
            start_h, end_h = policy.allowed_hours_utc
            if not (start_h <= now.hour < end_h):
                return False
        return True

    def evaluate(self, tool_name: str, *, tenant_ctx: TenantContext) -> PolicyResult:
        for policy in self._policies:
            # Skip policies belonging to other tenants
            policy_tenant = getattr(policy, "tenant_id", "")
            if policy_tenant and policy_tenant != tenant_ctx.tenant_id:
                continue
            if not self._is_within_time_window(policy):
                continue  # Time window not active, skip this policy
            for pattern in policy.denied_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    return PolicyResult.DENY
            for pattern in policy.approval_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    return PolicyResult.REQUIRE_APPROVAL
        return PolicyResult.ALLOW

    async def reload_from_db(self, db: Any, tenant_id: str | None = None) -> int:
        """Reload policies from DB. If tenant_id given, reload only that tenant's policies.
        Called by Redis subscriber when another replica modifies policies.
        Returns count of policies loaded.
        """
        if db is None:
            return 0
        try:
            from sqlalchemy import text
            async with db() as session:
                if tenant_id:
                    rows = (await session.execute(
                        text(
                            "SELECT name, action, tools_pattern, tenant_id "
                            "FROM governance_policies WHERE tenant_id=:tid"
                        ),
                        {"tid": tenant_id},
                    )).fetchall()
                    # Remove old policies for this tenant only
                    self._policies = [
                        p for p in self._policies
                        if getattr(p, "tenant_id", "") != tenant_id
                    ]
                else:
                    rows = (await session.execute(
                        text(
                            "SELECT name, action, tools_pattern, tenant_id "
                            "FROM governance_policies"
                        )
                    )).fetchall()
                    self._policies = []

                for row in rows:
                    name, action, tools_pattern, pol_tenant_id = row
                    denied_tools = [tools_pattern or ".*"] if action == "deny" else []
                    approval_tools = (
                        [tools_pattern or ".*"] if action == "require_approval" else []
                    )
                    p = Policy(
                        name=name,
                        description="",
                        denied_tools=denied_tools,
                        approval_tools=approval_tools,
                        tenant_id=pol_tenant_id or "",
                        action=action,
                        tool_pattern=tools_pattern or ".*",
                    )
                    self._policies.append(p)
            return len(rows)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("policy_reload_from_db_failed: %s", exc)
            return 0

    @staticmethod
    async def publish_change(redis: Any, tenant_id: str, action: str) -> None:
        """Publish a policy change event so other replicas can reload.

        Channel: policy_changes
        Message: JSON {tenant_id, action, timestamp}
        """
        if redis is None:
            return
        try:
            import json
            from datetime import UTC, datetime
            msg = json.dumps({
                "tenant_id": tenant_id,
                "action": action,  # "created" | "deleted"
                "ts": datetime.now(UTC).isoformat(),
            })
            await redis.publish("policy_changes", msg)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("policy_publish_failed: %s", exc)

    @classmethod
    async def subscribe_to_changes(
        cls, redis_url: str, engine: "PolicyEngine", db: Any
    ) -> None:
        """Long-running coroutine: subscribe to policy_changes channel and reload on message.

        Designed to run as an asyncio background task via asyncio.create_task().
        Uses a separate Redis connection (pubsub requires dedicated connection).
        """
        import asyncio
        import json
        import redis.asyncio as aioredis
        from app.observability.logging import get_logger

        logger = get_logger(__name__)

        while True:
            try:
                async with aioredis.from_url(redis_url, decode_responses=True) as r:
                    pubsub = r.pubsub()
                    await pubsub.subscribe("policy_changes")
                    logger.info("policy_pubsub_subscribed")

                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        try:
                            data = json.loads(message["data"])
                            tenant_id = data.get("tenant_id")
                            logger.info(
                                "policy_change_received",
                                tenant_id=tenant_id,
                                action=data.get("action"),
                            )
                            await engine.reload_from_db(db, tenant_id=tenant_id)
                        except Exception as exc:
                            logger.warning("policy_change_process_failed", error=str(exc))
            except asyncio.CancelledError:
                logger.info("policy_pubsub_cancelled")
                return
            except Exception as exc:
                logger.warning("policy_pubsub_error", error=str(exc))
                await asyncio.sleep(5)  # reconnect after 5s


def start_policy_subscriber(
    redis_url: str, engine: "PolicyEngine", db: Any
) -> "asyncio.Task[None]":
    """Start the policy change subscriber as a background task.
    Call this from main.py lifespan after Redis is available.
    Returns the task so it can be cancelled on shutdown.
    """
    import asyncio
    return asyncio.create_task(
        PolicyEngine.subscribe_to_changes(redis_url, engine, db),
        name="policy_pubsub_subscriber",
    )
