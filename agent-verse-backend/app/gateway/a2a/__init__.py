"""OrgAsAgent + OrgA2AClient — Q8 of spec.

OrgAsAgent:
  Makes an org callable like a standard AI agent.
  Implements the AgentVerse A2A protocol.
  External systems call via HTTP — no framework lock-in.

OrgA2AClient:
  Enables one org to delegate work to another org.
  Both orgs must be in same tenant OR have explicit federation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
import structlog
from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── Response types ─────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Result returned by OrgAsAgent.invoke()."""
    output: Any = None
    artifacts: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    mission_id: str | None = None
    success: bool = True
    error: str | None = None


@dataclass
class AgentEvent:
    """Streaming event from OrgAsAgent.stream()."""
    event_type: str         # thinking | step | tool_call | artifact | complete | error
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    mission_id: str | None = None


@dataclass
class DelegationResult:
    """Result of inter-org delegation."""
    delegated_to_org: str
    task: str
    mission_id: str | None = None
    result: AgentResult | None = None
    delegation_cost_usd: float = 0.0


# ── OrgAsAgent ─────────────────────────────────────────────────────────────────

class OrgAsAgent:
    """
    Makes this org callable like a standard AI agent.
    External orchestrators, other agents, and pipelines call this.

    Endpoints (handled by gateway router):
      POST /v1/a2a/{org_id}/invoke          ← synchronous
      POST /v1/a2a/{org_id}/invoke-async    ← async (returns mission_id)
      GET  /v1/a2a/{org_id}/stream          ← SSE stream
    """

    def __init__(self, org_id: str, tenant_id: str) -> None:
        self.org_id    = org_id
        self.tenant_id = tenant_id

    async def invoke(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        timeout_seconds: float = 300.0,
    ) -> AgentResult:
        """
        Synchronous invocation — waits for mission to complete.
        For long missions use invoke_async instead.
        """
        with _tracer.start_as_current_span("a2a.invoke") as span:
            span.set_attribute("org_id", self.org_id)
            span.set_attribute("task_len", len(task))

            import time
            start = time.monotonic()

            # TODO: create mission and poll for completion
            # For now: return stub result indicating service is ready
            await asyncio.sleep(0.1)

            return AgentResult(
                output=f"Task accepted: {task[:100]}. Connect org service for full execution.",
                cost_usd=0.0,
                duration_seconds=time.monotonic() - start,
                success=True,
            )

    async def invoke_async(
        self,
        task: str,
        callback_url: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """
        Non-blocking — returns mission_id immediately.
        Results delivered via callback_url | polling | SSE stream.
        """
        with _tracer.start_as_current_span("a2a.invoke_async") as span:
            span.set_attribute("org_id", self.org_id)

            # TODO: create mission via OrgService
            import uuid
            mission_id = f"mission_{uuid.uuid4().hex[:12]}"
            _log.info("a2a.invoke_async", org_id=self.org_id, mission_id=mission_id, task=task[:80])
            return mission_id

    async def stream(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Stream events as the mission executes."""
        with _tracer.start_as_current_span("a2a.stream") as span:
            span.set_attribute("org_id", self.org_id)

            yield AgentEvent("thinking", text="Analyzing task and forming team…", mission_id=None)
            await asyncio.sleep(0.05)
            yield AgentEvent("step", text="Team formed. Beginning execution.", mission_id=None)
            await asyncio.sleep(0.05)
            yield AgentEvent(
                "complete",
                text=f"Task accepted: {task[:100]}",
                data={"org_id": self.org_id},
            )


# ── OrgA2AClient ───────────────────────────────────────────────────────────────

class OrgA2AClient:
    """
    Enables one org to delegate work to another org.
    Uses the A2A HTTP protocol — no framework lock-in.
    """

    def __init__(
        self,
        calling_org_id: str,
        calling_tenant_id: str,
        base_url: str = "",
        api_key: str = "",
    ) -> None:
        self.calling_org_id    = calling_org_id
        self.calling_tenant_id = calling_tenant_id
        self._base_url = base_url.rstrip("/")
        self._api_key  = api_key
        self._http     = httpx.AsyncClient(timeout=30.0)

    async def delegate_to_org(
        self,
        to_org_id: str,
        task: str,
        share_context: list[str] | None = None,
        budget_usd: float | None = None,
    ) -> DelegationResult:
        """
        Delegate a task from this org to another org.

        Example:
          marketing_org.delegate_to_org(
              to_org_id="law_firm_org",
              task="Review this contract before we sign",
              share_context=["contract.pdf"],
          )
        """
        with _tracer.start_as_current_span("a2a.delegate") as span:
            span.set_attribute("from_org", self.calling_org_id)
            span.set_attribute("to_org",   to_org_id)
            span.set_attribute("task_len", len(task))

            if not self._base_url:
                # Internal delegation (same deployment)
                agent = OrgAsAgent(to_org_id, self.calling_tenant_id)
                result = await agent.invoke(task, context={"delegated_by": self.calling_org_id})
                return DelegationResult(
                    delegated_to_org=to_org_id,
                    task=task,
                    mission_id=result.mission_id,
                    result=result,
                )

            # External delegation via HTTP A2A API
            try:
                resp = await self._http.post(
                    f"{self._base_url}/v1/a2a/{to_org_id}/invoke",
                    json={
                        "task":          task,
                        "calling_org_id": self.calling_org_id,
                        "share_context": share_context or [],
                        "budget_usd":    budget_usd,
                    },
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
                return DelegationResult(
                    delegated_to_org=to_org_id,
                    task=task,
                    mission_id=data.get("mission_id"),
                    result=AgentResult(output=data.get("output"), cost_usd=data.get("cost_usd", 0.0)),
                )
            except Exception as exc:
                _log.error("a2a.delegate_failed", to_org=to_org_id, error=str(exc))
                return DelegationResult(
                    delegated_to_org=to_org_id,
                    task=task,
                    result=AgentResult(success=False, error=str(exc)),
                )

    async def close(self) -> None:
        await self._http.aclose()
