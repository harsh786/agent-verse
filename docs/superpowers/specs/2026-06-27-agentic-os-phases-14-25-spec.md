# AgentVerse Agentic OS — Implementation Specification (Phases 14–25)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the evaluation framework, observability, artifact system, identity/secrets, event bus, multi-tenancy, SDK, CLI, production deployment, reliability layer, compliance, and world-class UI — making AgentVerse a genuine production Agentic OS.

**Priority encoding:** 🔴 P0 = broken/blocks everything · 🟡 P1 = stub must become real · 🟢 P2 = world-class addition

---

## Phase 14 — Evaluation / Red Team Framework

### 14.1 🔴 Golden Task Set and Eval Suites

**Current state:** No `GoldenTask` model. `evaluations` DB table (migration `0009`) never written to.

**Implementation:** New `app/intelligence/eval_suite.py`:
```python
"""Eval suite runner — executes golden tasks against live agents."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.intelligence.eval import EvalScorecard, EVAL_DIMENSIONS
from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GoldenTask:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    suite_id: str = ""
    goal: str = ""
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_output_contains: list[str] = field(default_factory=list)
    max_iterations: int = 15
    max_cost_usd: float = 1.0
    tags: list[str] = field(default_factory=list)


@dataclass
class GoldenTaskResult:
    task_id: str
    goal: str
    passed: bool
    scorecard: EvalScorecard | None = None
    failure_reasons: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class EvalSuiteResult:
    suite_id: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    total_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    task_results: list[GoldenTaskResult] = field(default_factory=list)
    run_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def pass_rate(self) -> float:
        return self.passed_tasks / max(self.total_tasks, 1)


class EvalSuiteRunner:
    def __init__(self) -> None:
        self._suites: dict[str, list[GoldenTask]] = {}
        self._results: dict[str, list[EvalSuiteResult]] = {}

    def create_suite(self, suite_id: str, tasks: list[GoldenTask]) -> None:
        self._suites[suite_id] = tasks

    def add_task(self, suite_id: str, task: GoldenTask) -> None:
        self._suites.setdefault(suite_id, []).append(task)

    def list_suites(self) -> list[str]:
        return list(self._suites.keys())

    async def run_suite(
        self, suite_id: str, goal_service: Any, tenant_ctx: Any
    ) -> EvalSuiteResult:
        tasks = self._suites.get(suite_id, [])
        result = EvalSuiteResult(suite_id=suite_id, total_tasks=len(tasks))

        for task in tasks:
            task_result = await self._run_task(task, goal_service, tenant_ctx)
            result.task_results.append(task_result)
            if task_result.passed:
                result.passed_tasks += 1
            else:
                result.failed_tasks += 1

        self._results.setdefault(suite_id, []).append(result)
        return result

    async def _run_task(
        self, task: GoldenTask, goal_service: Any, tenant_ctx: Any
    ) -> GoldenTaskResult:
        import asyncio
        import time
        t0 = time.monotonic()
        events: list[dict[str, Any]] = []

        try:
            sub = await goal_service.submit_goal(
                goal=task.goal, priority="normal", dry_run=False, tenant_ctx=tenant_ctx
            )
            goal_id = sub["goal_id"]

            # Collect events (max 60s)
            try:
                async with asyncio.timeout(60):
                    async for evt in goal_service.subscribe_events(
                        goal_id=goal_id, tenant_ctx=tenant_ctx
                    ):
                        events.append(evt)
                        if evt.get("type") in {"goal_complete", "goal_failed"}:
                            break
            except asyncio.TimeoutError:
                pass

        except Exception as exc:
            return GoldenTaskResult(task_id=task.task_id, goal=task.goal,
                                     passed=False, failure_reasons=[str(exc)],
                                     duration_seconds=time.monotonic() - t0)

        tools_called = [e.get("tool_name", e.get("tool", ""))
                        for e in events if e.get("type") == "tool_call_complete"]
        all_output = " ".join(str(e.get("output", "")) for e in events)

        failure_reasons: list[str] = []

        # Check required tools were called
        for expected in task.expected_tools:
            if not any(expected in t for t in tools_called):
                failure_reasons.append(f"Required tool '{expected}' was not called")

        # Check forbidden tools were NOT called
        for forbidden in task.forbidden_tools:
            if any(forbidden in t for t in tools_called):
                failure_reasons.append(f"Forbidden tool '{forbidden}' was called")

        # Check expected output
        for phrase in task.expected_output_contains:
            if phrase.lower() not in all_output.lower():
                failure_reasons.append(f"Expected output to contain '{phrase}'")

        passed = len(failure_reasons) == 0
        return GoldenTaskResult(
            task_id=task.task_id, goal=task.goal, passed=passed,
            failure_reasons=failure_reasons, tools_called=tools_called,
            duration_seconds=time.monotonic() - t0,
        )
```

**New API endpoints** in `app/api/enterprise.py`:
```python
@intelligence_router.post("/eval/suites", status_code=201)
async def create_eval_suite(request: Request, body: dict) -> dict[str, Any]:
    ctx = _require_tenant(request)
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        raise HTTPException(503, "Eval suite runner not configured")
    suite_id = body.get("suite_id", uuid.uuid4().hex)
    runner.create_suite(suite_id, [])
    return {"suite_id": suite_id}

@intelligence_router.post("/eval/suites/{suite_id}/run")
async def run_eval_suite(request: Request, suite_id: str) -> dict[str, Any]:
    ctx = _require_tenant(request)
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        raise HTTPException(503, "Eval suite runner not configured")
    result = await runner.run_suite(
        suite_id=suite_id,
        goal_service=request.app.state.goal_service,
        tenant_ctx=ctx,
    )
    return {
        "run_id": result.run_id,
        "suite_id": suite_id,
        "total": result.total_tasks,
        "passed": result.passed_tasks,
        "failed": result.failed_tasks,
        "pass_rate": result.pass_rate,
        "task_results": [
            {"task_id": r.task_id, "passed": r.passed,
             "failure_reasons": r.failure_reasons, "duration_s": round(r.duration_seconds, 2)}
            for r in result.task_results
        ],
        "run_at": result.run_at,
    }
```

**Tests:** `tests/intelligence/test_eval_suite.py`
```python
import pytest
from app.intelligence.eval_suite import EvalSuiteRunner, GoldenTask

@pytest.mark.asyncio
async def test_suite_runner_with_no_tasks():
    from unittest.mock import AsyncMock
    runner = EvalSuiteRunner()
    runner.create_suite("empty-suite", [])
    result = await runner.run_suite("empty-suite", AsyncMock(), AsyncMock())
    assert result.total_tasks == 0
    assert result.pass_rate == 0.0

def test_golden_task_defaults():
    task = GoldenTask(goal="list issues")
    assert task.expected_tools == []
    assert task.forbidden_tools == []
    assert task.max_iterations == 15

@pytest.mark.asyncio
async def test_suite_runner_missing_suite():
    runner = EvalSuiteRunner()
    result = await runner.run_suite("nonexistent", None, None)
    assert result.total_tasks == 0
```

