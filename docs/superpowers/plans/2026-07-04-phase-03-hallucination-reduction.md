# Phase 3: Hallucination Reduction v7+ — Detailed Plan

> REQUIRED SUB-SKILL superpowers:subagent-driven-development

This plan is executable task-by-task. Each `### Task N` is one TDD cycle: write a **failing test with real code**, run it and confirm it FAILS for the stated reason, write the **real implementation**, run it and confirm it PASSES, run `uv run ruff check . && uv run mypy app`, then commit. No task depends on a task numbered higher than itself. Tasks within a track share files, so run them in order within a track; tracks A→F are ordered because B/C/D consume the structured-output primitive from A.

---

## Goal

Drive the ungrounded-claim rate on the golden set below **1%** and the verifier false-confirm rate below **2%**, with **zero fabricated entity IDs** in outputs. Prerequisite (assumed merged): Phase 0 wiring — `llm_response_cache`, cross-model verifier in both the `goal_service` and Celery paths, and the atomic cost controller. This phase adds five hard guarantees on top of the existing strong prompts + verifier:

1. **Claim grounding** — every concrete claim (ID, count, URL, date, quoted value) an executor step emits must be traceable to that step's tool output, or the step is marked `UNGROUNDED` and the goal replans.
2. **Citation-carrying answers** — the final synthesis cites step/tool provenance for every factual claim; the UI renders citations.
3. **Consensus verification** — goals touching `write_high`/`destructive` tools or regulated domains require a 3-way majority verdict (primary verifier + cross-model verifier + rubric-scored `LLMJudge`); disagreement routes through the HITL gateway.
4. **Verifier calibration** — verifier verdicts are logged against eventual outcomes in a `verifier_calibration` table; a report exposes the false-confirm rate.
5. **Provider-native structured outputs** — planner and verifier stop parsing free-text JSON (`_parse_json` / `_parse_verifier_response` guessing) and receive schema-constrained JSON, eliminating a whole class of parse-then-hallucinate failures.

And one process guarantee:

6. **Golden-set regression gate** — any change to `app/agent/prompts.py` (or the grounding/consensus prompts) must pass a hallucination golden suite before merge.

## Architecture

The agent loop is a LangGraph `StateGraph` in `app/agent/graph.py`:
`initialize → rag_retrieval → (think?) → plan → execute → verify → (complete | replan | max_iter | waiting_human)`.
Phase 3 inserts grounding **inside** `_node_execute` (per-step, after each step produces output) and upgrades `_node_verify` to route through a `ConsensusVerifier` when the goal is high-stakes. A new `synthesize` node runs on the success path to produce a cited final answer. All new components are constructed in `create_app()`, bound to `app.state`, and passed into both `AgentGraph` construction sites (`app/services/goal_service.py:764` and `app/scaling/tasks.py:785`) exactly like the existing `verifier`/`llm_response_cache` kwargs — follow the two-phase wiring pattern.

New modules:
- `app/agent/schemas.py` — pydantic response schemas (`PlannerPlan`, `VerifierVerdict`) for structured outputs.
- `app/agent/grounding.py` — deterministic + cheap-LLM claim grounding.
- `app/agent/consensus.py` — 3-way consensus verification + HITL escalation.
- `app/agent/synthesis.py` — citation-carrying final answer synthesis.
- `app/intelligence/verifier_calibration.py` — calibration store + false-confirm report.
- `app/db/models/eval.py` (extend) — `VerifierCalibration` ORM.
- `app/db/migrations/versions/0072_verifier_calibration.py` — table + RLS.
- Frontend: `agent-verse-frontend/src/features/goals/` — citation rendering.

## Tech Stack

Python 3.12 / FastAPI / LangGraph / SQLAlchemy 2 async + asyncpg / Alembic / Redis / Postgres+pgvector. Providers via the `LLMProvider` protocol in `app/providers/base.py` (`anthropic_provider`, `openai_compatible`, `gemini_provider`, `voyage_provider`, `FakeProvider`). Frontend: React 19 / Vite / TanStack Query / Tailwind. Tests: pytest (backend, `filterwarnings=error`), vitest + Playwright (frontend).

## Global Constraints

- Backend: prefix every command with `uv run` (system Python is 3.9; backend is 3.12 via uv).
- Lint/type from `pyproject.toml`: ruff line-length 100, target py312, rule set `E,F,I,N,UP,B,A,C4,SIM,RUF`; mypy `strict` with the pydantic plugin. Every task ends green on `uv run ruff check . && uv run mypy app`.
- `pytest` treats warnings as errors (`filterwarnings=["error"]`). Do not introduce a `DeprecationWarning`. Use `httpx2` transport patterns already in the suite for any `TestClient` work.
- **Fail-closed:** grounding failure, consensus-verifier error, or calibration-store error must never silently upgrade a step to "grounded"/"success". On dependency error, the safe verdict is UNGROUNDED / not-verified / require-HITL — never auto-pass. Replace bare `except Exception: pass` around any correctness signal with logged, typed handling.
- New table carries a tenant RLS policy (`ENABLE` + `FORCE` + `USING`/`WITH CHECK` on `current_setting('app.tenant_id', TRUE)`, mirroring `0046_workflows.py`), `created_at`/`updated_at`, and a test asserting cross-tenant isolation.
- Migrations are forward-only and additive. Next revision is `0072` (current head is `0070`; `170245f26dcb` is a sibling — set `down_revision = "0070"`).
- **Standard deliverable (world-class bar):** backend service on `app.state` with structured errors + OTel spans + a Prometheus counter; the UI slice with WCAG 2.2 AA (axe-clean) loading/empty/error states; unit ≥80% line coverage on new code, integration tests for the DB/RLS path, and a Playwright e2e for the citation UI.
- Determinism in tests: use `FakeProvider(responses=[...])` (scripted, cycling; `call_history` records every `CompletionRequest`) — never hit a real provider. Mark any real-LLM test `slow`.

## File Structure

```
agent-verse-backend/
  app/agent/
    schemas.py                     # NEW — PlannerPlan, VerifierVerdict pydantic models
    grounding.py                   # NEW — Claim, GroundingResult, extract_claims,
                                   #        deterministic_ground, GroundingChecker
    consensus.py                   # NEW — VerifierVote, ConsensusResult, ConsensusVerifier
    synthesis.py                   # NEW — CitedAnswer, AnswerSynthesizer
    state.py                       # EDIT — StepStatus.UNGROUNDED; AgentState.provenance,
                                   #        ungrounded_claims, cited_answer
    prompts.py                     # EDIT — GROUNDING_SYSTEM, SYNTHESIS_SYSTEM, JUDGE_RUBRIC_SYSTEM
    graph.py                       # EDIT — structured planner/verifier, grounding in _node_execute,
                                   #        consensus in _node_verify, new _node_synthesize
  app/providers/
    base.py                        # EDIT — CompletionRequest.response_schema, supports_structured_output
    anthropic_provider.py          # EDIT — forced-tool structured output
    openai_compatible.py           # EDIT — response_format json_schema
    gemini_provider.py             # EDIT — response_mime_type + response_schema
    fake.py                        # EDIT — record response_schema; supports_structured_output
  app/intelligence/
    verifier_calibration.py        # NEW — VerifierCalibrationStore
  app/db/models/eval.py            # EDIT — VerifierCalibration ORM
  app/db/migrations/versions/
    0072_verifier_calibration.py   # NEW — table + indexes + RLS
  app/api/goals.py                 # EDIT — expose citations + grounding status in result payload
  tests/
    providers/test_structured_output.py        # NEW
    agent/test_schemas.py                       # NEW
    agent/test_grounding.py                     # NEW
    agent/test_grounding_in_graph.py            # NEW
    agent/test_consensus.py                     # NEW
    agent/test_consensus_in_graph.py            # NEW
    agent/test_synthesis.py                     # NEW
    intelligence/test_verifier_calibration.py   # NEW
    intelligence/test_verifier_calibration_rls.py  # NEW (integration)
    intelligence/test_hallucination_golden_gate.py # NEW
    fixtures/hallucination_golden.json          # NEW
agent-verse-frontend/
  src/features/goals/CitationList.tsx           # NEW
  src/features/goals/CitationList.test.tsx      # NEW
  e2e/goal-citations.spec.ts                    # NEW
```

