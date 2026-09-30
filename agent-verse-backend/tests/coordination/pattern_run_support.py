"""Scripted provider + in-memory app state for coordination pattern-run tests."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from app.coordination.auction.repository import InMemoryAuctionRepository, InMemorySealedBidInbox
from app.coordination.camel.repository import InMemoryCamelRepository
from app.coordination.contracts import AuthorizationContext
from app.coordination.generative.repository import InMemoryGenerativeRepository
from app.coordination.ledger.repository import InMemoryProgressLedgerRepository
from app.coordination.live_bus import CoordinationLiveBus
from app.coordination.magentic.human_review import MagenticHumanReviewService
from app.coordination.magentic.repository import InMemoryMagenticRunRepository
from app.coordination.moa.repository import InMemoryMoARepository, InMemoryMoARunRepository
from app.coordination.pattern_runs.service import PatternRunService
from app.coordination.service import CoordinationService, SessionAdmission
from app.coordination.store import InMemoryCoordinationStore
from app.coordination.swarm.repository import InMemorySwarmRepository
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="k", roles=("operator",)
)


class ScriptedProvider:
    """Answers each pattern prompt deterministically (no real LLM)."""

    default_model = "scripted-model"

    def __init__(self, *, magentic_completes: bool = True) -> None:
        self.prompts: list[str] = []
        self.magentic_completes = magentic_completes

    async def complete(self, request: Any) -> CompletionResponse:
        prompt = request.messages[-1].content
        self.prompts.append(prompt)
        return CompletionResponse(
            content=self._answer(prompt), model="scripted-model", input_tokens=5, output_tokens=5
        )

    def _answer(self, p: str) -> str:
        if "Build a task ledger" in p:
            return json.dumps(
                {
                    "objective": "Write the report",
                    "open_work": ["research", "draft"],
                    "satisfaction_criteria": ["report written"],
                }
            )
        if "Your work item:" in p:
            item = re.search(r"Your work item: (.*)", p)
            done = item.group(1) if item else "?"
            return json.dumps(
                {
                    "result": f"done {done}" if self.magentic_completes else "",
                    "completed": self.magentic_completes,
                    "verified_facts": [f"fact about {done}"] if self.magentic_completes else [],
                    "blockers": [] if self.magentic_completes else ["missing data"],
                }
            )
        if "Which criteria are satisfied" in p:
            return json.dumps({"satisfied": ["report written"], "missing_work": []})
        if "stalled" in p:
            return json.dumps({"open_work": ["research"], "contradicted_facts": []})
        if "Write the final answer" in p:
            return "FINAL REPORT"
        if "Mixture-of-Agents layer" in p:
            return "an independent proposal"
        if "You are the aggregator" in p:
            return "AGGREGATED ANSWER"
        if "Role-play (CAMEL)" in p:
            solver = "You are 'ai_assistant'" in p
            turns = p.count("ai_user:") + p.count("ai_assistant:")
            finished = solver and turns >= 1
            return json.dumps(
                {
                    "content": "solution step" if solver else "instruction step",
                    "completed": finished,
                    "agreement": finished,
                    "safe_output": "CAMEL SOLUTION" if finished else "",
                }
            )
        if "generative agent" in p:
            return json.dumps(
                {
                    "observation": "observed the market",
                    "importance": 6000,
                    "action": "wrote notes",
                    "completed": "hour 2." in p,
                    "safe_output": "GENERATIVE SUMMARY",
                }
            )
        if "Reflect:" in p:
            return "Markets reward preparation."
        if "Split this objective" in p:
            return json.dumps({"work_items": ["collect data", "analyse data"]})
        if "You are swarm agent" in p:
            return "swarm work result"
        if "Combine the swarm" in p:
            return "SWARM ANSWER"
        if "bidding to perform" in p:
            strong = "'bidder-2'" in p
            return json.dumps(
                {
                    "approach": "careful plan" if strong else "quick plan",
                    "quality": 9000 if strong else 4000,
                    "cost_usd": 0.2,
                    "latency_ms": 1000,
                    "confidence": 8000 if strong else 5000,
                }
            )
        if "winner of the auction" in p:
            return "DELIVERED WORK"
        return "ok"


def pattern_state(provider: Any | None = None) -> SimpleNamespace:
    store = InMemoryCoordinationStore()
    return SimpleNamespace(
        llm_provider=provider,
        coordination_service=CoordinationService(store),
        transcript_service=TranscriptService(InMemoryTranscriptRepository()),
        coordination_live_bus=CoordinationLiveBus(),
        progress_ledger_repository=InMemoryProgressLedgerRepository(),
        magentic_run_repository=InMemoryMagenticRunRepository(),
        magentic_human_review=MagenticHumanReviewService(),
        moa_repository=InMemoryMoARepository(),
        moa_run_repository=InMemoryMoARunRepository(),
        camel_repository=InMemoryCamelRepository(),
        generative_repository=InMemoryGenerativeRepository(),
        swarm_repository=InMemorySwarmRepository(),
        auction_repository=InMemoryAuctionRepository(),
        auction_bid_inbox=InMemorySealedBidInbox(),
    )


async def active_session(state: SimpleNamespace, *, start: bool = True) -> str:
    created = await state.coordination_service.create_session(
        TENANT,
        SessionAdmission(
            civilization_id="civ",
            goal_id="goal",
            policy_snapshot={},
            budget_snapshot={},
            authorization=AuthorizationContext(
                actor_id="k", permissions=frozenset({"coordination:create"})
            ),
        ),
    )
    if start:
        await state.coordination_service.start_session(
            TENANT, created.session_id, expected_version=1, idempotency_key="start"
        )
    return str(created.session_id)


def service(state: SimpleNamespace) -> PatternRunService:
    return PatternRunService(state)