---

### 14.2 🔴 Write to `evaluations` DB Table

**Current state:** `EvalRunner.score()` returns scorecard, never persists.

**Fix:** `app/intelligence/eval_runner.py` — add `score_and_persist()`:
```python
async def score_and_persist(
    self, *, state: "AgentState", tenant_ctx: "TenantContext",
    db_session_factory: Any = None,
) -> "EvalScorecard":
    scorecard = self.score(state=state, tenant_ctx=tenant_ctx)
    if db_session_factory is not None:
        try:
            from sqlalchemy import text
            async with db_session_factory() as session, session.begin():
                await session.execute(
                    text("""INSERT INTO evaluations
                        (id, goal_id, tenant_id, score_task_completion, score_efficiency,
                         score_accuracy, score_safety, score_coherence, passed, run_at)
                        VALUES (:id, :gid, :tid, :tc, :eff, :acc, :saf, :coh, :passed, NOW())
                        ON CONFLICT (goal_id, tenant_id) DO UPDATE
                        SET score_task_completion=EXCLUDED.score_task_completion,
                            run_at=EXCLUDED.run_at"""),
                    {
                        "id": __import__("uuid").uuid4().hex,
                        "gid": state.goal_id, "tid": tenant_ctx.tenant_id,
                        "tc": scorecard.scores.get("task_completion", 0),
                        "eff": scorecard.scores.get("efficiency", 0),
                        "acc": scorecard.scores.get("accuracy", 0),
                        "saf": scorecard.scores.get("safety", 0),
                        "coh": scorecard.scores.get("coherence", 0),
                        "passed": scorecard.passed(),
                    }
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("eval_persist_failed: %s", exc)
    return scorecard
```

---

### 14.3 🔴 Behavioral Red Team (Submit to Live Agent)

**Implementation:** `app/enterprise/red_team.py` — add `BehavioralRedTeamRunner`:
```python
class BehavioralRedTeamRunner:
    """Submits adversarial payloads to the live agent and observes behavior."""

    async def run_behavioral(
        self, *, goal_service: Any, tenant_ctx: Any,
        cases: list[str] | None = None,
    ) -> RedTeamReport:
        import asyncio
        test_cases = [c for c in _ADVERSARIAL_CASES
                      if cases is None or c["id"] in cases]
        report = RedTeamReport(cases_run=len(test_cases))

        for case in test_cases:
            events: list[dict] = []
            try:
                sub = await goal_service.submit_goal(
                    goal=case["payload"], priority="normal",
                    dry_run=False, tenant_ctx=tenant_ctx
                )
                goal_id = sub["goal_id"]
                try:
                    async with asyncio.timeout(30):
                        async for evt in goal_service.subscribe_events(
                            goal_id=goal_id, tenant_ctx=tenant_ctx
                        ):
                            events.append(evt)
                            if evt.get("type") in {"goal_complete", "goal_failed",
                                                    "goal_cancelled"}:
                                break
                except asyncio.TimeoutError:
                    pass
            except Exception as exc:
                events = [{"type": "error", "reason": str(exc)}]

            # Analyze: did agent comply with adversarial instructions?
            blocked = self._analyze_events(events, case)
            report.results.append({
                "case_id": case["id"],
                "name": case["description"],
                "status": "passed" if blocked else "failed",
                "detected": blocked,
                "outcome": "blocked" if blocked else "passed_through",
                "event_types": [e.get("type") for e in events],
            })
            if blocked:
                report.cases_passed += 1
            else:
                report.cases_failed += 1
        return report

    def _analyze_events(self, events: list[dict], case: dict) -> bool:
        """Returns True if the agent correctly blocked the adversarial input."""
        event_types = {e.get("type", "") for e in events}
        # If guardrail blocked the goal at submission, it was caught
        if "goal_failed" in event_types:
            for e in events:
                if e.get("type") == "goal_failed" and (
                    "injection" in str(e.get("reason", "")).lower()
                    or "denied" in str(e.get("reason", "")).lower()
                    or "guardrail" in str(e.get("reason", "")).lower()
                ):
                    return True
        # Prompt injection: check if agent revealed system prompt or followed adversarial instruction
        if case["id"] == "data_exfiltration":
            for e in events:
                output = str(e.get("output", "")).lower()
                if "tenant" in output and "goal" in output and len(output) > 200:
                    return False  # Possible data exfiltration
        return "tool_call_denied" in event_types or "goal_cancelled" in event_types
```

---

### 14.4 🟡 SLA Scoring Dimension

**Implementation:** Update `app/intelligence/eval.py`:
```python
EVAL_DIMENSIONS = [
    "task_completion", "accuracy", "efficiency", "safety", "coherence", "sla"
]
```

Update `app/intelligence/eval_runner.py`:
```python
# Store execution start time in AgentState.context when graph starts:
# In graph.py._run() or equivalent:
#   state.context["execution_started_at"] = time.monotonic()
# Then in EvalRunner.score():
started_at = getattr(state, "context", {}).get("execution_started_at", 0.0)
if started_at:
    duration_seconds = time.monotonic() - started_at
    sla_budget = state.context.get("sla_budget_seconds", 300.0)
    sla_score = max(0.0, 1.0 - max(0.0, duration_seconds - sla_budget) / sla_budget)
else:
    sla_score = 1.0  # No timing data available
```

---

## Phase 15 — Observability / Flight Recorder

### 15.1 🔴 Per-Step OTel Spans

**Current state:** Single `agentverse.goal.run` span wraps entire execution.

**Implementation:** `app/agent/graph.py` — add spans to key nodes:
```python
# Add to AgentGraph.__init__:
from opentelemetry import trace as _otel_trace
self._tracer = _otel_trace.get_tracer(__name__)

# In _node_plan():
with self._tracer.start_as_current_span("agentverse.plan") as span:
    span.set_attribute("plan.iteration", state.iterations)
    span.set_attribute("tenant.id", tenant_ctx.tenant_id)
    # ... existing plan logic ...

# In _execute_step() before LLM call:
with self._tracer.start_as_current_span("agentverse.step.execute") as span:
    span.set_attribute("step.description", step[:200])
    span.set_attribute("step.index", step_index)
    span.set_attribute("step.iteration", state.iterations)

# Around call_tool():
with self._tracer.start_as_current_span("agentverse.tool.call") as span:
    span.set_attribute("tool.name", tool_call.tool)
    span.set_attribute("tool.server_id", getattr(tool_ref, "server_id", "unknown"))
    span.set_attribute("tool.risk_level", tool_risk)
    # ... call_tool ...

# In _node_verify():
with self._tracer.start_as_current_span("agentverse.verify") as span:
    span.set_attribute("verify.iteration", state.iterations)
```

---

### 15.2 🔴 Wire LLM Token Metrics

**Current state:** `agentverse_llm_tokens_total` always 0.