---

## Track A — Provider-native structured outputs (kills parse-then-hallucinate)

### Task 1: Add `response_schema` to the provider contract

**Files:** `app/providers/base.py`, `app/providers/fake.py`. Test: `tests/providers/test_structured_output.py`.

**Interfaces:**
```python
# base.py — extend CompletionRequest
@dataclass
class CompletionRequest:
    messages: list[Message]
    model: str
    system: str | None = None
    tools: list[ToolDefinition] = field(default_factory=list)
    max_tokens: int = 4096
    temperature: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    response_schema: dict[str, Any] | None = None  # JSON Schema; when set, provider
                                                    # MUST return content that is a
                                                    # single valid JSON object matching it

# LLMProvider protocol — new capability method (default helper for existing providers)
def supports_structured_output(self) -> bool: ...
```

Steps:
- [ ] Write failing test `tests/providers/test_structured_output.py::test_fake_records_response_schema`:
```python
import pytest
from app.providers.base import CompletionRequest, Message
from app.providers.fake import FakeProvider


@pytest.mark.asyncio
async def test_fake_records_response_schema() -> None:
    fake = FakeProvider(responses=['{"steps": []}'])
    schema = {"type": "object", "properties": {"steps": {"type": "array"}}}
    await fake.complete(
        CompletionRequest(
            messages=[Message(role="user", content="plan it")],
            model="x",
            response_schema=schema,
        )
    )
    assert fake.call_history[-1].response_schema == schema
    assert fake.supports_structured_output() is True
```
- [ ] Run: `uv run pytest tests/providers/test_structured_output.py::test_fake_records_response_schema -v` → **FAIL** (`TypeError: __init__() got an unexpected keyword argument 'response_schema'` then `AttributeError: 'FakeProvider' object has no attribute 'supports_structured_output'`).
- [ ] Implement: add the `response_schema` field to `CompletionRequest`; add `supports_structured_output(self) -> bool` to the `LLMProvider` Protocol; add `def supports_structured_output(self) -> bool: return True` to `FakeProvider` (it echoes scripted JSON, so it "supports" it deterministically). `FakeProvider.complete` already appends the request to `call_history`, so the field is captured automatically.
- [ ] Run: same command → **PASS**.
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `feat(providers): add response_schema to CompletionRequest and supports_structured_output`.

### Task 2: Implement structured output in the three real providers

**Files:** `app/providers/anthropic_provider.py`, `app/providers/openai_compatible.py`, `app/providers/gemini_provider.py`. Test: `tests/providers/test_structured_output.py`.

**Interfaces (behavioral contract, no signature change to `complete`):** when `request.response_schema is not None`, the provider constrains the model to emit exactly one JSON object matching the schema.
- Anthropic: inject a single synthetic tool `{"name": "emit", "input_schema": response_schema}` and set `tool_choice={"type": "tool", "name": "emit"}`; return `json.dumps(tool_use.input)` as `CompletionResponse.content`. (Do this only when `request.tools` is empty; if the caller already passes tools, prefer prefill assistant-turn `{` and still validate.)
- OpenAI-compatible: set `response_format={"type": "json_schema", "json_schema": {"name": "response", "schema": response_schema, "strict": True}}`.
- Gemini: set `generation_config={"response_mime_type": "application/json", "response_schema": response_schema}`.
- `supports_structured_output` returns `True` for all three.

Steps:
- [ ] Write failing test `test_anthropic_structured_forces_tool` that constructs the provider with a **fake httpx transport** (reuse the transport-injection pattern already used in `tests/providers/`; grep `tests/providers/test_anthropic*` for the existing mock-transport fixture) and asserts the outgoing request body contains `"tool_choice"` with `"name": "emit"` and that `complete()` returns the tool input as JSON text. Add analogous `test_openai_structured_sets_response_format` and `test_gemini_structured_sets_response_schema`.
- [ ] Run: `uv run pytest tests/providers/test_structured_output.py -v` → **FAIL** (providers ignore `response_schema`; assertion on request body fails).
- [ ] Implement in each provider's `complete()` (the branch that builds the request payload, circa `anthropic_provider.py:44`, `openai_compatible.py`, `gemini_provider.py`). Keep the non-schema path byte-for-byte identical (guard with `if request.response_schema is not None:`). Add `supports_structured_output(self) -> bool: return True` to each.
- [ ] Run: → **PASS**.
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `feat(providers): provider-native structured JSON output for anthropic/openai/gemini`.

### Task 3: Define planner + verifier response schemas

**Files:** `app/agent/schemas.py` (new), `app/agent/prompts.py` (no change yet). Test: `tests/agent/test_schemas.py`.

**Interfaces:**
```python
# app/agent/schemas.py
from __future__ import annotations
from pydantic import BaseModel, Field


class PlannerPlan(BaseModel):
    steps: list[str] = Field(default_factory=list)


class VerifierVerdict(BaseModel):
    success: bool
    reason: str = ""
    retry: bool = True

    @classmethod
    def json_schema(cls) -> dict:
        """JSON Schema for provider structured output (drops pydantic-only keys)."""
        ...


def planner_schema() -> dict: ...
def verifier_schema() -> dict: ...
```

