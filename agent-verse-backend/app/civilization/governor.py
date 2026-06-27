"""Governor — central authority for the civilization.

The ONLY component that may create or retire a society member.
Every decision is audited. Stateless across calls except via DB/Redis.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.civilization.constitution import evaluate_breach, evaluate_spawn
from app.civilization.models import (
    BreachContext,
    BreachVerdict,
    Constitution,
    MetaAgentConfigValidated,
    SpawnContext,
    SpawnDecision,
    SpawnVerdict,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)


class Governor:
    """Governs the civilization: enforces the Constitution, creates/retires members.

    Dependencies injected at construction time; stateless between calls.
    """

    def __init__(
        self,
        *,
        constitution: Constitution,
        civilization_id: str,
        tenant_id: str,
        agent_store: Any = None,
        meta_agent_planner: Any = None,
        cost_controller: Any = None,
        policy_engine: Any = None,
        hitl_gateway: Any = None,
        audit_log: Any = None,
        db_session_factory: Any = None,
        redis: Any = None,
    ) -> None:
        self._constitution = constitution
        self._civilization_id = civilization_id
        self._tenant_id = tenant_id
        self._agent_store = agent_store
        self._planner = meta_agent_planner
        self._cost_controller = cost_controller
        self._policy_engine = policy_engine
        self._hitl = hitl_gateway
        self._audit_log = audit_log
        self._db = db_session_factory
        self._redis = redis

    async def evaluate_spawn_request(
        self,
        *,
        requester_agent_id: str,
        requested_capability: str,
        goal_text: str,
        depth: int,
        parent_budget_usd: float,
        parent_policy_ids: list[str],
        tenant_ctx: Any,
    ) -> SpawnVerdict:
        """Evaluate and record a spawn request. Returns verdict."""
        # Gather live metrics
        metrics = await self._get_live_metrics()

        ctx = SpawnContext(
            civilization_id=self._civilization_id,
            tenant_id=self._tenant_id,
            requester_agent_id=requester_agent_id,
            requested_capability=requested_capability,
            goal_text=goal_text,
            depth=depth,
            current_total_agents=metrics["total_agents"],
            current_concurrent_agents=metrics["concurrent_agents"],
            civilization_budget_spent_usd=metrics["budget_spent_usd"],
            spawn_rate_last_min=metrics["spawn_rate_last_min"],
            parent_budget_usd=parent_budget_usd,
            parent_policy_ids=parent_policy_ids,
        )

        verdict = evaluate_spawn(ctx, self._constitution)

        # Audit the verdict regardless
        await self._audit_spawn(
            requester_agent_id=requester_agent_id,
            requested_capability=requested_capability,
            goal_text=goal_text,
            verdict=verdict,
        )

        logger.info(
            "governor_spawn_verdict",
            civilization_id=self._civilization_id,
            decision=verdict.decision.value,
            reason=verdict.reason,
            depth=depth,
        )

        return verdict

    async def spawn_agent(
        self,
        *,
        verdict: SpawnVerdict,
        requested_capability: str,
        goal_text: str,
        requester_agent_id: str,
        depth: int,
        tenant_ctx: Any,
    ) -> dict[str, Any]:
        """Create a new civilization member (only called with APPROVED verdict).

        Returns the created agent record.
        """
        if verdict.decision != SpawnDecision.APPROVED:
            raise ValueError(f"Cannot spawn with DENIED verdict: {verdict.reason}")

        # Check if an idle member matches the capability
        existing = await self._find_idle_matching(requested_capability, tenant_ctx)
        if existing:
            logger.info(
                "governor_reusing_idle_member",
                agent_id=existing.get("agent_id"),
                capability=requested_capability,
            )
            return existing

        # Plan a new agent config
        new_config = await self._plan_and_validate_agent(
            requested_capability=requested_capability,
            goal_text=goal_text,
            verdict=verdict,
            tenant_ctx=tenant_ctx,
        )

        # Create via AgentStore
        record: dict[str, Any] = {
            "name": new_config.name,
            "goal_template": new_config.goal_template,
            "autonomy_mode": new_config.autonomy_mode,
            "connector_ids": new_config.connector_ids,
            "trigger_config": new_config.trigger_config,
            "system_prompt": new_config.system_prompt,
            "max_iterations": new_config.max_iterations,
            "allowed_collection_ids": new_config.allowed_collection_ids,
            "policy_ids": new_config.policy_ids,
            "eval_suite_id": new_config.eval_suite_id,
        }

        if self._agent_store is not None:
            agent_id = await self._agent_store.create(record, tenant_ctx=tenant_ctx)
            record["agent_id"] = agent_id
        else:
            record["agent_id"] = uuid.uuid4().hex

        # Record in civilization_agents table
        await self._register_civilization_member(
            agent_id=record["agent_id"],
            parent_agent_id=requester_agent_id,
            depth=depth,
            budget_usd=verdict.allowed_budget_usd,
        )

        logger.info(
            "governor_agent_spawned",
            civilization_id=self._civilization_id,
            agent_id=record["agent_id"],
            capability=requested_capability,
            depth=depth,
        )

        return record

    async def check_breach(self) -> BreachVerdict:
        """Check Constitution breach. Called by Celery beat every 30s."""
        metrics = await self._get_live_metrics()
        ctx = BreachContext(
            civilization_id=self._civilization_id,
            tenant_id=self._tenant_id,
            budget_spent_usd=metrics["budget_spent_usd"],
            budget_total_usd=self._constitution.total_budget_usd,
            spawn_rate_last_min=metrics["spawn_rate_last_min"],
            total_agents=metrics["total_agents"],
            concurrent_agents=metrics["concurrent_agents"],
        )
        verdict = evaluate_breach(ctx, self._constitution)

        if verdict.breached:
            logger.warning(
                "governor_breach_detected",
                civilization_id=self._civilization_id,
                reasons=verdict.reasons,
            )
            await self._auto_pause(reasons=verdict.reasons)

        return verdict

    async def auto_retire_idle(self) -> list[str]:
        """Retire members below reputation floor or past idle TTL. Returns retired agent IDs."""
        if self._db is None:
            return []
        retired = []
        try:
            from sqlalchemy import text
            async with self._db() as session:
                rows = (await session.execute(text("""
                    SELECT id, agent_id, reputation, last_active_at
                    FROM civilization_agents
                    WHERE civilization_id = :cid AND tenant_id = :tid
                      AND status = 'active'
                    ORDER BY reputation ASC, last_active_at ASC
                """), {"cid": self._civilization_id, "tid": self._tenant_id})).fetchall()

            # Count active members to ensure min_viable_roster
            active_count = len(rows)
            from datetime import timedelta
            idle_cutoff = datetime.now(UTC) - timedelta(seconds=self._constitution.idle_ttl_seconds)

            for row in rows:
                if active_count <= self._constitution.min_viable_roster:
                    break  # Keep minimum viable roster
                member_id, agent_id, reputation, last_active = row
                should_retire = (
                    (reputation is not None and reputation < self._constitution.reputation_floor) or
                    (last_active is not None and last_active < idle_cutoff.replace(tzinfo=None))
                )
                if should_retire:
                    await self._retire_member(member_id, agent_id)
                    retired.append(agent_id)
                    active_count -= 1
        except Exception as exc:
            logger.warning("governor_auto_retire_failed", error=str(exc))
        return retired

    async def kill_agent(self, agent_id: str, tenant_ctx: Any) -> None:
        """Kill a specific civilization member."""
        await self._retire_member_by_agent_id(agent_id)
        # Signal running task to halt (Redis flag)
        if self._redis is not None:
            try:
                await self._redis.set(
                    f"civ_kill_agent:{self._civilization_id}:{agent_id}", "1", ex=3600
                )
            except Exception as exc:
                logger.warning("governor_kill_signal_failed", error=str(exc))

    async def pause(self) -> None:
        """Pause the civilization — stops new spawns, signals agents to halt at next checkpoint."""
        await self._set_civilization_status("paused")
        if self._redis is not None:
            try:
                await self._redis.set(
                    f"civ_paused:{self._tenant_id}:{self._civilization_id}", "1", ex=86400
                )
            except Exception as exc:
                logger.warning("governor_pause_redis_failed", error=str(exc))

    async def resume(self) -> None:
        """Resume a paused civilization."""
        await self._set_civilization_status("active")
        if self._redis is not None:
            try:
                await self._redis.delete(
                    f"civ_paused:{self._tenant_id}:{self._civilization_id}"
                )
            except Exception as exc:
                logger.warning("governor_resume_redis_failed", error=str(exc))

    def is_paused_sync(self, redis_sync: Any) -> bool:
        """Synchronous check for Celery workers."""
        if redis_sync is None:
            return False
        try:
            return bool(redis_sync.get(
                f"civ_paused:{self._tenant_id}:{self._civilization_id}"
            ))
        except Exception:
            return False

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_live_metrics(self) -> dict:
        """Fetch live metrics from DB/Redis."""
        metrics = {
            "total_agents": 0,
            "concurrent_agents": 0,
            "budget_spent_usd": 0.0,
            "spawn_rate_last_min": 0,
        }
        if self._db is None:
            return metrics
        try:
            from sqlalchemy import text
            async with self._db() as session:
                row = (await session.execute(text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status != 'retired') as total,
                        COUNT(*) FILTER (WHERE status = 'active') as concurrent,
                        COALESCE(SUM(COALESCE(budget_spent_usd, 0)), 0) as spent,
                        COUNT(*) FILTER (
                            WHERE spawned_at > NOW() - INTERVAL '1 minute'
                        ) as spawn_rate
                    FROM civilization_agents
                    WHERE civilization_id = :cid AND tenant_id = :tid
                """), {"cid": self._civilization_id, "tid": self._tenant_id})).fetchone()
            if row:
                metrics["total_agents"] = int(row[0] or 0)
                metrics["concurrent_agents"] = int(row[1] or 0)
                metrics["budget_spent_usd"] = float(row[2] or 0)
                metrics["spawn_rate_last_min"] = int(row[3] or 0)
        except Exception as exc:
            logger.warning("governor_metrics_fetch_failed", error=str(exc))
        return metrics

    async def _find_idle_matching(self, capability: str, tenant_ctx: Any) -> dict | None:
        """Look for an idle member that could handle this capability."""
        if self._agent_store is None:
            return None
        try:
            agents = await self._agent_store.list_async(tenant_ctx=tenant_ctx)
            for agent in agents:
                if (
                    agent.get("goal_template", "").lower().find(capability.lower()) != -1
                    and await self._is_idle_member(agent.get("agent_id", ""))
                ):
                    return agent
        except Exception:
            pass
        return None

    async def _is_idle_member(self, agent_id: str) -> bool:
        if self._db is None:
            return False
        try:
            from sqlalchemy import text
            async with self._db() as session:
                row = (await session.execute(text(
                    "SELECT status FROM civilization_agents "
                    "WHERE agent_id=:aid AND civilization_id=:cid AND tenant_id=:tid"
                ), {
                    "aid": agent_id,
                    "cid": self._civilization_id,
                    "tid": self._tenant_id,
                })).fetchone()
            return row is not None and row[0] == "idle"
        except Exception:
            return False

    async def _plan_and_validate_agent(
        self,
        *,
        requested_capability: str,
        goal_text: str,
        verdict: SpawnVerdict,
        tenant_ctx: Any,
    ) -> MetaAgentConfigValidated:
        """Plan a new agent config via MetaAgentPlanner and validate/clamp."""
        if self._planner is not None:
            try:
                raw_config = await self._planner.plan(
                    command=(
                        f"Create an agent for capability: {requested_capability}."
                        f" Goal: {goal_text}"
                    ),
                    tenant_ctx=tenant_ctx,
                )
                return MetaAgentConfigValidated(
                    name=getattr(raw_config, "name", f"Agent-{requested_capability[:20]}"),
                    goal_template=getattr(
                        raw_config, "goal_template", f"Handle: {goal_text[:200]}"
                    ),
                    autonomy_mode=verdict.clamped_autonomy,
                    connector_ids=getattr(raw_config, "connectors", [])[:10],
                    trigger_config={},
                    system_prompt="",
                    max_iterations=10,
                    allowed_collection_ids=[],
                    policy_ids=verdict.inherited_policy_ids,
                )
            except Exception as exc:
                logger.warning("governor_planner_failed", error=str(exc))

        # Fallback: minimal config
        return MetaAgentConfigValidated(
            name=f"Agent-{requested_capability[:20]}-{uuid.uuid4().hex[:6]}",
            goal_template=(
                f"You are an agent specialized in: {requested_capability}."
                f" Execute: {goal_text[:300]}"
            ),
            autonomy_mode=verdict.clamped_autonomy,
            connector_ids=[],
            trigger_config={},
            system_prompt="",
            max_iterations=10,
            allowed_collection_ids=[],
            policy_ids=verdict.inherited_policy_ids,
        )

    async def _register_civilization_member(
        self,
        *,
        agent_id: str,
        parent_agent_id: str,
        depth: int,
        budget_usd: float,
    ) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(text("""
                    INSERT INTO civilization_agents
                        (id, civilization_id, tenant_id, agent_id, role, parent_agent_id,
                         reputation, status, depth, budget_usd, budget_spent_usd,
                         spawned_at, last_active_at)
                    VALUES
                        (:id, :cid, :tid, :aid, 'worker', :parent, 0.5, 'active',
                         :depth, :budget, 0.0, NOW(), NOW())
                    ON CONFLICT (civilization_id, agent_id) DO UPDATE
                        SET status = 'active', last_active_at = NOW()
                """), {
                    "id": uuid.uuid4().hex, "cid": self._civilization_id,
                    "tid": self._tenant_id, "aid": agent_id,
                    "parent": parent_agent_id, "depth": depth, "budget": budget_usd,
                })
        except Exception as exc:
            logger.warning("governor_register_member_failed", error=str(exc))

    async def _retire_member(self, member_id: str, agent_id: str) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(text("""
                    UPDATE civilization_agents
                    SET status = 'retired', retired_at = NOW()
                    WHERE id = :id AND tenant_id = :tid
                """), {"id": member_id, "tid": self._tenant_id})
        except Exception as exc:
            logger.warning("governor_retire_failed", member_id=member_id, error=str(exc))

    async def _retire_member_by_agent_id(self, agent_id: str) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(text("""
                    UPDATE civilization_agents
                    SET status = 'retired', retired_at = NOW()
                    WHERE agent_id = :aid AND civilization_id = :cid AND tenant_id = :tid
                """), {"aid": agent_id, "cid": self._civilization_id, "tid": self._tenant_id})
        except Exception as exc:
            logger.warning("governor_retire_by_agent_failed", error=str(exc))

    async def _auto_pause(self, reasons: list[str]) -> None:
        await self.pause()
        # Raise HITL if configured
        if self._hitl is not None:
            try:
                from app.tenancy.context import PlanTier, TenantContext
                tenant_ctx = TenantContext(
                    tenant_id=self._tenant_id,
                    plan=PlanTier.ENTERPRISE,
                    api_key_id="governor",
                )
                await self._hitl.request_approval(
                    goal_id=f"civ_breach_{self._civilization_id}",
                    step_description=(
                        f"Civilization breach: {'; '.join(reasons)}. Approve to resume."
                    ),
                    tenant_ctx=tenant_ctx,
                    risk_level="high",
                    required_approvers=1,
                )
            except Exception as exc:
                logger.warning("governor_hitl_breach_failed", error=str(exc))

    async def _set_civilization_status(self, status: str) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(text(
                    "UPDATE civilizations SET status=:status, updated_at=NOW() "
                    "WHERE id=:id AND tenant_id=:tid"
                ), {"status": status, "id": self._civilization_id, "tid": self._tenant_id})
        except Exception as exc:
            logger.warning("governor_set_status_failed", error=str(exc))

    async def _audit_spawn(
        self,
        *,
        requester_agent_id: str,
        requested_capability: str,
        goal_text: str,
        verdict: SpawnVerdict,
    ) -> None:
        if self._db is None:
            return
        try:
            import json

            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(text("""
                    INSERT INTO spawn_requests
                        (id, civilization_id, tenant_id, requester_agent_id, requested_capability,
                         goal_text, decision, reason, verdict, created_at)
                    VALUES
                        (:id, :cid, :tid, :req, :cap, :goal, :dec, :reason, :verdict::jsonb, NOW())
                """), {
                    "id": uuid.uuid4().hex,
                    "cid": self._civilization_id,
                    "tid": self._tenant_id,
                    "req": requester_agent_id,
                    "cap": requested_capability[:200],
                    "goal": goal_text[:500],
                    "dec": verdict.decision.value,
                    "reason": verdict.reason[:500],
                    "verdict": json.dumps(verdict.snapshot),
                })
        except Exception as exc:
            logger.warning("governor_audit_spawn_failed", error=str(exc))