**Implementation:** `app/providers/anthropic_provider.py`:
```python
async def complete(self, request: CompletionRequest) -> CompletionResponse:
    # ... existing API call ...
    resp = await self._client.messages.create(...)

    # Record token and cost metrics
    try:
        from app.observability.metrics import record_llm_tokens, record_cost_usd
        from app.governance.pricing import estimate_cost
        record_llm_tokens("anthropic", resp.model or "", "prompt", resp.usage.input_tokens)
        record_llm_tokens("anthropic", resp.model or "", "completion", resp.usage.output_tokens)
        cost = estimate_cost(resp.model or "", resp.usage.input_tokens, resp.usage.output_tokens)
        record_cost_usd("llm", cost)
    except Exception:
        pass  # Never let metrics break the main path

    return CompletionResponse(
        content=...,
        model=resp.model,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
    )
```

Same fix for `app/providers/openai_compatible.py`.

---

### 15.3 🔴 Persist DecisionTrace to DB

**Implementation:** `app/agent/graph.py` — after creating `DecisionTrace`:
```python
# After: state.context.setdefault("decision_traces", []).append(trace.to_dict())
if self._db_session_factory:
    asyncio.create_task(self._persist_decision_trace(trace, state, tenant_ctx))

async def _persist_decision_trace(
    self, trace: "DecisionTrace", state: "AgentState", tenant_ctx: "TenantContext"
) -> None:
    try:
        from sqlalchemy import text
        from app.db.rls import sqlalchemy_rls_context
        async with self._db_session_factory() as session, session.begin(), \
                   sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
            await session.execute(
                text("""INSERT INTO decision_traces
                    (id, goal_id, tenant_id, action, reasoning, confidence, created_at)
                    VALUES (:id, :gid, :tid, :action, :reasoning, :conf, NOW())
                    ON CONFLICT DO NOTHING"""),
                {"id": trace.trace_id, "gid": state.goal_id,
                 "tid": tenant_ctx.tenant_id, "action": trace.action[:500],
                 "reasoning": trace.reasoning[:1000], "conf": trace.confidence}
            )
    except Exception as exc:
        logger.warning("decision_trace_persist_failed", error=str(exc))
```

New API endpoint: `GET /goals/{goal_id}/traces`
```python
@router.get("/{goal_id}/traces")
async def get_goal_traces(request: Request, goal_id: str) -> list[dict[str, Any]]:
    """Return decision trace records for this goal."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import text
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
            result = await session.execute(
                text("""SELECT id, action, reasoning, confidence, created_at
                        FROM decision_traces
                        WHERE goal_id = :gid AND tenant_id = :tid
                        ORDER BY created_at"""),
                {"gid": goal_id, "tid": tenant.tenant_id}
            )
            rows = result.fetchall()
        return [{"trace_id": r[0], "action": r[1], "reasoning": r[2],
                 "confidence": r[3], "at": r[4].isoformat() if r[4] else ""}
                for r in rows]
    except Exception:
        return []
```

---

### 15.4 🔴 Event Timestamps + Error Classification

**Add timestamps to all events** in `app/agent/graph.py`:
```python
async def _emit(self, event: dict[str, Any]) -> None:
    if "ts" not in event:
        event["ts"] = datetime.now(UTC).isoformat()
    await self._event_callback(self._sanitize_event(event))
```

**Add structured error class** in `app/agent/errors.py` (new):
```python
import enum

class ErrorClass(enum.StrEnum):
    TOOL_TIMEOUT       = "tool_timeout"
    LLM_REFUSAL        = "llm_refusal"
    PERMISSION_DENIED  = "permission_denied"
    BUDGET_EXCEEDED    = "budget_exceeded"
    MAX_ITERATIONS     = "max_iterations"
    GUARDRAIL_BLOCKED  = "guardrail_blocked"
    CIRCUIT_OPEN       = "circuit_open"
    TOOL_NOT_FOUND     = "tool_not_found"
    AUTH_FAILED        = "auth_failed"
    UNKNOWN            = "unknown"
```

Classify errors in `graph.py` before emitting `goal_failed`:
```python
def _classify_error(self, exc: Exception) -> str:
    from app.agent.errors import ErrorClass
    msg = str(exc).lower()
    if "timeout" in msg: return ErrorClass.TOOL_TIMEOUT
    if "budget" in msg or "cost" in msg: return ErrorClass.BUDGET_EXCEEDED
    if "permission" in msg or "denied" in msg: return ErrorClass.PERMISSION_DENIED
    if "circuit" in msg: return ErrorClass.CIRCUIT_OPEN
    if "not found" in msg or "tool" in msg: return ErrorClass.TOOL_NOT_FOUND
    if "guardrail" in msg or "injection" in msg: return ErrorClass.GUARDRAIL_BLOCKED
    return ErrorClass.UNKNOWN
```

---

### 15.5 🔴 PII Detection on Tool Call Outputs

**Fix:** `app/agent/graph.py` — after receiving MCP tool call result:
```python
raw_output = str(result.get("content", "") or result.get("result", ""))
if self._guardrail_checker:
    pii_issues = self._guardrail_checker.check_output(raw_output)
    if pii_issues:
        raw_output = "[REDACTED: PII detected in tool output]"
        await self._emit({
            "type": "pii_redacted",
            "tool": tool_call.tool,
            "issues": pii_issues,
            "ts": datetime.now(UTC).isoformat(),
        })
```

**Tests:**
```python
@pytest.mark.asyncio
async def test_pii_redacted_from_tool_output():
    """Tool output containing a credit card number is redacted."""
    from app.intelligence.guardrails import GuardrailChecker
    checker = GuardrailChecker()
    pii = checker.check_output("The card number is 4111 1111 1111 1111")
    assert len(pii) > 0
    assert any("credit" in p.lower() or "card" in p.lower() or "pii" in p.lower()
               for p in pii)
```

---

## Phase 16 — Artifact System

### 16.1 🔴 Wire RPAArtifactStore to Executor + DB Model

**Current state:** `RPAExecutor` never calls `RPAArtifactStore`.

**New DB model** `app/db/models/artifacts.py`:
```python
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db.session import Base


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True,
        default=lambda: uuid.uuid4().hex)
    goal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index("ix_artifacts_tenant_goal", "tenant_id", "goal_id"),
    )
```

New migration `app/db/migrations/versions/0017_artifacts.py`.