Steps:
- [ ] Failing test `tests/agent/test_schemas.py`:
```python
from app.agent.schemas import PlannerPlan, VerifierVerdict, planner_schema, verifier_schema


def test_verifier_schema_is_object_with_success() -> None:
    s = verifier_schema()
    assert s["type"] == "object"
    assert "success" in s["properties"]
    assert s["properties"]["success"]["type"] == "boolean"


def test_planner_plan_parses_steps() -> None:
    p = PlannerPlan.model_validate({"steps": ["a", "b"]})
    assert p.steps == ["a", "b"]


def test_verifier_verdict_defaults_retry_true() -> None:
    v = VerifierVerdict.model_validate({"success": False, "reason": "x"})
    assert v.retry is True
```
- [ ] Run: `uv run pytest tests/agent/test_schemas.py -v` → **FAIL** (module does not exist).
- [ ] Implement `app/agent/schemas.py`. `planner_schema()`/`verifier_schema()` build a clean JSON Schema dict (`additionalProperties: False`, `required: ["success"]` for verifier) — do NOT pass raw `model_json_schema()` to providers (it contains `$defs`/`title` some providers reject; strip to the minimal object).
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(agent): planner and verifier response schemas`.

### Task 4: Route planner + verifier through structured output

**Files:** `app/agent/graph.py` (`_node_plan` circa 726–753; `_node_verify` circa 2180–2209). Test: `tests/agent/test_grounding_in_graph.py` is later — here add `tests/agent/test_structured_nodes.py`.

**Interfaces:** no signature change. In `_node_plan`, when `self._planner.supports_structured_output()`, set `req.response_schema = planner_schema()` before `self._planner.complete(req)`; parse with `PlannerPlan.model_validate_json(resp.content)` and fall back to the existing `_parse_json(resp.content, key="steps")` on `ValidationError`. In `_node_verify`, when `self._verifier.supports_structured_output()`, set `req.response_schema = verifier_schema()`; parse with `VerifierVerdict.model_validate_json(resp.content)`, fall back to `_parse_verifier_response`.

Steps:
- [ ] Failing test `tests/agent/test_structured_nodes.py::test_verifier_uses_structured_schema`:
```python
import pytest
from app.agent.graph import AgentGraph
from app.providers.fake import FakeProvider
from app.tenancy.context import TenantContext


@pytest.mark.asyncio
async def test_verifier_uses_structured_schema() -> None:
    planner = FakeProvider(responses=['{"steps": ["do it"]}'])
    verifier = FakeProvider(responses=['{"success": true, "reason": "done"}'])
    g = AgentGraph(planner=planner, executor=FakeProvider(), verifier=verifier)
    ctx = TenantContext(tenant_id="t1", api_key_id="k1", plan="professional")
    await g.run(goal="do it", tenant_ctx=ctx)
    verify_reqs = [r for r in verifier.call_history if r.response_schema is not None]
    assert verify_reqs, "verifier was not called with response_schema"
    assert verify_reqs[-1].response_schema["properties"]["success"]["type"] == "boolean"
```
- [ ] Run: `uv run pytest tests/agent/test_structured_nodes.py -v` → **FAIL** (`response_schema` is `None` on every verifier call).
- [ ] Implement the two node edits. Keep the LLM-response-cache and model-router logic intact — only add the `response_schema` assignment and the pydantic parse-with-fallback. Guard on `getattr(provider, "supports_structured_output", lambda: False)()` so non-conforming fakes/providers still work.
- [ ] Run: → **PASS**. Regression: `uv run pytest tests/agent -k "verify or plan or loop" -q` → all green (existing text-format tests still pass via the fallback).
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `feat(hallucination): planner and verifier use provider-native structured outputs with text fallback`.

---

## Track B — Claim-grounding checker

### Task 5: Deterministic claim extraction + cross-check pre-pass

**Files:** `app/agent/grounding.py` (new). Test: `tests/agent/test_grounding.py`.

**Interfaces:**
```python
# app/agent/grounding.py
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Claim:
    value: str                # the exact substring that must be grounded
    kind: str                 # "id" | "number" | "url" | "date" | "quote"


@dataclass
class GroundingResult:
    grounded: list[Claim] = field(default_factory=list)
    ungrounded: list[Claim] = field(default_factory=list)
    checked_by: str = "deterministic"   # "deterministic" | "llm" | "skipped"

    @property
    def is_grounded(self) -> bool:
        return not self.ungrounded


def extract_claims(text: str) -> list[Claim]:
    """Regex-extract concrete claims: ticket IDs (PROJ-123), bare numbers/counts,
    URLs, ISO/date-like strings, and quoted literals."""


def deterministic_ground(
    claims: list[Claim], tool_output: str
) -> tuple[list[Claim], list[Claim]]:
    """Return (grounded, ungrounded). A claim is grounded when its value appears
    as a normalized substring of tool_output. Numbers are matched digit-normalized."""
```

Steps:
- [ ] Failing test `tests/agent/test_grounding.py`:
```python
from app.agent.grounding import Claim, deterministic_ground, extract_claims


def test_extract_claims_finds_ids_numbers_urls() -> None:
    text = "Found 3 issues incl. PROJ-42 at https://x.co/a on 2026-07-04."
    kinds = {c.kind for c in extract_claims(text)}
    assert {"id", "number", "url", "date"} <= kinds


def test_deterministic_ground_flags_fabricated_id() -> None:
    claims = extract_claims("Ticket PROJ-999 was closed and 5 items updated.")
    tool_output = "results: PROJ-999 status=closed; updated 5 items"
    grounded, ungrounded = deterministic_ground(claims, tool_output)
    assert not ungrounded  # both PROJ-999 and 5 appear in tool output


def test_deterministic_ground_catches_hallucinated_count() -> None:
    claims = extract_claims("Closed ticket PROJ-999 and updated 12 items.")
    tool_output = "results: PROJ-999 status=closed; updated 5 items"
    grounded, ungrounded = deterministic_ground(claims, tool_output)
    assert any(c.value == "12" for c in ungrounded)
```
- [ ] Run: `uv run pytest tests/agent/test_grounding.py -v` → **FAIL** (module missing).
- [ ] Implement `extract_claims` (regexes: `\b[A-Z]{2,10}-\d+\b` id; `https?://\S+` url; `\b\d{4}-\d{2}-\d{2}\b` date; `"[^"]{2,80}"` quote; `\b\d[\d,]*\b` number — dedupe, and do not double-count a number that is part of an id/date span) and `deterministic_ground` (normalize whitespace + strip commas from numbers before substring test).
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(grounding): deterministic claim extraction and cross-check`.

### Task 6: Cheap-LLM grounding pass for residual claims

**Files:** `app/agent/grounding.py`, `app/agent/prompts.py` (add `GROUNDING_SYSTEM`). Test: `tests/agent/test_grounding.py`.

**Interfaces:**
```python
class GroundingChecker:
    def __init__(self, *, provider: Any | None = None, model: str = "", enabled: bool = True) -> None: ...

    async def check(self, *, answer: str, tool_output: str, tenant_id: str = "") -> GroundingResult:
        """1. Deterministic pre-pass. 2. Only if residual claims remain AND a
        provider is set, run one cheap-LLM pass over the residuals. Fail-closed:
        on provider error, residual claims stay UNGROUNDED (never auto-grounded)."""
```
`GROUNDING_SYSTEM` prompt: "You verify whether each listed claim is supported by the tool output. Return ONLY JSON: {\"grounded\": [\"claim\", ...], \"ungrounded\": [\"claim\", ...]}. A claim is grounded only if the tool output directly states or entails it. Never guess."

Steps:
- [ ] Failing test:
```python
import pytest
from app.agent.grounding import GroundingChecker
from app.providers.fake import FakeProvider


