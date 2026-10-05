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
from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger
from app.observability.tracing import get_tracer

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


class CivilizationControlError(RuntimeError):
    """A pause/resume/kill could not be carried out (no DB/Redis, or a write failed).

    Raised instead of reporting success for a control that did not happen
    (a08-F182-02/03). An unknown civilization or member raises ``LookupError``.
    """


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
        with _tracer.start_as_current_span("civ.governor.evaluate_spawn") as span:
            span.set_attribute("civilization_id", self._civilization_id)
            span.set_attribute("depth", depth)
            span.set_attribute("requester_agent_id", requester_agent_id)
            span.set_attribute("requested_capability", requested_capability[:100])

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
            span.set_attribute("decision", verdict.decision.value)

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

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                rows = (
                    await session.execute(
                        text("""
                    SELECT id, agent_id, reputation, last_active_at
                    FROM civilization_agents
                    WHERE civilization_id = :cid AND tenant_id = :tid
                      AND status = 'active'
                    ORDER BY reputation ASC, last_active_at ASC
                """),
                        {"cid": self._civilization_id, "tid": self._tenant_id},
                    )
                ).fetchall()

            # Count active members to ensure min_viable_roster
            active_count = len(rows)
            from datetime import timedelta

            idle_cutoff = datetime.now(UTC) - timedelta(seconds=self._constitution.idle_ttl_seconds)

            for row in rows:
                if active_count <= self._constitution.min_viable_roster:
                    break  # Keep minimum viable roster
                member_id, agent_id, reputation, last_active = row
                # Fix timezone-aware comparison: ensure both sides are UTC-aware
                if last_active is not None and last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=UTC)
                should_retire = (
                    reputation is not None and reputation < self._constitution.reputation_floor
                ) or (last_active is not None and last_active < idle_cutoff)
                if should_retire:
                    await self._retire_member(member_id, agent_id)
                    retired.append(agent_id)
                    active_count -= 1
        except Exception as exc:
            logger.warning("governor_auto_retire_failed", error=str(exc))
        return retired

    async def kill_agent(self, agent_id: str, tenant_ctx: Any) -> dict[str, int]:
        """Kill a civilization member: retire it durably, then cancel its running goals.

        Retiring removes it from routing and A2A dispatch (both require an active
        member). Its in-flight goals are cancelled through the shared goal
        lifecycle (Redis cancel signal + conditional status write), which every
        runner honours at its next step. The ``civ_kill_agent`` flag this used
        to set was read by nothing (a08-F182-01). Unknown member → LookupError;
        any failure raises (a08-F182-03).
        """
        if not await self._retire_member_by_agent_id(agent_id):
            raise LookupError(f"agent {agent_id!r} is not a member of this civilization")
        return await self._cancel_member_goals(agent_id)

    async def _cancel_member_goals(self, agent_id: str, *, batch_size: int = 200) -> dict[str, int]:
        """Cancel the member's non-terminal goals in this civilization, in keyset batches."""
        from sqlalchemy import text

        from app.reliability.goal_lifecycle import signal_cancel

        if self._db is None:
            raise CivilizationControlError("no database configured")
        select_sql = text(
            "SELECT id FROM goals WHERE tenant_id = :tid AND agent_id = :aid "
            "AND execution_context->>'civilization_id' = :cid "
            "AND status NOT IN ('complete', 'failed', 'cancelled') AND id > :after "
            "ORDER BY id LIMIT :lim"
        )
        update_sql = text(
            "UPDATE goals SET status = 'cancelled', "
            "error_message = 'Cancelled: civilization member was killed' "
            "WHERE tenant_id = :tid AND id = ANY(:ids) "
            "AND status NOT IN ('complete', 'failed', 'cancelled')"
        )
        after = ""
        cancelled = signal_failures = 0
        try:
            while True:
                params = {
                    "tid": self._tenant_id,
                    "aid": agent_id,
                    "cid": self._civilization_id,
                    "after": after,
                    "lim": batch_size,
                }
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, self._tenant_id),
                ):
                    ids = [str(r[0]) for r in await session.execute(select_sql, params)]
                if not ids:
                    break
                for gid in ids:
                    try:
                        await signal_cancel(gid, self._redis, strict=True)
                    except Exception:
                        signal_failures += 1
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, self._tenant_id),
                ):
                    res = await session.execute(update_sql, {"tid": self._tenant_id, "ids": ids})
                    cancelled += int(getattr(res, "rowcount", 0) or 0)
                after = ids[-1]
                if len(ids) < batch_size:
                    break
        except Exception as exc:
            raise CivilizationControlError(f"could not cancel the member's goals: {exc}") from exc
        logger.info(
            "governor_member_killed",
            civilization_id=self._civilization_id,
            agent_id=agent_id,
            goals_cancelled=cancelled,
            signal_failures=signal_failures,
        )
        return {"goals_cancelled": cancelled, "signal_failures": signal_failures}

    async def pause(self) -> None:
        """Pause the civilization — stops new spawns, signals agents to halt at next checkpoint.

        The shared flag is written first (with no TTL: a pause never lapses on its
        own) so a failure part-way leaves the civilization more stopped, never
        less; any failure raises (a08-F182-02).
        """
        if self._redis is not None:
            try:
                await self._redis.set(f"civ_paused:{self._tenant_id}:{self._civilization_id}", "1")
            except Exception as exc:
                raise CivilizationControlError(f"pause flag could not be set: {exc}") from exc
        await self._set_civilization_status("paused")
        # GAP 2: Emit CIVILIZATION_PAUSED event
        try:
            from app.civilization.events import CivEventType, emit_event

            await emit_event(
                civilization_id=self._civilization_id,
                tenant_id=self._tenant_id,
                event_type=CivEventType.CIVILIZATION_PAUSED,
                payload={"reason": "operator_request"},
                db=self._db,
                redis=self._redis,
            )
        except Exception:
            pass

    async def resume(self) -> None:
        """Resume a paused civilization. Any failure raises (it stays paused)."""
        await self._set_civilization_status("active")
        if self._redis is not None:
            try:
                await self._redis.delete(f"civ_paused:{self._tenant_id}:{self._civilization_id}")
            except Exception as exc:
                raise CivilizationControlError(f"pause flag could not be cleared: {exc}") from exc
        # GAP 2: Emit CIVILIZATION_RESUMED event
        try:
            from app.civilization.events import CivEventType, emit_event

            await emit_event(
                civilization_id=self._civilization_id,
                tenant_id=self._tenant_id,
                event_type=CivEventType.CIVILIZATION_RESUMED,
                payload={"reason": "operator_request"},
                db=self._db,
                redis=self._redis,
            )
        except Exception:
            pass

    async def is_paused(self) -> bool:
        """Async pause-flag read for request paths; a Redis error propagates."""
        if self._redis is None:
            return False
        value = await self._redis.get(f"civ_paused:{self._tenant_id}:{self._civilization_id}")
        return bool(value)

    def is_paused_sync(self, redis_sync: Any) -> bool:
        """Synchronous check for Celery workers."""
        if redis_sync is None:
            return False
        try:
            return bool(redis_sync.get(f"civ_paused:{self._tenant_id}:{self._civilization_id}"))
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

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                row = (
                    await session.execute(
                        text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status != 'retired') as total,
                        COUNT(*) FILTER (WHERE status = 'active') as concurrent,
                        COALESCE(SUM(COALESCE(budget_spent_usd, 0)), 0) as spent,
                        COUNT(*) FILTER (
                            WHERE spawned_at > NOW() - INTERVAL '1 minute'
                        ) as spawn_rate
                    FROM civilization_agents
                    WHERE civilization_id = :cid AND tenant_id = :tid
                """),
                        {"cid": self._civilization_id, "tid": self._tenant_id},
                    )
                ).fetchone()
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
                if agent.get("goal_template", "").lower().find(
                    capability.lower()
                ) != -1 and await self._is_idle_member(agent.get("agent_id", "")):
                    return agent
        except Exception:
            pass
        return None

    async def _is_idle_member(self, agent_id: str) -> bool:
        if self._db is None:
            return False
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                row = (
                    await session.execute(
                        text(
                            "SELECT status FROM civilization_agents "
                            "WHERE agent_id=:aid AND civilization_id=:cid AND tenant_id=:tid"
                        ),
                        {
                            "aid": agent_id,
                            "cid": self._civilization_id,
                            "tid": self._tenant_id,
                        },
                    )
                ).fetchone()
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
                        f"Create an agent for capability: {requested_capability}. Goal: {goal_text}"
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

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                await session.execute(
                    text("""
                    INSERT INTO civilization_agents
                        (id, civilization_id, tenant_id, agent_id, role, parent_agent_id,
                         reputation, status, depth, budget_usd, budget_spent_usd,
                         spawned_at, last_active_at)
                    VALUES
                        (:id, :cid, :tid, :aid, 'worker', :parent, 0.5, 'active',
                         :depth, :budget, 0.0, NOW(), NOW())
                    ON CONFLICT (civilization_id, agent_id) DO UPDATE
                        SET status = 'active', last_active_at = NOW()
                """),
                    {
                        "id": uuid.uuid4().hex,
                        "cid": self._civilization_id,
                        "tid": self._tenant_id,
                        "aid": agent_id,
                        "parent": parent_agent_id,
                        "depth": depth,
                        "budget": budget_usd,
                    },
                )
        except Exception as exc:
            logger.warning("governor_register_member_failed", error=str(exc))

    async def _retire_member(self, member_id: str, agent_id: str) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                await session.execute(
                    text("""
                    UPDATE civilization_agents
                    SET status = 'retired', retired_at = NOW()
                    WHERE id = :id AND tenant_id = :tid
                """),
                    {"id": member_id, "tid": self._tenant_id},
                )
        except Exception as exc:
            logger.warning("governor_retire_failed", member_id=member_id, error=str(exc))

    async def _retire_member_by_agent_id(self, agent_id: str) -> int:
        """Retire the member; returns the rows matched (0 = not a member). Raises on error."""
        if self._db is None:
            raise CivilizationControlError("no database configured")
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                result = await session.execute(
                    text("""
                    UPDATE civilization_agents
                    SET status = 'retired', retired_at = COALESCE(retired_at, NOW())
                    WHERE agent_id = :aid AND civilization_id = :cid AND tenant_id = :tid
                """),
                    {"aid": agent_id, "cid": self._civilization_id, "tid": self._tenant_id},
                )
                return int(getattr(result, "rowcount", 0) or 0)
        except Exception as exc:
            raise CivilizationControlError(f"member could not be retired: {exc}") from exc

    async def _auto_pause(self, reasons: list[str]) -> None:
        await self.pause()
        # GAP 2: Emit BREACH_DETECTED and CIVILIZATION_PAUSED events
        try:
            from app.civilization.events import CivEventType, emit_event

            await emit_event(
                civilization_id=self._civilization_id,
                tenant_id=self._tenant_id,
                event_type=CivEventType.BREACH_DETECTED,
                payload={"reasons": reasons, "action": "auto_paused"},
                db=self._db,
                redis=self._redis,
            )
            await emit_event(
                civilization_id=self._civilization_id,
                tenant_id=self._tenant_id,
                event_type=CivEventType.CIVILIZATION_PAUSED,
                payload={"reason": "breach_detected", "reasons": reasons},
                db=self._db,
                redis=self._redis,
            )
        except Exception as exc:
            logger.warning("governor_pause_event_failed", error=str(exc))
        # Raise HITL if configured
        if self._hitl is not None:
            await self._raise_breach_approval(reasons)

    async def _raise_breach_approval(self, reasons: list[str]) -> None:
        """File the resume approval durably (HITL-01): the civilization stays
        paused either way; a request that could not be persisted is logged as an
        error instead of living in one process's memory."""
        try:
            from app.agent.hitl_filing import file_persisted_approval
            from app.tenancy.context import PlanTier, TenantContext

            tenant_ctx = TenantContext(
                tenant_id=self._tenant_id,
                plan=PlanTier.ENTERPRISE,
                api_key_id="governor",
            )
            await file_persisted_approval(
                self._hitl,
                goal_id=f"civ_breach_{self._civilization_id}",
                action=f"Civilization breach: {'; '.join(reasons)}. Approve to resume.",
                risk_level="high",
                tenant_ctx=tenant_ctx,
            )
        except Exception as exc:
            logger.error("governor_hitl_breach_failed", error=str(exc))

    async def _set_civilization_status(self, status: str) -> None:
        """Persist the status; raises when it cannot (a08-F182-02), LookupError if unknown."""
        if self._db is None:
            raise CivilizationControlError("no database configured")
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                result = await session.execute(
                    text(
                        "UPDATE civilizations SET status=:status, updated_at=NOW() "
                        "WHERE id=:id AND tenant_id=:tid"
                    ),
                    {"status": status, "id": self._civilization_id, "tid": self._tenant_id},
                )
                updated = int(getattr(result, "rowcount", 0) or 0)
        except Exception as exc:
            raise CivilizationControlError(f"status could not be saved: {exc}") from exc
        if not updated:
            raise LookupError(f"civilization {self._civilization_id!r} not found")

    async def _audit_spawn(
        self,
        *,
        requester_agent_id: str,
        requested_capability: str,
        goal_text: str,
        verdict: SpawnVerdict,
    ) -> None:
        from app.civilization.metrics import record_spawn

        record_spawn(
            tenant_id=self._tenant_id,
            civilization_id=self._civilization_id,
            decision=verdict.decision.value,
        )

        if self._db is None:
            # Even without DB, still record in audit log if available
            if self._audit_log is not None:
                try:
                    from app.governance.audit import AuditEvent
                    from app.governance.permissions import ActionLevel
                    from app.tenancy.context import PlanTier, TenantContext

                    self._audit_log.record(
                        AuditEvent(
                            goal_id=f"spawn_{self._civilization_id}",
                            tool_name="civilization_spawn",
                            action_level=ActionLevel.ALLOW_LOG,
                            outcome=verdict.decision.value,
                            note=(
                                f"requester={requester_agent_id[:50]} "
                                f"cap={requested_capability[:100]} "
                                f"reason={verdict.reason[:200]}"
                            ),
                            api_key_id="governor",
                        ),
                        tenant_ctx=TenantContext(
                            tenant_id=self._tenant_id,
                            plan=PlanTier.ENTERPRISE,
                            api_key_id="governor",
                        ),
                    )
                except Exception as exc:
                    import logging

                    logging.getLogger(__name__).warning("governor_audit_log_failed: %s", exc)
            return
        try:
            import json

            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                await session.execute(
                    text("""
                    INSERT INTO spawn_requests
                        (id, civilization_id, tenant_id, requester_agent_id, requested_capability,
                         goal_text, decision, reason, verdict, created_at)
                    VALUES
                        (:id, :cid, :tid, :req, :cap, :goal, :dec, :reason,
                         CAST(:verdict AS jsonb), NOW())
                """),
                    {
                        "id": uuid.uuid4().hex,
                        "cid": self._civilization_id,
                        "tid": self._tenant_id,
                        "req": requester_agent_id,
                        "cap": requested_capability[:200],
                        "goal": goal_text[:500],
                        "dec": verdict.decision.value,
                        "reason": verdict.reason[:500],
                        "verdict": json.dumps(verdict.snapshot),
                    },
                )
        except Exception as exc:
            logger.warning("governor_audit_spawn_failed", error=str(exc))

        # GAP 1: Record in AuditLog for compliance trail
        if self._audit_log is not None:
            try:
                from app.governance.audit import AuditEvent
                from app.governance.permissions import ActionLevel
                from app.tenancy.context import PlanTier, TenantContext

                self._audit_log.record(
                    AuditEvent(
                        goal_id=f"spawn_{self._civilization_id}",
                        tool_name="civilization_spawn",
                        action_level=ActionLevel.ALLOW_LOG,
                        outcome=verdict.decision.value,
                        note=(
                            f"requester={requester_agent_id[:50]} "
                            f"cap={requested_capability[:100]} "
                            f"reason={verdict.reason[:200]}"
                        ),
                        api_key_id="governor",
                    ),
                    tenant_ctx=TenantContext(
                        tenant_id=self._tenant_id,
                        plan=PlanTier.ENTERPRISE,
                        api_key_id="governor",
                    ),
                )
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning("governor_audit_log_failed: %s", exc)