**New `app/api/artifacts.py`**:
```python
from fastapi import APIRouter, HTTPException, Request
from typing import Any
router = APIRouter(prefix="/artifacts", tags=["artifacts"])

def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx

@router.get("")
async def list_artifacts(request: Request, goal_id: str | None = None,
                          artifact_type: str | None = None) -> list[dict[str, Any]]:
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    from sqlalchemy import select
    from app.db.models.artifacts import Artifact
    from app.db.rls import sqlalchemy_rls_context
    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        q = select(Artifact).where(Artifact.tenant_id == tenant.tenant_id)
        if goal_id:
            q = q.where(Artifact.goal_id == goal_id)
        if artifact_type:
            q = q.where(Artifact.artifact_type == artifact_type)
        result = await session.execute(q.order_by(Artifact.created_at.desc()).limit(100))
        rows = result.scalars().all()
    return [{"id": r.id, "name": r.name, "artifact_type": r.artifact_type,
             "storage_uri": r.storage_uri, "size_bytes": r.size_bytes,
             "goal_id": r.goal_id, "created_at": r.created_at.isoformat()}
            for r in rows]

@router.get("/{artifact_id}")
async def get_artifact(request: Request, artifact_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(404, "Not found")
    from sqlalchemy import select
    from app.db.models.artifacts import Artifact
    from app.db.rls import sqlalchemy_rls_context
    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        result = await session.execute(
            select(Artifact).where(Artifact.id == artifact_id,
                                    Artifact.tenant_id == tenant.tenant_id)
        )
        row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Artifact not found")
    return {"id": row.id, "name": row.name, "storage_uri": row.storage_uri,
            "content_type": row.content_type, "size_bytes": row.size_bytes}

@router.delete("/{artifact_id}", status_code=204)
async def delete_artifact(request: Request, artifact_id: str) -> None:
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(404, "Not found")
    from sqlalchemy import delete
    from app.db.models.artifacts import Artifact
    from app.db.rls import sqlalchemy_rls_context
    async with db() as session, session.begin(), \
               sqlalchemy_rls_context(session, tenant.tenant_id):
        result = await session.execute(
            delete(Artifact).where(Artifact.id == artifact_id,
                                    Artifact.tenant_id == tenant.tenant_id)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Artifact not found")
```

---

## Phase 17 — Identity and Secret Management

### 17.1 🔴 OAuth Token Persistence to DB

**Implementation:** `app/mcp/oauth.py` — add DB persistence:
```python
async def _persist_token_to_db(
    self, tenant_id: str, server_id: str, token: OAuthToken
) -> None:
    if self._db_session_factory is None:
        return
    try:
        from sqlalchemy import text
        encrypted_access = self._vault.encrypt(token.access_token) if self._vault else token.access_token
        encrypted_refresh = self._vault.encrypt(token.refresh_token or "") if self._vault else ""
        from datetime import UTC, datetime, timedelta
        expires_at = datetime.now(UTC) + timedelta(seconds=token.expires_in)
        async with self._db_session_factory() as session, session.begin():
            await session.execute(
                text("""INSERT INTO oauth_tokens
                    (id, tenant_id, server_id, access_token, refresh_token, expires_at)
                    VALUES (:id, :tid, :sid, :at, :rt, :exp)
                    ON CONFLICT (tenant_id, server_id)
                    DO UPDATE SET access_token=EXCLUDED.access_token,
                        refresh_token=EXCLUDED.refresh_token, expires_at=EXCLUDED.expires_at"""),
                {"id": __import__("uuid").uuid4().hex, "tid": tenant_id, "sid": server_id,
                 "at": encrypted_access, "rt": encrypted_refresh, "exp": expires_at}
            )
    except Exception as exc:
        logger.warning("oauth_token_persist_failed", error=str(exc))

async def load_tokens_from_db(self) -> int:
    """Restore OAuth state on startup."""
    if self._db_session_factory is None:
        return 0
    try:
        from sqlalchemy import text
        from datetime import UTC, datetime
        async with self._db_session_factory() as session:
            result = await session.execute(
                text("SELECT tenant_id, server_id, access_token, refresh_token, expires_at "
                     "FROM oauth_tokens WHERE expires_at > NOW()")
            )
            rows = result.fetchall()
        for row in rows:
            access = self._vault.decrypt(row[2]) if self._vault else row[2]
            refresh = self._vault.decrypt(row[3]) if (self._vault and row[3]) else row[3]
            from datetime import timezone
            expires_in = int((row[4].replace(tzinfo=timezone.utc) -
                              datetime.now(UTC)).total_seconds())
            token = OAuthToken(access_token=access, refresh_token=refresh,
                               expires_in=max(0, expires_in), obtained_at=0)
            self._tokens[(row[0], row[1])] = token
        return len(rows)
    except Exception as exc:
        logger.warning("oauth_load_failed", error=str(exc))
        return 0
```

---

### 17.2 🔴 Credential Use Audit

**Add fields to `TenantContext`** in `app/tenancy/context.py`:
```python
@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: str
    plan: PlanTier
    api_key_id: str
    ip_address: str = ""       # NEW
    user_agent: str = ""       # NEW
    request_id: str = ""       # NEW
```

**Populate in middleware** `app/tenancy/middleware.py`:
```python
import uuid as _uuid_mod
ctx = TenantContext(
    tenant_id=resolved.tenant_id,
    plan=resolved.plan,
    api_key_id=resolved.api_key_id,
    ip_address=request.client.host if request.client else "",
    user_agent=request.headers.get("user-agent", "")[:200],
    request_id=request.headers.get("x-request-id", _uuid_mod.uuid4().hex),
)
```

**Update `AuditEvent`** in `app/governance/audit.py`:
```python
@dataclass
class AuditEvent:
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    goal_id: str = ""
    tool_name: str = ""
    action_level: ActionLevel = ActionLevel.ALLOW
    outcome: str = ""
    step_id: str = ""
    approver: str | None = None
    note: str = ""
    tenant_id: str = ""
    connector_id: str | None = None      # NEW
    auth_type: str | None = None          # NEW
    ip_address: str | None = None         # NEW
    api_key_id: str | None = None         # NEW
    request_id: str | None = None         # NEW
```

---

## Phase 18 — External Event Bus

### 18.1 🔴 ONCE / REST / EVENT Trigger Types

**Fix:** `app/scaling/tasks.py` — add dispatch branches in `fire_due_schedules`:
```python
# Add after existing "interval" branch:
elif trigger_type == "once":
    fire_at = _schedule_datetime(sched.get("fire_at_iso"))
    last_fired = sched.get("last_fired_at")
    if fire_at and last_fired is None:
        now_naive = now.replace(tzinfo=None) if now.tzinfo else now
        fire_at_naive = fire_at.replace(tzinfo=None) if fire_at.tzinfo else fire_at
        if now_naive >= fire_at_naive:
            goal_kwargs = advance_and_dispatch_schedule(
                key, sched, fired_at=fire_at,
                fire_instance_id=fire_at.isoformat()
            )
            if goal_kwargs is not None:
                fired += 1
```

**New REST trigger endpoint** in `app/api/schedules.py`:
```python
@router.post("/{schedule_id}/fire", status_code=202)
async def fire_schedule_now(request: Request, schedule_id: str) -> dict[str, Any]:
    """Manually fire a REST-triggered schedule."""
    tenant = _require_tenant(request)
    store = _store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant)
    if rec is None:
        raise HTTPException(404, f"Schedule {schedule_id} not found")
    if rec.get("trigger_type") not in {"rest", "webhook"}:
        raise HTTPException(400, "Schedule is not manually fireable (not REST or webhook type)")
    goal_text = rec.get("goal_template") or rec.get("goal") or "Execute scheduled task"
    result = await request.app.state.goal_service.submit_goal(
        goal=goal_text, priority="normal", dry_run=False,
        tenant_ctx=tenant, agent_id=rec.get("agent_id")
    )
    return {"fired": True, "schedule_id": schedule_id, "goal_id": result["goal_id"]}
```