@pytest.mark.asyncio
async def test_llm_pass_only_runs_on_residuals() -> None:
    # Deterministic pass grounds PROJ-1; the free-text phrase needs the LLM pass.
    provider = FakeProvider(responses=['{"grounded": [], "ungrounded": ["the deploy succeeded"]}'])
    checker = GroundingChecker(provider=provider)
    res = await checker.check(
        answer='PROJ-1 done; the deploy succeeded.',
        tool_output="PROJ-1 status=done",
    )
    assert res.checked_by == "llm"
    assert any("deploy" in c.value for c in res.ungrounded)


@pytest.mark.asyncio
async def test_grounding_fails_closed_on_provider_error() -> None:
    class Boom:
        def supports_structured_output(self) -> bool: return False
        async def complete(self, req): raise RuntimeError("down")
    checker = GroundingChecker(provider=Boom())
    res = await checker.check(answer="revenue was $9,999,999", tool_output="no data")
    assert not res.is_grounded  # residual stayed ungrounded, did not auto-pass
```
- [ ] Run: `uv run pytest tests/agent/test_grounding.py -v` → **FAIL**.
- [ ] Implement `GroundingChecker.check`: run `deterministic_ground`; if no residual ungrounded → return `checked_by="deterministic"`; else if `provider` set, send `GROUNDING_SYSTEM` + the residual claim list + tool_output (use structured output when supported), merge results; wrap the provider call in `try/except` that logs and leaves residuals ungrounded (fail-closed). Add a `myapp_grounding_ungrounded_total` counter increment.
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(grounding): cheap-LLM grounding pass with fail-closed fallback`.

### Task 7: `UNGROUNDED` step status + AgentState provenance fields

**Files:** `app/agent/state.py`. Test: `tests/agent/test_grounding.py` (add) or `tests/agent/test_state.py`.

**Interfaces:**
```python
class StepStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNGROUNDED = "ungrounded"     # NEW

@dataclass
class AgentState:
    ...
    provenance: list[dict[str, Any]] = field(default_factory=list)       # NEW: claim→step/tool
    ungrounded_claims: list[dict[str, Any]] = field(default_factory=list) # NEW
    cited_answer: str = ""                                                # NEW (Track C)
```

Steps:
- [ ] Failing test `test_agent_state_has_grounding_fields`:
```python
from app.agent.state import AgentState, StepStatus
from app.tenancy.context import TenantContext


def test_agent_state_has_grounding_fields() -> None:
    s = AgentState(goal="g", tenant_ctx=TenantContext(tenant_id="t", api_key_id="k", plan="free"))
    assert s.provenance == []
    assert s.ungrounded_claims == []
    assert StepStatus.UNGROUNDED == "ungrounded"
```
- [ ] Run: → **FAIL** (`AttributeError`/enum member missing).
- [ ] Implement the enum member + three fields (all serializable — lists of dicts and str, safe for checkpointing).
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(state): UNGROUNDED status and provenance fields`.

### Task 8: Wire grounding into `_node_execute` per step

**Files:** `app/agent/graph.py` (`__init__` accept `grounding_checker`; `_execute_step` return path; the single-step and parallel-wave blocks circa 990–1096). Test: `tests/agent/test_grounding_in_graph.py`.

**Interfaces:**
```python
# AgentGraph.__init__ — new kwarg (bound from app.state.grounding_checker)
grounding_checker: Any | None = None,
# stored as self._grounding_checker
```
After a step's `output` is produced and before `step.status = COMPLETE`, when `self._grounding_checker is not None` and the step made ≥1 successful tool call: build `tool_output = "\n".join(tc.get("raw_output","") for tc in step.tool_calls)`, call `await self._grounding_checker.check(answer=output, tool_output=tool_output, tenant_id=tenant_ctx.tenant_id)`; if not grounded, set `step.status = StepStatus.UNGROUNDED`, append the ungrounded claims to `agent_state.ungrounded_claims`, and emit `{"type": "claims_ungrounded", "step": step_desc, "claims": [...]}`. Steps with no tool calls are skipped (nothing to ground against — the executor prompt already forbids fabrication without tool evidence).

Steps:
- [ ] Failing test `tests/agent/test_grounding_in_graph.py::test_ungrounded_step_is_flagged`:
```python
import pytest
from app.agent.grounding import GroundingChecker
from app.agent.graph import AgentGraph
from app.agent.state import StepStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import TenantContext


@pytest.mark.asyncio
async def test_ungrounded_step_is_flagged() -> None:
    planner = FakeProvider(responses=['{"steps": ["call jira to fetch"]}'])
    # executor "reports" a ticket ID + count that the tool output will NOT contain
    executor = FakeProvider(responses=['Fetched PROJ-777 with 42 open issues.'])
    verifier = FakeProvider(responses=['{"success": false, "reason": "ungrounded", "retry": true}'])
    g = AgentGraph(
        planner=planner, executor=executor, verifier=verifier,
        grounding_checker=GroundingChecker(provider=None),  # deterministic only
        max_iterations=1,
    )
    ctx = TenantContext(tenant_id="t1", api_key_id="k1", plan="professional")
    # Simulate a tool call whose raw output lacks PROJ-777/42 (wire via a stub tool
    # context per the existing test helper in tests/agent/conftest.py).
    state = await g.run(goal="fetch issues", tenant_ctx=ctx)
    assert any(s.status == StepStatus.UNGROUNDED for s in state.steps)
    assert state.ungrounded_claims
```
- [ ] Run: `uv run pytest tests/agent/test_grounding_in_graph.py -v` → **FAIL** (`grounding_checker` kwarg rejected / no UNGROUNDED status set). If the tool-call stubbing helper does not exist, extend `tests/agent/conftest.py` with a minimal fake tool context first (a fixture returning a step with `tool_calls=[{"tool_name":"jira","success":True,"raw_output":"no matching data"}]`).
- [ ] Implement the `__init__` kwarg + the grounding hook in both the single-step and parallel-wave completion paths. Fail-closed: if `check` raises, log and leave the step COMPLETE only if there are zero extracted claims; otherwise mark UNGROUNDED.
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(grounding): flag ungrounded executor steps in the agent loop`.

### Task 9: Route ungrounded steps into the verifier summary and force replan

**Files:** `app/agent/graph.py` (`_build_verifier_summary` circa 105–149; `_node_verify`). Test: `tests/agent/test_grounding_in_graph.py`.

**Interfaces:** `_build_verifier_summary(steps)` gains an `[UNGROUNDED CLAIM]` line for any step whose `status == StepStatus.UNGROUNDED`, listing the ungrounded claim values. The `VERIFIER_SYSTEM` prompt already fails on `[TOOL FAILED]`/`[STEP ERROR]`; add one rule: "CRITICAL: any step marked [UNGROUNDED CLAIM] means the agent fabricated an unverified value — the goal is NOT achieved."

