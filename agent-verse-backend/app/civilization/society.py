"""Society — civilization membership, reputation tracking, goal routing.

Reputation: seeds at 0.5, updated by EvalRunner EWMA.
Routing: AgentRouter for best-member selection.
"""

from __future__ import annotations

import contextlib
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

logger = get_logger(__name__)

_REPUTATION_EWMA_ALPHA = 0.2  # Weight for new observations in EWMA


def _normalize_connectors(value: Any) -> list[str]:
    """Coerce a connector_ids DB value into a list[str].

    The ``agents.connector_ids`` column is JSON; the asyncpg dialect usually
    hands it back already deserialized as a list, but a raw ``text()`` read can
    occasionally return the raw JSON string. Handle both, and never raise.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        import json

        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return []
        return [str(x) for x in decoded] if isinstance(decoded, list) else []
    return []


class Society:
    """Manages civilization membership, reputation, and goal routing.

    The Society knows who is in the civilization and how they're performing.
    The Governor creates/retires members; the Society tracks their state.
    """

    def __init__(
        self,
        *,
        civilization_id: str,
        tenant_id: str,
        db_session_factory: Any = None,
        agent_router: Any = None,
        eval_runner: Any = None,
        bus: Any = None,
    ) -> None:
        self._civ_id = civilization_id
        self._tenant_id = tenant_id
        self._db = db_session_factory
        self._router = agent_router
        self._eval_runner = eval_runner
        self._bus = bus
        # In-memory cache of active members: {agent_id: member_dict}
        self._members: dict[str, dict] = {}

    async def load_members(self) -> list[dict]:
        """Load active members from DB into memory cache."""
        if self._db is None:
            return list(self._members.values())
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                sqlalchemy_rls_context(session, self._tenant_id),
            ):
                rows = (
                    await session.execute(
                        text("""
                    SELECT ca.id, ca.agent_id, ca.role, ca.parent_agent_id, ca.reputation,
                           ca.status, ca.depth, ca.budget_usd, ca.budget_spent_usd,
                           ca.spawned_at, ca.last_active_at,
                           a.name, a.goal_template, a.connector_ids
                    FROM civilization_agents ca
                    LEFT JOIN agents a
                        ON a.id = ca.agent_id AND a.tenant_id = ca.tenant_id
                    WHERE ca.civilization_id = :cid AND ca.tenant_id = :tid
                      AND ca.status NOT IN ('retired')
                    ORDER BY ca.reputation DESC
                """),
                        {"cid": self._civ_id, "tid": self._tenant_id},
                    )
                ).fetchall()
            members = []
            for r in rows:
                member = self._row_to_member(r)
                self._members[member["agent_id"]] = member
                members.append(member)
            return members
        except Exception as exc:
            logger.warning("society_load_members_failed", error=str(exc))
            return list(self._members.values())

    async def get_member(self, agent_id: str) -> dict | None:
        """Get a single member by agent_id."""
        if agent_id in self._members:
            return self._members[agent_id]
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with (
                    self._db() as session,
                    sqlalchemy_rls_context(session, self._tenant_id),
                ):
                    row = (
                        await session.execute(
                            text("""
                        SELECT ca.id, ca.agent_id, ca.role, ca.parent_agent_id, ca.reputation,
                               ca.status, ca.depth, ca.budget_usd, ca.budget_spent_usd,
                               ca.spawned_at, ca.last_active_at,
                               a.name, a.goal_template, a.connector_ids
                        FROM civilization_agents ca
                        LEFT JOIN agents a
                            ON a.id = ca.agent_id AND a.tenant_id = ca.tenant_id
                        WHERE ca.agent_id = :aid AND ca.civilization_id = :cid
                          AND ca.tenant_id = :tid
                    """),
                            {"aid": agent_id, "cid": self._civ_id, "tid": self._tenant_id},
                        )
                    ).fetchone()
                if row:
                    member = self._row_to_member(row)
                    self._members[agent_id] = member
                    return member
            except Exception as exc:
                logger.warning("society_get_member_failed", error=str(exc))
        return None

    def _row_to_member(self, r: Any) -> dict:
        """Map a civilization_agents (LEFT JOIN agents) row to a member dict.

        The last three columns (``name``, ``goal_template``, ``connector_ids``)
        come from the joined ``agents`` row and feed capability-aware routing.
        Index-safe access keeps older/narrower rows (e.g. unit-test fakes that
        supply only the base 11 columns) working with sensible defaults.
        """
        return {
            "id": r[0],
            "agent_id": r[1],
            "role": r[2],
            "parent_agent_id": r[3],
            "reputation": float(r[4] or 0.5),
            "status": r[5],
            "depth": r[6],
            "budget_usd": float(r[7] or 0),
            "budget_spent_usd": float(r[8] or 0),
            "spawned_at": r[9].isoformat() if r[9] else "",
            "last_active_at": r[10].isoformat() if r[10] else "",
            "name": (r[11] if len(r) > 11 else None) or "",
            "goal_template": (r[12] if len(r) > 12 else None) or "",
            "connector_ids": _normalize_connectors(r[13] if len(r) > 13 else None),
            "civilization_id": self._civ_id,
        }

    async def update_reputation(
        self,
        *,
        agent_id: str,
        new_score: float,
    ) -> float:
        """Update reputation via EWMA. Returns new reputation value.

        EWMA formula: new_rep = alpha * new_score + (1 - alpha) * old_rep
        """
        new_score = min(1.0, max(0.0, new_score))
        current = await self._get_current_reputation(agent_id)
        new_rep = _REPUTATION_EWMA_ALPHA * new_score + (1 - _REPUTATION_EWMA_ALPHA) * current

        await self._persist_reputation(agent_id, new_rep)
        if agent_id in self._members:
            self._members[agent_id]["reputation"] = new_rep

        logger.info(
            "society_reputation_updated",
            agent_id=agent_id,
            old=round(current, 3),
            new=round(new_rep, 3),
            score=round(new_score, 3),
            civilization_id=self._civ_id,
        )

        # Publish lifecycle event
        if self._bus is not None:
            with contextlib.suppress(Exception):
                await self._bus.publish(
                    from_agent_id=agent_id,
                    topic="lifecycle",
                    payload={
                        "event": "reputation_updated",
                        "agent_id": agent_id,
                        "old_reputation": current,
                        "new_reputation": new_rep,
                    },
                )

        return new_rep

    async def update_member_status(self, agent_id: str, status: str) -> None:
        """Update member status (active/idle/debating/spawning/failed)."""
        if self._db is not None:
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
                        SET status = :status, last_active_at = NOW()
                        WHERE agent_id = :aid AND civilization_id = :cid AND tenant_id = :tid
                    """),
                        {
                            "status": status,
                            "aid": agent_id,
                            "cid": self._civ_id,
                            "tid": self._tenant_id,
                        },
                    )
            except Exception as exc:
                logger.warning("society_update_status_failed", error=str(exc))
        if agent_id in self._members:
            self._members[agent_id]["status"] = status

    async def update_budget_spent(self, agent_id: str, additional_spent_usd: float) -> None:
        """Record additional spend for a member (for cost rollup)."""
        if self._db is not None:
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
                        SET budget_spent_usd = budget_spent_usd + :spent
                        WHERE agent_id = :aid AND civilization_id = :cid AND tenant_id = :tid
                    """),
                        {
                            "spent": additional_spent_usd,
                            "aid": agent_id,
                            "cid": self._civ_id,
                            "tid": self._tenant_id,
                        },
                    )
            except Exception as exc:
                logger.warning("society_update_budget_failed", error=str(exc))
        if agent_id in self._members:
            self._members[agent_id]["budget_spent_usd"] = (
                self._members[agent_id].get("budget_spent_usd", 0.0) + additional_spent_usd
            )

    async def route_goal(
        self,
        *,
        goal: str,
        tenant_ctx: Any,
    ) -> dict:
        """Route an incoming goal to the best society member.

        Returns routing decision: {agent_id, mode, reason, confidence}
        """
        members = await self.load_members()
        active_agents = [
            {
                "agent_id": m["agent_id"],
                "reputation": m["reputation"],
                "name": m.get("name", ""),
                "goal_template": m.get("goal_template", ""),
                "connector_ids": m.get("connector_ids", []),
            }
            for m in members
            if m["status"] in ("active", "idle")
        ]

        if not active_agents:
            return {
                "mode": "needs_new_agent",
                "reason": "no active members",
                "agent_id": None,
                "confidence": 0.0,
            }

        if self._router is not None:
            try:
                decision = await self._router.route(
                    goal=goal, tenant_ctx=tenant_ctx, available_agents=active_agents
                )
                # Boost routing by reputation when we have a match
                if decision.agent_id and decision.agent_id in {
                    a["agent_id"] for a in active_agents
                }:
                    rep = self._members.get(decision.agent_id, {}).get("reputation", 0.5)
                    return {
                        "agent_id": decision.agent_id,
                        "mode": decision.mode,
                        "reason": decision.reason,
                        "confidence": min(1.0, decision.confidence * (0.5 + rep * 0.5)),
                    }
                # Router returned an external agent (not a local member) — return as-is
                if decision.agent_id:
                    return {
                        "agent_id": decision.agent_id,
                        "mode": decision.mode,
                        "reason": decision.reason,
                        "confidence": decision.confidence,
                    }
                # Router returned agent_id=None — fall through to reputation fallback
                logger.info(
                    "society_router_no_match",
                    reason=getattr(decision, "reason", "low_confidence"),
                    confidence=getattr(decision, "confidence", 0.0),
                )
            except Exception as exc:
                logger.warning("society_route_failed", error=str(exc))

        # Fallback: pick highest-reputation active member
        # This fires when: (a) no router, (b) router returned agent_id=None (score < 0.3),
        # (c) router exception. Always guarantees a member is selected.
        best = max(active_agents, key=lambda a: a.get("reputation", 0.5))
        return {
            "agent_id": best["agent_id"],
            "mode": "single_agent",
            "reason": "highest_reputation_fallback",
            "confidence": best.get("reputation", 0.5),
        }

    async def get_lineage_graph(self) -> dict:
        """Get the spawn lineage graph for visualization."""
        members = await self.load_members()
        nodes = []
        edges = []
        for m in members:
            nodes.append(
                {
                    "id": m["agent_id"],
                    "label": m.get("role", "worker"),
                    "status": m["status"],
                    "reputation": m["reputation"],
                    "depth": m["depth"],
                    "budget_spent_usd": m["budget_spent_usd"],
                }
            )
            if m.get("parent_agent_id"):
                edges.append(
                    {
                        "source": m["parent_agent_id"],
                        "target": m["agent_id"],
                        "type": "spawn_lineage",
                    }
                )
        return {"nodes": nodes, "edges": edges, "member_count": len(members)}

    async def get_metrics(self) -> dict:
        """Get live society metrics.

        ``load_members`` deliberately excludes retired rows, so ``retired_members``
        is counted separately (a real DB count when a session is available, else
        the retired entries still present in the in-memory cache) rather than being
        silently reported as zero.
        """
        members = await self.load_members()
        active = [m for m in members if m["status"] == "active"]
        total_budget_spent = sum(m["budget_spent_usd"] for m in members)
        reputations = [m["reputation"] for m in members if m["reputation"] is not None]
        return {
            "total_members": len(members),
            "active_members": len(active),
            "idle_members": len([m for m in members if m["status"] == "idle"]),
            "retired_members": await self._count_retired(),
            "total_budget_spent_usd": total_budget_spent,
            "avg_reputation": sum(reputations) / len(reputations) if reputations else 0.5,
            "max_reputation": max(reputations) if reputations else 0.5,
            "min_reputation": min(reputations) if reputations else 0.5,
        }

    async def _count_retired(self) -> int:
        """Count retired members honestly.

        ``load_members`` filters retired rows out, so counting them from that
        list would always yield 0. Query the DB directly when available; fall
        back to the in-memory cache otherwise.
        """
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with (
                    self._db() as session,
                    sqlalchemy_rls_context(session, self._tenant_id),
                ):
                    row = (
                        await session.execute(
                            text(
                                "SELECT COUNT(*) FROM civilization_agents "
                                "WHERE civilization_id = :cid AND tenant_id = :tid "
                                "AND status = 'retired'"
                            ),
                            {"cid": self._civ_id, "tid": self._tenant_id},
                        )
                    ).fetchone()
                if row is not None:
                    return int(row[0] or 0)
            except Exception as exc:
                logger.warning("society_count_retired_failed", error=str(exc))
        return len([m for m in self._members.values() if m.get("status") == "retired"])

    async def _get_current_reputation(self, agent_id: str) -> float:
        if agent_id in self._members:
            return self._members[agent_id].get("reputation", 0.5)
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with (
                    self._db() as session,
                    sqlalchemy_rls_context(session, self._tenant_id),
                ):
                    row = (
                        await session.execute(
                            text(
                                "SELECT reputation FROM civilization_agents "
                                "WHERE agent_id=:aid AND civilization_id=:cid AND tenant_id=:tid"
                            ),
                            {"aid": agent_id, "cid": self._civ_id, "tid": self._tenant_id},
                        )
                    ).fetchone()
                if row:
                    return float(row[0] or 0.5)
            except Exception:
                pass
        return 0.5  # Default seed

    async def _persist_reputation(self, agent_id: str, reputation: float) -> None:
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
                    SET reputation = :rep, last_active_at = NOW()
                    WHERE agent_id = :aid AND civilization_id = :cid AND tenant_id = :tid
                """),
                    {
                        "rep": reputation,
                        "aid": agent_id,
                        "cid": self._civ_id,
                        "tid": self._tenant_id,
                    },
                )
        except Exception as exc:
            logger.warning("society_persist_reputation_failed", error=str(exc))