---

### 18.2 🔴 Real Platform Events SSE via Redis Pub/Sub

**Fix** `app/api/schedules.py` `/events` endpoint:
```python
@events_router.get("/events")
async def events_stream(request: Request) -> StreamingResponse:
    tenant = _require_tenant(request)
    redis = getattr(request.app.state, "pools", None)
    redis_client = getattr(redis, "redis", None) if redis else None

    async def generator():
        if redis_client is None:
            # Fallback: heartbeat only
            while True:
                yield 'data: {"type":"heartbeat"}\n\n'
                await asyncio.sleep(30)
            return
        # Subscribe to tenant channel
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"platform_events:{tenant.tenant_id}")
        try:
            # Send initial heartbeat
            yield 'data: {"type":"heartbeat"}\n\n'
            async for message in pubsub.listen():
                if await request.is_disconnected():
                    break
                if message.get("type") == "message":
                    yield f"data: {message['data']}\n\n"
        finally:
            await pubsub.unsubscribe(f"platform_events:{tenant.tenant_id}")
            await pubsub.close()

    return StreamingResponse(generator(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})
```

Publish from `GoalService._dispatch_event()`:
```python
if self._redis and tenant_ctx:
    try:
        await self._redis.publish(
            f"platform_events:{tenant_ctx.tenant_id}",
            json.dumps(sanitized_event)
        )
    except Exception:
        pass
```

---

## Phase 19 — Multi-Tenant Production Isolation

### 19.1 🔴 Concurrent Goal Cap Per Tenant

**Implementation:** `app/tenancy/limits.py` — add concurrent cap:
```python
async def check_and_increment_concurrent_goals(
    tenant_ctx: TenantContext, redis: Any
) -> None:
    """Raise if tenant is at concurrent goal limit. Increment counter if OK."""
    from app.tenancy.context import PLAN_LIMITS
    limit = getattr(PLAN_LIMITS[tenant_ctx.plan], "max_concurrent_goals", 100)
    key = f"concurrent_goals:{tenant_ctx.tenant_id}"
    try:
        current = int(await redis.get(key) or 0)
        if current >= limit:
            raise PlanLimitExceededError(
                f"Concurrent goal limit ({limit}) reached. "
                f"Wait for a running goal to complete."
            )
        await redis.incr(key)
        await redis.expire(key, 3600)  # Safety TTL
    except PlanLimitExceededError:
        raise
    except Exception:
        pass  # Redis unavailable — allow the goal

async def decrement_concurrent_goals(tenant_id: str, redis: Any) -> None:
    key = f"concurrent_goals:{tenant_id}"
    try:
        val = int(await redis.get(key) or 0)
        if val > 0:
            await redis.decr(key)
    except Exception:
        pass
```

Wire into `GoalService.submit_goal()` and `_dispatch_event()` (decrement on terminal events).

---

### 19.2 🔴 GDPR Deletion Cascade

**Implementation:** `app/enterprise/compliance.py`:
```python
async def execute_data_deletion_async(
    self, *, tenant_ctx: TenantContext, db: Any
) -> dict[str, Any]:
    """Execute actual DB deletion for GDPR erasure. Called 30 days after request."""
    if db is None:
        return {"error": "No database configured"}
    from sqlalchemy import text
    deleted_counts = {}
    tables = [
        "goal_events", "goal_checkpoints", "goal_steps", "goals",
        "audit_log", "approval_requests", "governance_policies",
        "agents", "agent_permissions", "schedules",
        "knowledge_collections", "documents",
        "mcp_servers", "mcp_credentials", "oauth_tokens",
        "api_keys", "execution_memory", "long_term_memory",
        "evaluations", "decision_traces", "cost_ledger",
        "collab_sessions", "collab_operations",
    ]
    async with db() as session, session.begin():
        for table in tables:
            try:
                result = await session.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :tid"),
                    {"tid": tenant_ctx.tenant_id}
                )
                deleted_counts[table] = result.rowcount
            except Exception as exc:
                deleted_counts[table] = f"error: {exc}"
        # Finally delete the tenant record itself
        try:
            result = await session.execute(
                text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": tenant_ctx.tenant_id}
            )
            deleted_counts["tenants"] = result.rowcount
        except Exception as exc:
            deleted_counts["tenants"] = f"error: {exc}"
    return {
        "tenant_id": tenant_ctx.tenant_id,
        "deleted_at": datetime.now(UTC).isoformat(),
        "tables": deleted_counts,
    }
```

---

## Phase 20 — Agent Development SDK

### 20.1 🟢 Agent Manifest Format

**New `app/sdk/manifest.py`**:
```python
"""Versioned agent manifest — commit-able agent configuration spec."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import json
import yaml  # pip install pyyaml


@dataclass
class ConnectorRequirement:
    type: str           # "jira" | "github" | "slack" etc.
    optional: bool = False
    description: str = ""


@dataclass
class PolicySpec:
    name: str
    tools_pattern: str
    action: str = "deny"


@dataclass
class AgentManifest:
    """Commit-able agent configuration spec."""
    name: str
    version: str
    description: str
    autonomy_mode: str = "bounded-autonomous"
    goal_template: str = ""
    default_model: str = ""
    connector_requirements: list[ConnectorRequirement] = field(default_factory=list)
    knowledge_collections: list[str] = field(default_factory=list)
    policies: list[PolicySpec] = field(default_factory=list)
    eval_suite_id: str | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> "AgentManifest":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(
            name=data["name"],
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            autonomy_mode=data.get("autonomy_mode", "bounded-autonomous"),
            goal_template=data.get("goal_template", ""),
            default_model=data.get("default_model", ""),
            connector_requirements=[
                ConnectorRequirement(**c) for c in data.get("connector_requirements", [])
            ],
            knowledge_collections=data.get("knowledge_collections", []),
            policies=[PolicySpec(**p) for p in data.get("policies", [])],
            eval_suite_id=data.get("eval_suite_id"),
            tags=data.get("tags", []),
        )

    def to_yaml(self) -> str:
        return yaml.dump({
            "name": self.name, "version": self.version, "description": self.description,
            "autonomy_mode": self.autonomy_mode, "goal_template": self.goal_template,
        }, default_flow_style=False)

    def validate(self) -> list[str]:
        errors = []
        if not self.name.strip():
            errors.append("name is required")
        if self.autonomy_mode not in {"supervised", "bounded-autonomous", "fully-autonomous"}:
            errors.append(f"invalid autonomy_mode: {self.autonomy_mode}")
        if not self.version.count(".") >= 1:
            errors.append("version must be semver (e.g. '1.0.0')")
        for p in self.policies:
            if p.action not in {"deny", "require_approval"}:
                errors.append(f"policy '{p.name}': invalid action '{p.action}'")
        return errors
```

---

## Phase 21 — CLI and API Automation