Steps:
- [ ] Failing test `test_ungrounded_summary_line_present`:
```python
from app.agent.graph import _build_verifier_summary
from app.agent.state import StepResult, StepStatus


def test_ungrounded_summary_line_present() -> None:
    s = StepResult(description="fetch", status=StepStatus.UNGROUNDED, output="PROJ-777")
    s.tool_calls = [{"tool_name": "jira", "success": True}]
    summary = _build_verifier_summary([s])
    assert "[UNGROUNDED CLAIM]" in summary
```
- [ ] Run: → **FAIL** (no such line).
- [ ] Implement: extend `_step_line` in `_build_verifier_summary` and treat UNGROUNDED steps as "failed" for the "FAILED STEPS" section; add the verifier prompt rule. This makes the verifier return `success=false` → `_route` returns `replan` (or `waiting_human`/`max_iter` per existing logic).
- [ ] Run: → **PASS**. Add an end-to-end assertion in `test_grounding_in_graph.py` that a goal with an ungrounded step and `max_iterations=2` ends `FAILED`/replans rather than `COMPLETE`.
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `feat(grounding): ungrounded steps fail verification and trigger replan`.

---

## Track C — Citation-carrying final synthesis

### Task 10: Provenance capture during execution

**Files:** `app/agent/graph.py` (step completion paths). Test: `tests/agent/test_synthesis.py`.

**Interfaces:** on each successful step, append to `agent_state.provenance`:
```python
{"step_id": step.step_id, "description": step_desc,
 "tools": [tc.get("tool_name") for tc in step.tool_calls],
 "output_excerpt": (output or "")[:300]}
```

Steps:
- [ ] Failing test `test_provenance_recorded_per_step`:
```python
import pytest
from app.agent.graph import AgentGraph
from app.providers.fake import FakeProvider
from app.tenancy.context import TenantContext


@pytest.mark.asyncio
async def test_provenance_recorded_per_step() -> None:
    g = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["step one"]}']),
        executor=FakeProvider(responses=["did step one"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    )
    ctx = TenantContext(tenant_id="t1", api_key_id="k1", plan="professional")
    state = await g.run(goal="g", tenant_ctx=ctx)
    assert state.provenance
    assert state.provenance[0]["description"] == "step one"
```
- [ ] Run: → **FAIL** (`provenance` empty).
- [ ] Implement the append in both single-step and parallel-wave completion blocks.
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(synthesis): capture step provenance during execution`.

### Task 11: `AnswerSynthesizer` + `_node_synthesize`

**Files:** `app/agent/synthesis.py` (new), `app/agent/prompts.py` (add `SYNTHESIS_SYSTEM`), `app/agent/graph.py` (add node on the success path). Test: `tests/agent/test_synthesis.py`.

**Interfaces:**
```python
# app/agent/synthesis.py
@dataclass
class Citation:
    index: int          # [1], [2] ...
    step_id: str
    tools: list[str]
    excerpt: str


@dataclass
class CitedAnswer:
    text: str                       # final answer with inline [n] markers
    citations: list[Citation]


class AnswerSynthesizer:
    def __init__(self, *, provider: Any, model: str = "") -> None: ...

    async def synthesize(
        self, *, goal: str, provenance: list[dict], tenant_id: str = ""
    ) -> CitedAnswer:
        """Produce a final answer where every factual claim carries a [n] citation
        mapping to a provenance entry. Fail-closed: if the LLM omits citations for a
        claim that extract_claims() flags, that claim is dropped or marked
        [uncited] rather than presented as fact."""
```
`SYNTHESIS_SYSTEM`: "Write the final answer to the goal using ONLY the provided step results. Every sentence containing a specific value (ID, number, URL, date) MUST end with a citation marker [n] referencing the step number that produced it. Do not state any value not present in the step results."

Graph wiring: add node `synthesize` between `verify`(success) and `END`. In `_build`, route `"complete"` to `"synthesize"` and `synthesize → END`, but only when `self._answer_synthesizer is not None`; otherwise keep `"complete": END`. `_node_synthesize` calls the synthesizer with `agent_state.provenance`, stores `agent_state.cited_answer`, and emits `{"type": "final_answer", "text": ..., "citations": [...]}`.

Steps:
- [ ] Failing test `test_synthesizer_attaches_citations`:
```python
import pytest
from app.agent.synthesis import AnswerSynthesizer
from app.providers.fake import FakeProvider


@pytest.mark.asyncio
async def test_synthesizer_attaches_citations() -> None:
    prov = [{"step_id": "s1", "description": "fetch", "tools": ["jira"],
             "output_excerpt": "PROJ-1 is open"}]
    synth = AnswerSynthesizer(provider=FakeProvider(responses=["Ticket PROJ-1 is open [1]."]))
    ans = await synth.synthesize(goal="status?", provenance=prov)
    assert "[1]" in ans.text
    assert ans.citations[0].step_id == "s1"
```
- [ ] Run: `uv run pytest tests/agent/test_synthesis.py -v` → **FAIL** (module missing).
- [ ] Implement `synthesis.py`, the prompt, and the graph node + conditional edge. Add `answer_synthesizer` to `AgentGraph.__init__` (bound from `app.state`).
- [ ] Run: → **PASS**. Add `test_graph_populates_cited_answer` that runs a full goal and asserts `state.cited_answer` is non-empty.
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `feat(synthesis): citation-carrying final answer node`.

### Task 12: Expose citations in the goal-result API payload

**Files:** `app/api/goals.py` (the goal-result/`GET /goals/{id}` serializer and/or the SSE `final_answer` event). Test: `tests/api/test_goals.py` (extend nearest existing).

**Interfaces:** the goal result JSON gains `"cited_answer": str` and `"citations": [{"index", "step_id", "tools", "excerpt"}]` and `"grounding": {"ungrounded_claims": [...]}`. Never leak raw internal errors — keep the existing structured-error pattern.

Steps:
- [ ] Failing test asserting a completed goal's result payload contains `citations` and `cited_answer` keys (use the in-memory app fixture from `tests/api/conftest.py`, FakeProviders scripted to complete).
- [ ] Run: → **FAIL** (keys absent).
- [ ] Implement the serializer additions, reading from `AgentState.cited_answer` / `.provenance` / `.ungrounded_claims`.
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(api): surface citations and grounding status in goal results`.

### Task 13: Frontend citation rendering

**Files:** `agent-verse-frontend/src/features/goals/CitationList.tsx` (new), consuming component in the goal detail view; `CitationList.test.tsx`; `e2e/goal-citations.spec.ts`.

**Interfaces:**
```tsx
type Citation = { index: number; stepId: string; tools: string[]; excerpt: string };
export function CitationList({ citations }: { citations: Citation[] }): JSX.Element;
```
Render as a semantic `<ol>` with each `<li>` keyed by `index`; the answer body's `[n]` markers link (`<a href="#cite-n">`) to the list items. WCAG 2.2 AA: focusable links, visible focus ring, `aria-label` on each citation, empty state ("No sources cited") when `citations.length === 0`.

Steps:
- [ ] Failing vitest `CitationList.test.tsx`: renders 2 citations → asserts 2 `<li>` and that tools are shown; renders `[]` → asserts empty-state text.
- [ ] Run: `cd agent-verse-frontend && npm run test -- CitationList` → **FAIL** (component missing).
- [ ] Implement `CitationList.tsx` (extend `src/components/ui/`, Tailwind tokens, no new libs) and mount it in the goal detail view; wire the TanStack Query result field `citations`.
- [ ] Run: → **PASS**. Add `e2e/goal-citations.spec.ts`: submit a goal, wait for completion, assert the citation list is visible and axe-clean (`@axe-core/playwright`).
- [ ] Run: `npm run lint && npm run typecheck && npm run test -- CitationList` → clean; `npm run test:e2e -- goal-citations` → PASS (against a mocked/seeded backend). Commit `feat(goals-ui): render answer citations with a11y-compliant source list`.

