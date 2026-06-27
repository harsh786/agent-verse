"""CivilizationOrchestrator — the runtime loop for the civilization.

The ONLY component that enqueues agent Celery tasks.
Ticks the society, checks breaches, emits events, manages the lifecycle.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.civilization.events import CivEventType, emit_event
from app.observability.logging import get_logger

logger = get_logger(__name__)


class CivilizationOrchestrator:
    """Runtime coordinator for a civilization.

    Responsibilities:
    - Accept incoming goals and route them into the society
    - Trigger debates when needed
    - Run collective learning pipeline
    - Check Constitution breaches (tick)
    - Emit events for SSE/WS subscribers
    """

    def __init__(
        self,
        *,
        civilization_id: str,
        tenant_id: str,
        constitution: Any,  # app.civilization.models.Constitution
        governor: Any,  # Governor
        society: Any,  # Society
        bus: Any,  # CivilizationBus
        blackboard: Any,  # Blackboard
        goal_service: Any = None,
        debate_orchestrator: Any = None,
        supervisor_agent: Any = None,
        learning_pipeline: Any = None,
        db_session_factory: Any = None,
        redis: Any = None,
        celery_task_queue: Any = None,
        tenant_ctx: Any = None,
    ) -> None:
        self._civ_id = civilization_id
        self._tenant_id = tenant_id
        self._constitution = constitution
        self._governor = governor
        self._society = society
        self._bus = bus
        self._blackboard = blackboard
        self._goal_service = goal_service
        self._debate = debate_orchestrator
        self._supervisor = supervisor_agent
        self._learning = learning_pipeline
        self._db = db_session_factory
        self._redis = redis
        self._task_queue = celery_task_queue
        self._tenant_ctx = tenant_ctx

    async def submit_goal(self, goal: str, priority: str = "normal") -> dict:
        """Route an incoming goal into the civilization society."""
        # Check if paused
        if self._governor is not None and self._redis is not None:
            from app.scaling.tasks import _get_sync_redis

            try:
                sync_r = _get_sync_redis()
                if self._governor.is_paused_sync(sync_r):
                    return {"status": "rejected", "reason": "Civilization is paused"}
            except Exception:
                pass

        goal_id = f"civ_{uuid.uuid4().hex}"

        # Route to best member
        routing = await self._society.route_goal(goal=goal, tenant_ctx=self._tenant_ctx)
        mode = routing.get("mode", "single_agent")
        agent_id = routing.get("agent_id")

        # Emit routing event
        await emit_event(
            civilization_id=self._civ_id,
            tenant_id=self._tenant_id,
            event_type=CivEventType.GOAL_SUBMITTED,
            payload={
                "goal": goal[:200],
                "mode": mode,
                "routed_to": agent_id,
                "goal_id": goal_id,
            },
            db=self._db,
            redis=self._redis,
        )

        # Dispatch based on routing mode
        if mode == "needs_new_agent":
            # Governor will spawn a new agent
            if self._governor is not None:
                from app.civilization.models import SpawnDecision

                verdict = await self._governor.evaluate_spawn_request(
                    requester_agent_id="orchestrator",
                    requested_capability=goal[:100],
                    goal_text=goal,
                    depth=0,
                    parent_budget_usd=self._constitution.per_agent_budget_usd,
                    parent_policy_ids=list(self._constitution.inherited_policy_ids),
                    tenant_ctx=self._tenant_ctx,
                )
                if verdict.decision == SpawnDecision.APPROVED:
                    agent_record = await self._governor.spawn_agent(
                        verdict=verdict,
                        requested_capability=goal[:100],
                        goal_text=goal,
                        requester_agent_id="orchestrator",
                        depth=0,
                        tenant_ctx=self._tenant_ctx,
                    )
                    agent_id = agent_record.get("agent_id")
                    await emit_event(
                        civilization_id=self._civ_id,
                        tenant_id=self._tenant_id,
                        event_type=CivEventType.AGENT_SPAWNED,
                        payload={"agent_id": agent_id, "goal": goal[:200], "depth": 0},
                        db=self._db,
                        redis=self._redis,
                    )
                else:
                    return {
                        "status": "rejected",
                        "reason": verdict.reason,
                        "goal_id": goal_id,
                    }

        elif mode == "multi_agent" and self._supervisor is not None:
            # SupervisorAgent decomposes and dispatches in parallel
            try:
                result = await self._supervisor.run(
                    goal=goal, tenant_ctx=self._tenant_ctx
                )
                return {
                    "status": "accepted",
                    "mode": "multi_agent",
                    "goal_id": goal_id,
                    "supervisor_result": getattr(result, "synthesis", str(result))[:500],
                }
            except Exception as exc:
                logger.warning("orchestrator_supervisor_failed", error=str(exc))
                # Fall through to single agent

        # Submit goal via GoalService for the selected agent
        result_goal_id = goal_id
        if self._goal_service is not None and agent_id:
            try:
                result = await self._goal_service.submit_goal(
                    goal=goal,
                    tenant_ctx=self._tenant_ctx,
                    agent_id=agent_id,
                    priority=priority,
                    execution_context={
                        "civilization_id": self._civ_id,
                        "orchestrator_goal_id": goal_id,
                    },
                )
                result_goal_id = result.get("goal_id", goal_id)
            except Exception as exc:
                logger.warning("orchestrator_goal_submit_failed", error=str(exc))

        return {
            "status": "accepted",
            "mode": mode,
            "goal_id": result_goal_id,
            "agent_id": agent_id,
            "routing_confidence": routing.get("confidence", 0.0),
        }

    async def trigger_debate(
        self,
        *,
        topic: str,
        claim_a: dict,
        claim_b: dict,
        initiator_agent_id: str,
    ) -> dict:
        """Trigger a peer debate on conflicting claims."""
        debate_id = uuid.uuid4().hex

        await emit_event(
            civilization_id=self._civ_id,
            tenant_id=self._tenant_id,
            event_type=CivEventType.DEBATE_STARTED,
            payload={
                "debate_id": debate_id,
                "topic": topic,
                "claim_a": claim_a,
                "claim_b": claim_b,
                "initiator": initiator_agent_id,
            },
            db=self._db,
            redis=self._redis,
        )

        result: dict = {
            "debate_id": debate_id,
            "topic": topic,
            "status": "concluded",
            "consensus": None,
        }

        if self._debate is not None:
            try:
                # Update member statuses to "debating"
                members = await self._society.load_members()
                debate_participants = [m["agent_id"] for m in members[:3]]  # top-3 by reputation
                for pid in debate_participants:
                    await self._society.update_member_status(pid, "debating")

                debate_result = await self._debate.run(
                    goal=(
                        f"Debate: {topic}. "
                        f"Claim A: {claim_a.get('content', '')}. "
                        f"Claim B: {claim_b.get('content', '')}"
                    ),
                    tenant_ctx=self._tenant_ctx,
                    rounds=2,
                )
                consensus = getattr(debate_result, "consensus", str(debate_result))
                result["consensus"] = consensus[:500] if consensus else None
                result["consensus_level"] = getattr(debate_result, "confidence", 0.7)

                # Post consensus to blackboard
                if self._blackboard and consensus:
                    await self._blackboard.post(
                        author_agent_id="debate_orchestrator",
                        topic=topic,
                        content=f"[DEBATE CONSENSUS] {consensus[:400]}",
                        confidence=result.get("consensus_level", 0.7),
                    )

                # Restore member statuses
                for pid in debate_participants:
                    await self._society.update_member_status(pid, "active")

            except Exception as exc:
                logger.warning("orchestrator_debate_failed", error=str(exc))
                result["error"] = str(exc)

        await emit_event(
            civilization_id=self._civ_id,
            tenant_id=self._tenant_id,
            event_type=CivEventType.DEBATE_CONCLUDED,
            payload=result,
            db=self._db,
            redis=self._redis,
        )

        # Post to bus
        await self._bus.publish(
            from_agent_id=initiator_agent_id,
            topic="debate",
            payload=result,
        )

        return result

    async def tick(self) -> dict:
        """Periodic tick — breach check + auto-retire + learning pipeline.

        Called by Celery beat every 30s.
        """
        results: dict = {"tick_ts": datetime.now(UTC).isoformat()}

        # 1. Check Constitution breach
        if self._governor is not None:
            try:
                breach = await self._governor.check_breach()
                results["breach"] = {"detected": breach.breached, "reasons": breach.reasons}
            except Exception as exc:
                logger.warning("orchestrator_tick_breach_failed", error=str(exc))

        # 2. Auto-retire idle/low-reputation members
        if self._governor is not None:
            try:
                retired = await self._governor.auto_retire_idle()
                results["auto_retired"] = retired
                for agent_id in retired:
                    await emit_event(
                        civilization_id=self._civ_id,
                        tenant_id=self._tenant_id,
                        event_type=CivEventType.AGENT_RETIRED,
                        payload={"agent_id": agent_id, "reason": "auto_retire"},
                        db=self._db,
                        redis=self._redis,
                    )
            except Exception as exc:
                logger.warning("orchestrator_tick_retire_failed", error=str(exc))

        # 3. Learning pipeline step
        if self._learning is not None:
            try:
                learn_result = await self._learning.run_step()
                results["learning"] = learn_result
            except Exception as exc:
                logger.warning("orchestrator_tick_learning_failed", error=str(exc))

        return results

    async def get_status(self) -> dict:
        """Get full civilization status for API response."""
        society_metrics = await self._society.get_metrics()
        lineage = await self._society.get_lineage_graph()
        return {
            "civilization_id": self._civ_id,
            "tenant_id": self._tenant_id,
            "society": society_metrics,
            "lineage": lineage,
            "constitution": (
                self._constitution.to_dict()
                if hasattr(self._constitution, "to_dict")
                else {}
            ),
        }