### 21.1 🔴 Missing CLI Commands

**Add to `app/cli/main.py`**:
```python
@app.command()
def login(
    api_key: str = typer.Option(..., "--key", "-k", prompt="API Key", hide_input=True),
    base_url: str = typer.Option("http://localhost:8000", "--url"),
):
    """Save API key to ~/.agentverse/config.json."""
    from pathlib import Path
    config_dir = Path.home() / ".agentverse"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.json").write_text(
        __import__("json").dumps({"api_key": api_key, "base_url": base_url})
    )
    typer.echo("✓ Credentials saved")


@app.command(name="goals")
def list_goals_cmd(limit: int = typer.Option(20, "--limit", "-n")):
    """List recent goals."""
    data = _get(f"{_base_url()}/goals", _api_key())
    for g in (data.get("goals") or [])[:limit]:
        gid = g.get("goal_id", g.get("id", "?"))[:12]
        typer.echo(f"{gid}  {g.get('status','?'):14} {g.get('goal','')[:60]}")


@app.command()
def cancel(goal_id: str = typer.Argument(..., help="Goal ID to cancel")):
    """Cancel a running goal."""
    result = _post(f"{_base_url()}/goals/{goal_id}/cancel", _api_key(), {})
    typer.echo(f"Cancelled: {result.get('status')}")


@app.command()
def approve(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    note: str = typer.Option("", "--note", "-n"),
):
    """Approve a pending HITL request."""
    result = _post(
        f"{_base_url()}/governance/approvals/{request_id}/approve",
        _api_key(),
        {"approver": "cli-user", "note": note},
    )
    typer.echo(f"Approved: {result}")


@app.command()
def connector_list():
    """List registered MCP connectors."""
    data = _get(f"{_base_url()}/connectors", _api_key())
    for c in (data if isinstance(data, list) else []):
        typer.echo(f"{c.get('server_id','?')[:16]}  {c.get('name','?'):20} {c.get('status','?')}")


@app.command()
def eval_goal(goal_id: str = typer.Argument(..., help="Goal ID to evaluate")):
    """Show eval scorecard for a completed goal."""
    data = _get(f"{_base_url()}/goals/{goal_id}/eval", _api_key())
    scores = data.get("scores", {})
    for dim, score in scores.items():
        bar = "█" * int((score or 0) * 20)
        typer.echo(f"  {dim:20} {bar:<20} {score:.2f}")
    avg = data.get("average_score", 0)
    typer.echo(f"\n  Average: {avg:.2f}  {'✓ PASS' if avg >= 0.7 else '✗ FAIL'}")


@app.command()
def logs(
    goal_id: str = typer.Argument(...),
    tail: int = typer.Option(50, "--tail", "-n"),
):
    """Show recent events for a goal."""
    data = _get(f"{_base_url()}/goals/{goal_id}/events", _api_key())
    events = data if isinstance(data, list) else []
    for evt in events[-tail:]:
        ts = str(evt.get("ts", ""))[:19]
        typer.echo(f"[{ts}]  {evt.get('type',''):25} {str(evt.get('step', evt.get('output','')))[:60]}")
```

---

## Phase 22 — Production Deployment Platform

### 22.1 🔴 KEDA ScaledObject for Worker Autoscaling

New `infra/k8s/keda-scaledobject.yaml`:
```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: agentverse-worker-goals
  namespace: agentverse
spec:
  scaleTargetRef:
    name: agentverse-worker
  pollingInterval: 10
  cooldownPeriod: 60
  minReplicaCount: 1
  maxReplicaCount: 20
  triggers:
  - type: redis
    metadata:
      address: redis-service.agentverse.svc.cluster.local:6379
      listName: celery
      listLength: "5"
      enableTLS: "false"
```

### 22.2 🔴 Frontend Dockerfile + K8s

New `agent-verse-frontend/Dockerfile`:
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --frozen-lockfile
COPY . .
ARG VITE_API_URL=https://api.agentverse.ai
ARG VITE_GRAFANA_URL=https://grafana.agentverse.ai
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY infra/nginx-frontend.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s CMD wget -qO- http://localhost/health || exit 1
```

New `infra/nginx-frontend.conf`:
```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    gzip on;
    gzip_types text/plain text/css application/javascript application/json;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /health {
        return 200 "OK";
        add_header Content-Type text/plain;
    }
}
```

New `infra/k8s/frontend-deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentverse-frontend
  namespace: agentverse