---

## Track D — 3-way consensus verification for high-risk goals

### Task 14: Detect when consensus is required

**Files:** `app/agent/consensus.py` (new). Test: `tests/agent/test_consensus.py`.

**Interfaces:**
```python
# app/agent/consensus.py
def requires_consensus(
    *,
    steps: list[Any],                 # AgentState.steps
    policy_engine: Any | None = None,
    regulated: bool = False,          # from tenant/goal regulated-domain flag
) -> bool:
    """True when any executed tool classifies as write_high/destructive
    (via app.agent.tool_risk.classify_tool_risk on each tool_call), or the
    goal is flagged regulated."""
```

Steps:
- [ ] Failing test:
```python
from app.agent.consensus import requires_consensus
from app.agent.state import StepResult


def test_requires_consensus_on_destructive_tool() -> None:
    s = StepResult(description="delete it")
    s.tool_calls = [{"tool_name": "delete_issue", "server_name": "jira", "success": True}]
    assert requires_consensus(steps=[s]) is True


def test_no_consensus_on_read_only() -> None:
    s = StepResult(description="list")
    s.tool_calls = [{"tool_name": "search_issues", "server_name": "jira", "success": True}]
    assert requires_consensus(steps=[s], regulated=False) is False
```
- [ ] Run: `uv run pytest tests/agent/test_consensus.py -v` → **FAIL** (module missing).
- [ ] Implement `requires_consensus` using `classify_tool_risk(tc["tool_name"], tc.get("server_name",""))` — return True on `{"write_high","destructive"}` or `regulated`.
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(consensus): detect high-stakes goals requiring 3-way verification`.

### Task 15: `ConsensusVerifier` — 3 votes, majority

**Files:** `app/agent/consensus.py`, `app/agent/prompts.py` (add `JUDGE_RUBRIC_SYSTEM`). Test: `tests/agent/test_consensus.py`.

**Interfaces:**
```python
@dataclass
class VerifierVote:
    source: str          # "primary" | "cross_model" | "judge"
    success: bool
    reason: str = ""
    score: float = 0.0   # judge only (0.0–1.0)


@dataclass
class ConsensusResult:
    success: bool            # majority of the 3 votes
    votes: list[VerifierVote]
    agreement: float         # fraction agreeing with the majority (0.33/0.66/1.0)
    requires_hitl: bool      # True when votes are split (agreement < 1.0)


class ConsensusVerifier:
    def __init__(
        self,
        *,
        primary: LLMProvider,
        cross_model: LLMProvider,
        judge: Any,                       # intelligence.eval_suite.LLMJudge
        min_agreement_for_auto: float = 1.0,
    ) -> None: ...

    async def verify(
        self, *, goal: str, summary: str, actual_output: str,
        tools_called: list[str], forbidden_tools: list[str], tenant_id: str = "",
    ) -> ConsensusResult:
        """Run primary + cross_model (VERIFIER_SYSTEM, structured verdict) and
        judge.score() in parallel; majority决定 success; requires_hitl when split.
        Fail-closed: a provider error counts as a success=False vote."""
```
`JUDGE_RUBRIC_SYSTEM` is `LLMJudge`'s existing rubric (correctness/completeness/coherence/safety); the judge vote is `success = overall >= 0.7`.

Steps:
- [ ] Failing test:
```python
import pytest
from app.agent.consensus import ConsensusVerifier
from app.intelligence.eval_suite import LLMJudge
from app.providers.fake import FakeProvider


@pytest.mark.asyncio
async def test_split_vote_requires_hitl() -> None:
    primary = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    cross = FakeProvider(responses=['{"success": false, "reason": "no evidence", "retry": true}'])
    judge = LLMJudge(provider=FakeProvider(responses=['{"overall": 0.9, "safety": 1.0}']))
    cv = ConsensusVerifier(primary=primary, cross_model=cross, judge=judge)
    res = await cv.verify(goal="g", summary="s", actual_output="did x",
                          tools_called=["deploy"], forbidden_tools=[])
    assert res.agreement < 1.0
    assert res.requires_hitl is True
    assert len(res.votes) == 3


@pytest.mark.asyncio
async def test_unanimous_success_no_hitl() -> None:
    p = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    c = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    judge = LLMJudge(provider=FakeProvider(responses=['{"overall": 0.95, "safety": 1.0}']))
    cv = ConsensusVerifier(primary=p, cross_model=c, judge=judge)
    res = await cv.verify(goal="g", summary="s", actual_output="ok",
                          tools_called=["create_issue"], forbidden_tools=[])
    assert res.success is True
    assert res.requires_hitl is False
```
- [ ] Run: → **FAIL**.
- [ ] Implement `ConsensusVerifier.verify` — build the two verifier `CompletionRequest`s (with `response_schema=verifier_schema()` when supported, parse `VerifierVerdict`), call `judge.score(...)`, gather with `asyncio.gather(..., return_exceptions=True)`, convert exceptions to `success=False` votes (fail-closed), compute majority + agreement, set `requires_hitl = agreement < min_agreement_for_auto` when the majority is success (a split on a high-stakes goal must not auto-pass).
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(consensus): 3-way majority verifier with fail-closed voting`.

### Task 16: Wire consensus into `_node_verify` with HITL on disagreement

**Files:** `app/agent/graph.py` (`__init__` accept `consensus_verifier`; `_node_verify`). Test: `tests/agent/test_consensus_in_graph.py`.

**Interfaces:** `AgentGraph.__init__` gains `consensus_verifier: Any | None = None` (bound from `app.state`). In `_node_verify`, after building `summary`, if `self._consensus_verifier is not None and requires_consensus(steps=agent_state.steps, policy_engine=self._policy_engine, regulated=<goal flag>)`: run `consensus_verifier.verify(...)` instead of the single verifier call. If `result.requires_hitl` and `self._hitl_gateway is not None`: `request_approval(goal_id=..., action="verifier disagreement on high-stakes goal", risk_level="high", tenant_ctx=...)`, emit `waiting_approval`, and in supervised mode `await wait_for_approval(...)` — REJECTED/TIMED_OUT → `success=False, retry=False`; APPROVED → use the majority `success`. Otherwise use `result.success`. Record every vote to the calibration store (Track E) via a fire-and-forget task.

