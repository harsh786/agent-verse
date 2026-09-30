"""Decentralized swarm driver (minimal producer).

There was no swarm runtime at all — only its parts (fenced claims, credentialed
gossip router, convergence check). This driver composes them: agents gossip
advertisements, claim work items under fencing tokens, publish results only with
their current token, and the run stops when convergence says the mandatory work is
done (or a budget/deadline/no-progress bound trips). Every accepted gossip delivery
becomes a topology edge in the persisted read model.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.coordination.pattern_runs.context import RunContext, RunOutcome
from app.coordination.pattern_runs.llm import string_list
from app.coordination.swarm.claims import ClaimRepository, StaleFencingTokenError
from app.coordination.swarm.convergence import ConvergenceItem, evaluate_convergence
from app.coordination.swarm.gossip import GossipRouter
from app.coordination.swarm.models import GossipMessage

_EST_CALL_COST = Decimal("0.01")
_LEASE = timedelta(minutes=5)


async def run_swarm(ctx: RunContext) -> RunOutcome:
    agents = ctx.participants
    keys = {agent: secrets.token_bytes(32) for agent in agents}

    def credential(agent: str) -> str:
        return hmac.new(keys[agent], agent.encode(), hashlib.sha256).hexdigest()

    def validate(agent: str, presented: str) -> bool:
        return agent in keys and hmac.compare_digest(credential(agent), presented)

    decomposition = await ctx.llm.json(
        f"Split this objective into 2-4 independent work items a swarm can do in "
        f'parallel.\nObjective: {ctx.objective}\nReturn {{"work_items": [str]}}',
        step="decompose",
    )
    summaries = string_list(decomposition.get("work_items"), limit=4) or (ctx.objective[:500],)
    items: dict[str, dict[str, Any]] = {
        f"w{index + 1}": {"work_item_id": f"w{index + 1}", "summary": summary, "state": "pending"}
        for index, summary in enumerate(summaries)
    }
    nodes: dict[str, dict[str, Any]] = {
        agent: {"agent_id": agent, "claim_state": "available", "completed_items": 0}
        for agent in agents
    }
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    router = GossipRouter(max_messages_per_origin=200)
    claims = ClaimRepository()

    def gossip(origin: str, message_type: str, payload: str) -> None:
        for peer in agents:
            if peer == origin:
                continue
            message = GossipMessage(
                event_id=uuid.uuid4().hex,
                tenant_id=ctx.tenant_id,
                civilization_id=ctx.session_id,
                origin_agent_id=origin,
                origin_credential=credential(origin),
                message_type=message_type,  # type: ignore[arg-type]
                payload_digest=hashlib.sha256(payload.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(minutes=1),
                hops_remaining=2,
            )
            if router.accept(message, credential_validator=validate) is None:
                continue
            edge = edges.setdefault(
                (origin, peer, message_type),
                {"source": origin, "target": peer, "message_type": message_type, "count": 0},
            )
            edge["count"] += 1

    async def persist(phase: str) -> None:
        await ctx.update_view(
            phase=phase,
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            work_items=list(items.values()),
        )

    for agent in agents:
        gossip(agent, "advertisement", f"{agent}:available")
    await persist("gossiping")

    repeated = 0
    terminal_reason = "round_limit"
    for round_number in range(1, ctx.max_rounds + 1):
        progressed = False
        for agent in agents:
            pending = [item for item in items.values() if item["state"] == "pending"]
            if not pending:
                break
            item = pending[0]
            try:
                claim = await claims.acquire(
                    tenant_id=ctx.tenant_id,
                    work_item_id=f"{ctx.execution_id}:{item['work_item_id']}",
                    owner_agent_id=agent,
                    lease_duration=_LEASE,
                    now=datetime.now(UTC),
                )
            except RuntimeError:
                continue  # another agent holds the lease
            item |= {"state": "claimed", "owner": agent, "fencing_token": claim.fencing_token}
            nodes[agent] |= {
                "claim_state": "claimed",
                "work_item_id": item["work_item_id"],
                "fencing_token": claim.fencing_token,
            }
            gossip(agent, "claim", item["work_item_id"])
            result = await ctx.llm.text(
                f"You are swarm agent '{agent}'. Complete this work item for the objective "
                f"'{ctx.objective}':\n{item['summary']}",
                step=f"work-{round_number}-{item['work_item_id']}",
            )
            try:
                await claims.publish_result(
                    ctx.tenant_id,
                    f"{ctx.execution_id}:{item['work_item_id']}",
                    owner_agent_id=agent,
                    fencing_token=claim.fencing_token,
                    result_reference=f"swarm://{ctx.execution_id}/{item['work_item_id']}",
                )
            except StaleFencingTokenError:
                item |= {"state": "pending", "owner": None}
                continue
            item |= {"state": "completed", "result_excerpt": result[:2_000]}
            nodes[agent] |= {
                "claim_state": "available",
                "completed_items": int(nodes[agent]["completed_items"]) + 1,
            }
            gossip(agent, "result", item["work_item_id"])
            await ctx.say(
                agent,
                f"[swarm] {item['summary']}: {result}",
                step=f"swarm:{item['work_item_id']}",
                message_type="evidence",
            )
            progressed = True
        repeated = 0 if progressed else repeated + 1
        decision = evaluate_convergence(
            tuple(
                ConvergenceItem(
                    work_item_id=item["work_item_id"],
                    mandatory=True,
                    state=item["state"],
                    result_digest=hashlib.sha256(
                        str(item.get("result_excerpt", "")).encode()
                    ).hexdigest(),
                )
                for item in items.values()
            ),
            criteria_met=all(item["state"] == "completed" for item in items.values()),
            spent=_EST_CALL_COST * ctx.llm.calls,
            budget=Decimal(str(ctx.options.get("max_cost_usd", 1.0))),
            deadline=ctx.deadline,
            repeated_results=repeated,
        )
        await persist("converging" if not decision.terminal else "synthesizing")
        if decision.terminal:
            terminal_reason = decision.reason
            break
    if not all(item["state"] == "completed" for item in items.values()):
        await persist("failed")
        return RunOutcome(
            phase="failed",
            terminal_reason=terminal_reason,
            view={"nodes": list(nodes.values()), "edges": list(edges.values())},
        )
    answer = await ctx.llm.text(
        f"Combine the swarm's results into one answer for: {ctx.objective}\n"
        + "\n".join(f"- {item['summary']}: {item['result_excerpt']}" for item in items.values()),
        step="synthesize",
        max_tokens=1_200,
    )
    await ctx.say("swarm", answer, step="swarm:final", message_type="decision")
    await persist("completed")
    return RunOutcome(
        phase="completed",
        terminal_reason=terminal_reason,
        safe_output=answer[:8_000],
        view={
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "work_items": list(items.values()),
        },
    )


__all__ = ["run_swarm"]