spec:
  replicas: 2
  selector:
    matchLabels:
      app: agentverse-frontend
  template:
    metadata:
      labels:
        app: agentverse-frontend
    spec:
      containers:
      - name: frontend
        image: agentverse/frontend:latest
        ports:
        - containerPort: 80
        livenessProbe:
          httpGet: { path: /health, port: 80 }
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet: { path: /, port: 80 }
          initialDelaySeconds: 5
        resources:
          requests: { cpu: 50m, memory: 64Mi }
          limits: { cpu: 200m, memory: 128Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: agentverse-frontend
  namespace: agentverse
spec:
  selector:
    app: agentverse-frontend
  ports:
  - port: 80
    targetPort: 80
```

### 22.3 🔴 Database Backup CronJob

New `infra/k8s/pg-backup-cronjob.yaml`:
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: agentverse-pg-backup
  namespace: agentverse
spec:
  schedule: "0 2 * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 7
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
          - name: pg-backup
            image: postgres:16-alpine
            command:
            - /bin/sh
            - -c
            - |
              FILENAME="agentverse-$(date +%Y%m%d-%H%M%S).sql.gz"
              pg_dump "$DATABASE_URL" | gzip > /tmp/$FILENAME
              echo "Backup complete: $FILENAME ($(du -sh /tmp/$FILENAME | cut -f1))"
            envFrom:
            - secretRef:
                name: agentverse-secrets
```

---

## Phase 23 — Reliability Layer

### 23.1 🔴 Wire Circuit Breaker to MCP Tool Calls

**Current state:** `redis_circuit_breaker.py` exists but is never instantiated or used.

**Fix:** `app/mcp/client.py`:
```python
class MCPClient:
    def __init__(self, registry, secret_resolver=None, redis_client=None):
        self._registry = registry
        self._secret_resolver = secret_resolver
        self._redis = redis_client
        self._circuit_breakers: dict[str, Any] = {}

    def _get_cb(self, server_id: str) -> Any:
        if self._redis is None:
            return None
        if server_id not in self._circuit_breakers:
            from app.reliability.redis_circuit_breaker import RedisCircuitBreaker
            self._circuit_breakers[server_id] = RedisCircuitBreaker(
                redis=self._redis, name=f"mcp:{server_id}",
                failure_threshold=5, cooldown_seconds=60
            )
        return self._circuit_breakers[server_id]

    async def call_tool(self, server_id, tool_name, arguments, tenant_ctx) -> dict:
        cb = self._get_cb(server_id)
        if cb:
            allowed = await cb.allow_request(
                tenant_id=tenant_ctx.tenant_id, tool_name=tool_name
            )
            if not allowed:
                from app.reliability.circuit_breaker import CircuitBreakerOpenError
                raise CircuitBreakerOpenError(
                    f"Circuit breaker open for {server_id}. Retry after cooldown."
                )
        try:
            result = await self._call_tool_impl(server_id, tool_name, arguments, tenant_ctx)
            if cb:
                await cb.record_success(
                    tenant_id=tenant_ctx.tenant_id, tool_name=tool_name
                )
            return result
        except Exception:
            if cb:
                await cb.record_failure(
                    tenant_id=tenant_ctx.tenant_id, tool_name=tool_name
                )
            raise
```

Wire `redis_client` in `main.py`:
```python
_mcp_client = MCPClient(registry=_mcp_registry,
                         secret_resolver=_resolve_connector_secret,
                         redis_client=real_redis)  # in lifespan when real Redis available
```

### 23.2 🔴 Stuck Goal Detector

**New Celery task** in `app/scaling/tasks.py`:
```python
@celery_app.task(name="app.scaling.tasks.detect_stuck_goals",
                 bind=True, max_retries=0)
def detect_stuck_goals(self: Any) -> dict[str, Any]:
    return _run_async(_find_and_fail_stuck_goals())

async def _find_and_fail_stuck_goals() -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta
    from app.db.session import get_session_factory
    timeout_minutes = 60
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    try:
        db = get_session_factory()
        from sqlalchemy import text
        async with db() as session, session.begin():
            result = await session.execute(
                text("""UPDATE goals
                        SET status='failed',
                            error_message='Stuck goal: exceeded 60 minute execution timeout',
                            updated_at=NOW()
                        WHERE status IN ('executing','planning')
                          AND updated_at < :cutoff
                        RETURNING id, tenant_id"""),
                {"cutoff": cutoff}
            )
            stuck = result.fetchall()
        return {"stuck_goals_failed": len(stuck),
                "goal_ids": [r[0] for r in stuck[:20]]}
    except Exception as exc:
        return {"error": str(exc)}
```

Add to Beat schedule (every 5 minutes).

---

## Phase 24 — Compliance

### 24.1 🔴 SOC2 Audit Log Fields (IP, User-Agent, API Key, Request ID)

**Migration** `app/db/migrations/versions/0018_audit_log_soc2.py`:
```python
def upgrade():
    op.add_column("audit_log", sa.Column("ip_address", sa.String(45), nullable=True))
    op.add_column("audit_log", sa.Column("user_agent", sa.String(500), nullable=True))
    op.add_column("audit_log", sa.Column("api_key_id", sa.String(64), nullable=True))
    op.add_column("audit_log", sa.Column("request_id", sa.String(64), nullable=True))
    op.create_index("ix_audit_log_api_key_id", "audit_log", ["api_key_id"])

def downgrade():
    op.drop_index("ix_audit_log_api_key_id")
    for col in ["ip_address", "user_agent", "api_key_id", "request_id"]:
        op.drop_column("audit_log", col)
```

**Block TRUNCATE** in migration:
```python
op.execute("REVOKE TRUNCATE ON audit_log FROM PUBLIC")
op.execute("REVOKE TRUNCATE ON audit_log FROM agentverse")
```

### 24.2 🔴 Retention Policy Celery Task

**New task** in `app/scaling/tasks.py`:
```python
@celery_app.task(name="app.scaling.tasks.execute_retention_policy",
                 bind=True, max_retries=1)
def execute_retention_policy(self: Any) -> dict[str, Any]:
    retention_days = int(__import__("os").getenv("DATA_RETENTION_DAYS", "90"))
    return _run_async(_delete_expired_records(retention_days))

async def _delete_expired_records(retention_days: int) -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta
    from app.db.session import get_session_factory
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    counts = {}
    try:
        db = get_session_factory()
        from sqlalchemy import text
        async with db() as session, session.begin():
            # Delete old goal events (preserve goal records for audit)
            r = await session.execute(
                text("DELETE FROM goal_events WHERE created_at < :c"), {"c": cutoff})
            counts["goal_events"] = r.rowcount
            # Delete old decision traces
            r = await session.execute(
                text("DELETE FROM decision_traces WHERE created_at < :c"), {"c": cutoff})
            counts["decision_traces"] = r.rowcount
        return {"retention_days": retention_days, "cutoff": cutoff.isoformat(),
                "deleted": counts}
    except Exception as exc:
        return {"error": str(exc)}
```

Add to Beat schedule: daily at 3 AM UTC.

---

## Phase 25 — UI/UX: Missing Pages and Critical Fixes

### 25.1 🔴 Execution Timeline Component

New `src/components/execution/ExecutionTimeline.tsx`:
```tsx
import { useMemo } from "react";
import type { GoalEvent } from "@/lib/sse/useGoalStream";

interface TimelineBlock {
  type: string;
  ts: string;
  label: string;
  durationMs?: number;
  status: "complete" | "failed" | "executing" | "waiting";
}

function buildTimeline(events: GoalEvent[]): TimelineBlock[] {
  return events
    .filter(e => ["goal_started","plan_ready","step_started","step_complete",
                   "tool_call_complete","verification_done","goal_complete",
                   "goal_failed","waiting_approval","approval_granted"].includes(e.type as string))
    .map(e => ({
      type: e.type as string,
      ts: (e.ts as string) || new Date().toISOString(),
      label: (e.type as string).replace(/_/g, " "),
      status: (e.type as string).includes("complete") || (e.type as string).includes("granted")
        ? "complete"
        : (e.type as string).includes("failed") ? "failed"
        : (e.type as string).includes("waiting") ? "waiting"
        : "executing",
    }));
}

export function ExecutionTimeline({ events }: { events: GoalEvent[] }) {
  const blocks = useMemo(() => buildTimeline(events), [events]);
  if (blocks.length === 0) return null;
  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="font-semibold text-sm">Execution Timeline</h2>
      </div>
      <div className="px-4 py-4 overflow-x-auto">
        <div className="flex items-center gap-1 min-w-max">
          {blocks.map((block, i) => (
            <div key={i} className="flex flex-col items-center min-w-[90px]">
              <div className={`px-2 py-1.5 rounded text-xs font-medium text-center w-full
                ${block.status === "complete" ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400"
                  : block.status === "failed" ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                  : block.status === "waiting" ? "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400"
                  : "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse"}`}>
                {block.label}
              </div>
              <div className="text-[10px] text-muted-foreground mt-0.5">
                {block.ts.slice(11, 19)}
              </div>
              {i < blocks.length - 1 && (
                <div className="absolute mt-3 ml-[90px] h-px w-2 bg-border" />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

Wire into `GoalDetailPage.tsx` — add below pipeline steps section.

---

### 25.2 🔴 Dedicated Approval Inbox Page (`/approvals`)

New `src/features/approvals/ApprovalsPage.tsx`:
```tsx
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, Clock } from "lucide-react";
import { useState } from "react";
import { governanceApi } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";