Steps:
- [ ] Failing test `test_consensus_disagreement_requests_hitl`:
```python
import pytest
from app.agent.consensus import ConsensusVerifier
from app.agent.graph import AgentGraph
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.intelligence.eval_suite import LLMJudge
from app.providers.fake import FakeProvider
from app.tenancy.context import TenantContext


@pytest.mark.asyncio
async def test_consensus_disagreement_requests_hitl() -> None:
    planner = FakeProvider(responses=['{"steps": ["delete the prod database"]}'])
    executor = FakeProvider(responses=["deleted"])
    primary = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    cross = FakeProvider(responses=['{"success": false, "reason": "no proof", "retry": true}'])
    judge = LLMJudge(provider=FakeProvider(responses=['{"overall": 0.9, "safety": 1.0}']))
    gateway = HITLGateway()
    g = AgentGraph(
        planner=planner, executor=executor, verifier=primary,
        consensus_verifier=ConsensusVerifier(primary=primary, cross_model=cross, judge=judge),
        hitl_gateway=gateway, autonomy_mode="bounded-autonomous", max_iterations=1,
    )
    ctx = TenantContext(tenant_id="t1", api_key_id="k1", plan="professional")
    await g.run(goal="delete the prod database", tenant_ctx=ctx)
    # a high-risk goal with a split verdict must have opened an approval request
    assert gateway.list_pending(tenant_ctx=ctx)
```
- [ ] Run: `uv run pytest tests/agent/test_consensus_in_graph.py -v` → **FAIL** (kwarg rejected / no approval opened).
- [ ] Implement the `__init__` kwarg + `_node_verify` branch. Keep the existing single-verifier path for non-high-stakes goals unchanged.
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(consensus): high-stakes goals verified by 3-way consensus with HITL on disagreement`.

---

## Track E — Verifier calibration loop

### Task 17: `verifier_calibration` table + ORM + migration + RLS

**Files:** `app/db/models/eval.py` (extend), `app/db/migrations/versions/0072_verifier_calibration.py` (new). Tests: `tests/intelligence/test_verifier_calibration.py` (schema), `tests/intelligence/test_verifier_calibration_rls.py` (integration).

**Interfaces (ORM):**
```python
class VerifierCalibration(Base):
    __tablename__ = "verifier_calibration"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verifier_source: Mapped[str] = mapped_column(String(32), nullable=False)  # primary/cross_model/judge/consensus
    predicted_success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actual_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # filled by outcome
    reason: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```
Migration mirrors `0046_workflows.py`: `CREATE TABLE IF NOT EXISTS`, index on `(tenant_id, goal_id)`, then `ENABLE`+`FORCE ROW LEVEL SECURITY` and a `verifier_calibration_tenant_isolation` policy `USING (tenant_id = current_setting('app.tenant_id', TRUE)) WITH CHECK (...)`. `revision = "0072"`, `down_revision = "0070"`.

Steps:
- [ ] Failing schema test `test_verifier_calibration_columns`:
```python
from app.db.models.eval import VerifierCalibration


def test_verifier_calibration_columns() -> None:
    cols = VerifierCalibration.__table__.columns
    assert "predicted_success" in cols and "actual_success" in cols
    assert cols["actual_success"].nullable is True
```
- [ ] Run: `uv run pytest tests/intelligence/test_verifier_calibration.py -v` → **FAIL** (model missing).
- [ ] Implement the ORM class + the migration file.
- [ ] Run schema test → **PASS**.
- [ ] Failing RLS integration test `tests/intelligence/test_verifier_calibration_rls.py` (marked `integration`, uses testcontainers per CLAUDE.md env vars): insert a row under tenant A's `app.tenant_id` GUC, then query under tenant B's GUC → assert 0 rows.
- [ ] Run: `DOCKER_HOST=unix:///Users/harsh.kumar01/.colima/default/docker.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/intelligence/test_verifier_calibration_rls.py -m integration -v` → **FAIL** then, after `uv run alembic upgrade head` applies 0072 in the test setup, → **PASS**.
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `feat(calibration): verifier_calibration table with tenant RLS`.

### Task 18: `VerifierCalibrationStore` — record predictions + outcomes + false-confirm report

**Files:** `app/intelligence/verifier_calibration.py` (new). Test: `tests/intelligence/test_verifier_calibration.py`.

**Interfaces:**
```python
class VerifierCalibrationStore:
    def __init__(self, db_session_factory: Any | None = None) -> None: ...

    async def record_prediction(
        self, *, goal_id: str, tenant_id: str, source: str,
        predicted_success: bool, reason: str = "", db: Any = None,
    ) -> str: ...

    async def record_outcome(
        self, *, goal_id: str, tenant_id: str, actual_success: bool, db: Any = None,
    ) -> int:  # rows updated
        ...

    async def false_confirm_rate(
        self, *, tenant_id: str, db: Any = None, since_days: int = 30,
    ) -> dict[str, float]:
        """{"false_confirm_rate": p(actual=False | predicted=True),
            "sample_size": n, "resolved": m}. Returns rate=0.0 with sample_size=0
            when there is no resolved data (never divide by zero)."""
```
"False confirm" = verifier predicted success but the eventual outcome was failure — the KPI to keep < 2%.

Steps:
- [ ] Failing unit test using a fake in-memory `db` (list-backed) or the real store with a stubbed session factory:
```python
import pytest
from app.intelligence.verifier_calibration import VerifierCalibrationStore


@pytest.mark.asyncio
async def test_false_confirm_rate_zero_when_no_data() -> None:
    store = VerifierCalibrationStore(db_session_factory=None)
    rate = await store.false_confirm_rate(tenant_id="t1")
    assert rate["sample_size"] == 0
    assert rate["false_confirm_rate"] == 0.0
```
- [ ] Run: `uv run pytest tests/intelligence/test_verifier_calibration.py -v` → **FAIL** (module missing).
- [ ] Implement the store. `record_prediction` INSERTs; `record_outcome` UPDATEs matching pending rows setting `actual_success` + `resolved_at`; `false_confirm_rate` runs an aggregate `SELECT`. All methods no-op safely (log a warning) when `db is None` — do NOT raise into the agent loop.
- [ ] Add an integration test (marked `integration`) that records a prediction, records an outcome, and asserts the rate computes correctly.
- [ ] Run unit → **PASS**; integration → **PASS** (testcontainers).
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `feat(calibration): calibration store with false-confirm-rate report`.

### Task 19: Wire calibration recording into verify + goal-outcome paths

**Files:** `app/agent/graph.py` (`__init__` accept `calibration_store`; `_node_verify` records predictions; the success/terminal path records outcomes). Test: `tests/agent/test_consensus_in_graph.py` (extend).

**Interfaces:** `AgentGraph.__init__` gains `calibration_store: Any | None = None`. In `_node_verify`, after computing the verdict (single or consensus), fire-and-forget `calibration_store.record_prediction(goal_id, tenant_id, source, predicted_success=success, reason=...)` for each vote/source (use the existing `self._background_tasks` add/discard pattern so tasks aren't GC'd). When a goal reaches a terminal `COMPLETE`/`FAILED` where the true outcome is known (e.g. eval scorecard present, or `retry=False` failure), record the outcome. Fail-closed: recording errors are logged, never raised.

Steps:
- [ ] Failing test `test_verify_records_calibration_prediction` using a spy store (records calls in a list) asserting `record_prediction` was called with `predicted_success` matching the verdict.
- [ ] Run: → **FAIL** (kwarg rejected / no call).
- [ ] Implement the `__init__` kwarg + the two recording hooks.
- [ ] Run: → **PASS**. `uv run ruff check . && uv run mypy app` → clean. Commit `feat(calibration): record verifier predictions and goal outcomes from the agent loop`.

---

## Track F — Golden-set regression gate on prompt changes

### Task 20: Hallucination golden fixtures + `HallucinationGate` metrics

**Files:** `tests/fixtures/hallucination_golden.json` (new), `tests/intelligence/test_hallucination_golden_gate.py` (new). No production code beyond a small metrics helper in `app/intelligence/verifier_calibration.py` or a new `app/agent/grounding.py` helper (reuse `extract_claims`/`deterministic_ground`).

**Interfaces (fixture shape):**
```json
[
  {
    "id": "gt-jira-count",
    "goal": "How many open issues are assigned to me?",
    "tool_output": "issues: PROJ-1, PROJ-2, PROJ-3 (3 total)",
    "agent_answer": "You have 3 open issues: PROJ-1, PROJ-2, PROJ-3.",
    "expect_grounded": true
  },
  {
    "id": "gt-fabricated-id",
    "goal": "What is the status of my ticket?",
    "tool_output": "no matching tickets found",
    "agent_answer": "Ticket PROJ-8891 is In Progress.",
    "expect_grounded": false
  }
]
```
The gate metric: run `deterministic_ground(extract_claims(agent_answer), tool_output)` for each fixture; `ungrounded_rate = (# fixtures where is_grounded != expect_grounded) / total`. Gate passes when `ungrounded_rate < 0.01` (KPI) — with a small hand-curated set this means **zero misclassifications**.

Steps:
- [ ] Write `tests/fixtures/hallucination_golden.json` with ≥20 cases (mix of grounded truths and fabricated IDs/counts/dates/URLs — cover each `Claim.kind`).
- [ ] Write failing test `test_grounding_golden_gate`:
```python
import json
import pathlib
from app.agent.grounding import deterministic_ground, extract_claims

FIX = pathlib.Path(__file__).parent.parent / "fixtures" / "hallucination_golden.json"


def test_grounding_golden_gate() -> None:
    cases = json.loads(FIX.read_text())
    misses = 0
    for c in cases:
        _, ungrounded = deterministic_ground(extract_claims(c["agent_answer"]), c["tool_output"])
        is_grounded = not ungrounded
        if is_grounded != c["expect_grounded"]:
            misses += 1
    rate = misses / len(cases)
    assert rate < 0.01, f"ungrounded-classification miss rate {rate:.2%} exceeds 1% gate"
```
- [ ] Run: `uv run pytest tests/intelligence/test_hallucination_golden_gate.py -v` → initially **FAIL** if the deterministic matcher misclassifies any fixture (this is the point — tune `extract_claims`/`deterministic_ground` number/date normalization until the curated set passes at <1%).
- [ ] Fix the matcher (Task 5 code) as needed; re-run → **PASS**.
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `test(hallucination): golden-set grounding gate at <1% miss rate`.

### Task 21: Verifier false-confirm golden gate + CI wiring for prompt changes

**Files:** `tests/intelligence/test_hallucination_golden_gate.py` (extend), `agent-verse-backend/.github/workflows/ci.yml` (add a path-filtered job). Test: the same file.

**Interfaces:** a second gate that runs the verifier (FakeProvider scripted verdicts + `_parse_verifier_response`/`VerifierVerdict`) over labeled `(summary, should_pass)` fixtures and asserts the false-confirm rate < 2%. CI: add a job `hallucination-gate` that triggers `on: pull_request: paths: ["agent-verse-backend/app/agent/prompts.py", "agent-verse-backend/app/agent/grounding.py", "agent-verse-backend/app/agent/consensus.py", "agent-verse-backend/app/agent/synthesis.py"]` and runs `uv run pytest tests/intelligence/test_hallucination_golden_gate.py -v`.

Steps:
- [ ] Add ≥15 verifier fixtures to the JSON (fields `summary`, `should_pass`) covering `[TOOL FAILED]`, `[UNGROUNDED CLAIM]`, "Found 0 issues", and genuine successes.
- [ ] Failing test `test_verifier_false_confirm_gate` that scripts a `FakeProvider` verdict per fixture, computes `false_confirm = predicted_pass and not should_pass`, asserts `rate < 0.02`.
- [ ] Run: `uv run pytest tests/intelligence/test_hallucination_golden_gate.py::test_verifier_false_confirm_gate -v` → **FAIL** until fixtures/labels are consistent → **PASS**.
- [ ] Add the CI job (path-filtered). Verify locally: `uv run pytest tests/intelligence/test_hallucination_golden_gate.py -v` all green.
- [ ] `uv run ruff check . && uv run mypy app` → clean. Commit `ci(hallucination): gate prompt/grounding/consensus changes on the golden suite`.

---

## Self-Review

Before declaring Phase 3 complete, run and confirm (evidence before assertions, per superpowers:verification-before-completion):

- [ ] `cd agent-verse-backend && uv run pytest -q` — full suite green (no `filterwarnings=error` failures). New tests: `tests/providers/test_structured_output.py`, `tests/agent/test_schemas.py`, `test_grounding.py`, `test_grounding_in_graph.py`, `test_structured_nodes.py`, `test_consensus.py`, `test_consensus_in_graph.py`, `test_synthesis.py`, `tests/intelligence/test_verifier_calibration*.py`, `tests/intelligence/test_hallucination_golden_gate.py`.
- [ ] `uv run pytest -m integration -q` (with `DOCKER_HOST`/`TESTCONTAINERS_RYUK_DISABLED` set, `colima start` first) — RLS isolation + calibration integration green.
- [ ] `uv run ruff check . && uv run mypy app` — clean (strict).
- [ ] `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` — 0072 round-trips.
- [ ] `cd agent-verse-frontend && npm run lint && npm run typecheck && npm run test && npm run test:e2e -- goal-citations` — green + axe-clean.
- [ ] **Wiring integrity:** grep confirms `grounding_checker=`, `consensus_verifier=`, `answer_synthesizer=`, `calibration_store=` are passed at **both** `AgentGraph` construction sites (`app/services/goal_service.py:764`, `app/scaling/tasks.py:785`) and constructed in `create_app()` / lifespan — otherwise the whole track is inert (the exact failure class Phase 0 Task 0.1 fixed). Add these assertions to `tests/test_wiring_integrity.py`.
- [ ] **Fail-closed audit:** every `except` around a grounding/consensus/calibration correctness signal logs and defaults to the *unsafe-input* verdict (UNGROUNDED / not-verified / require-HITL), never auto-pass. Grep for `except Exception:\n            pass` added by this phase → none.
- [ ] **Structured-output fallback:** a provider without `supports_structured_output()` (e.g. an old `FakeProvider` in an existing test) still completes goals via the text-parse fallback — confirm the pre-Phase-3 agent tests are unmodified and green.
- [ ] **KPIs measured:** `test_hallucination_golden_gate.py` asserts ungrounded miss-rate < 1% and verifier false-confirm rate < 2%; zero fabricated entity IDs pass grounding (the `gt-fabricated-id` fixture is caught).
- [ ] **Idempotent redeploy:** re-running the golden gate after any prompt edit reproduces the same pass/fail (deterministic FakeProviders, no real-LLM calls in the gate).
- [ ] Confirm no task left a `# TODO`/placeholder; each commit message follows conventional-commits and ends with the `Co-Authored-By` trailer.