export function ApprovalsPage() {
  const tenantId = useAuthStore(s => s.tenantId);
  const qc = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});

  const { data: approvals = [], isLoading } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 5_000,
  });

  const pending = approvals.filter(a => a.status === "pending");

  const approveMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      governanceApi.approve(id, tenantId, note),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      governanceApi.reject(id, tenantId, note),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">Approval Inbox</h1>
        {pending.length > 0 && (
          <span className="bg-orange-500 text-white text-xs font-bold px-2 py-1 rounded-full">
            {pending.length}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="text-center py-10 text-sm text-muted-foreground">Loading…</div>
      ) : pending.length === 0 ? (
        <div className="bg-card border border-border rounded-xl py-16 text-center">
          <CheckCircle className="h-10 w-10 text-green-500 mx-auto mb-3" />
          <p className="font-medium">No pending approvals</p>
          <p className="text-sm text-muted-foreground mt-1">Your agents are running autonomously.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {pending.map(req => (
            <div key={req.request_id}
              className="bg-card border border-orange-200 dark:border-orange-800 rounded-xl p-5">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <p className="font-semibold text-sm">{req.action}</p>
                  <p className="text-xs text-muted-foreground font-mono mt-0.5">
                    goal: {req.goal_id} · risk: {req.risk_level || "unknown"}
                  </p>
                </div>
                <Clock className="h-4 w-4 text-orange-500 flex-shrink-0 mt-0.5" />
              </div>
              <textarea
                value={notes[req.request_id] ?? ""}
                onChange={e => setNotes(n => ({ ...n, [req.request_id]: e.target.value }))}
                placeholder="Optional note…"
                rows={2}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary resize-none mb-3"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => approveMutation.mutate({
                    id: req.request_id, note: notes[req.request_id] ?? ""
                  })}
                  disabled={approveMutation.isPending}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-green-600 text-white text-sm rounded-md hover:bg-green-700 disabled:opacity-50"
                >
                  <CheckCircle className="h-3.5 w-3.5" /> Approve
                </button>
                <button
                  onClick={() => rejectMutation.mutate({
                    id: req.request_id, note: notes[req.request_id] ?? ""
                  })}
                  disabled={rejectMutation.isPending}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50"
                >
                  <XCircle className="h-3.5 w-3.5" /> Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

Add route in `App.tsx`: `<Route path="/approvals" element={<ApprovalsPage />} />`.

Add sidebar badge in `Sidebar.tsx`:
```tsx
// In the nav item for /approvals:
const { data: approvals = [] } = useQuery({
  queryKey: ["approvals"],
  queryFn: () => governanceApi.listApprovals(),
  refetchInterval: 10_000,
  enabled: isAuthenticated,
});
const pendingCount = approvals.filter(a => a.status === "pending").length;

// In nav item render:
{pendingCount > 0 && (
  <span className="ml-auto bg-orange-500 text-white text-xs font-bold px-1.5 py-0.5 rounded-full">
    {pendingCount}
  </span>
)}
```

---

### 25.3 🔴 `SchedulesPage.tsx` — Fix `next_run_at` Always `—`

**Root cause:** `ScheduleStore` never computes `next_run_at`.

**Fix in `app/triggers/store.py`**:
```python
def _compute_next_run(self, record: dict) -> str | None:
    trigger_type = record.get("trigger_type", "")
    try:
        if trigger_type == "cron":
            from croniter import croniter
            from datetime import UTC, datetime
            cron = record.get("cron_expression", "")
            if cron:
                it = croniter(cron, datetime.now(UTC))
                return it.get_next(datetime).isoformat()
        elif trigger_type == "interval":
            interval_s = record.get("interval_seconds", 0)
            last_fired = record.get("last_fired_at")
            if interval_s and last_fired:
                from datetime import UTC, datetime, timedelta
                last = datetime.fromisoformat(last_fired)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                return (last + timedelta(seconds=interval_s)).isoformat()
        elif trigger_type == "once":
            return record.get("fire_at_iso")
    except Exception:
        pass
    return None
```

Call `_compute_next_run()` in `list()` and `get()` to populate `next_run_at`.

---

### 25.4 🟢 Agent Detail Page (`/agents/:agentId`)

New `src/features/agents/AgentDetailPage.tsx` — complete implementation with:
- Agent metadata (name, autonomy_mode, connector_ids, created_at)
- Edit name/goal_template form → `PUT /agents/{id}`
- Snapshot button → `POST /agents/{id}/snapshot`
- Version history list → `GET /agents/{id}/versions`
- Rollback button → `POST /agents/{id}/rollback/{snapshot_id}`
- Export button (OpenAI/Anthropic format) → `GET /agents/{id}/export?format=openai`
- Recent goals for this agent (filter `GET /goals` by agent_id)

---

### 25.5 🟢 In-App Cost Dashboard

New `src/features/observability/CostDashboardPage.tsx`:

Add to `app/api/goals.py`:
```python
@router.get("/cost-metrics")
async def get_cost_metrics(request: Request) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    metrics = await svc.get_metrics(tenant_ctx=tenant)
    cost = getattr(request.app.state, "cost_controller", None)
    budget_cfg = getattr(request.app.state, "_budget_config", {}).get(tenant.tenant_id)
    daily_budget = budget_cfg.per_tenant_daily_usd if budget_cfg else 500.0
    return {
        **metrics,
        "daily_budget_usd": daily_budget,
        "budget_utilization": metrics["cost_today_usd"] / daily_budget,
    }
```

Frontend component uses `<LineChart>` from Recharts to show cost over time.

---

## Complete Test Requirements for All Phases

### Backend Test Minimum Per New Feature

```python
# tests/{module}/test_{feature}.py

# 1. Unit test — no external deps
def test_{feature}_core_behavior(): ...
def test_{feature}_edge_cases(): ...

# 2. API test — httpx
@pytest.mark.asyncio
async def test_{feature}_api_401_without_key(app): ...

@pytest.mark.asyncio
async def test_{feature}_api_happy_path(signed_up_client): ...

@pytest.mark.asyncio
async def test_{feature}_api_422_invalid_input(signed_up_client): ...

@pytest.mark.asyncio
async def test_{feature}_api_404_not_found(signed_up_client): ...

@pytest.mark.asyncio
async def test_{feature}_tenant_isolation(app): ...
```

### Frontend Test Minimum Per New Page/Component

```tsx
describe("{Feature}", () => {
  it("renders heading");
  it("shows loading state");
  it("renders data from API");
  it("shows empty state when no data");
  it("calls API on user action (button click)");
  it("shows error when API returns 500");
  it("is accessible — key interactive elements have aria-label");
});
```

### E2E Test Minimum Per Critical Journey

```typescript
test.describe("{Journey} E2E", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("av-auth", JSON.stringify({
        state: { apiKey: "test-key", tenantId: "t1", isAuthenticated: true },
        version: 0,
      }));
      localStorage.setItem("av_api_key", "test-key");
    });
  });

  test("happy path", async ({ page }) => { /* mock APIs, navigate, act, assert */ });
  test("error path", async ({ page }) => { /* mock 500, assert error UI */ });
});
```
