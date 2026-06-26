# AgentVerse World-Class Completion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 36 gaps (6 critical + 18 quality + 12 world-class) with full E2E test coverage making AgentVerse production-grade.

**Architecture:** Three phases executed in parallel batches per phase. Every item ships with unit tests, integration tests where applicable, and E2E tests. No placeholders.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL, Redis, LangGraph, Celery, React 19, Zustand, TanStack Query, Tailwind, Playwright, Vitest.

---

## PHASE 1 — CRITICAL FIXES

### C-1: Fix SSE Authentication (Browser EventSource)

**Files:**
- Modify: `app/tenancy/middleware.py` — add `?api_key=` query param extraction
- Modify: `agent-verse-frontend/src/lib/sse/useGoalStream.ts` — switch from native EventSource to fetch-based SSE
- Test: `tests/tenancy/test_middleware.py` — add query-param auth test
- Test: `src/lib/sse/useGoalStream.test.ts` — new frontend test

**Fix middleware** — add query param extraction:
```python
def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    header_key = request.headers.get("X-API-Key")
    if header_key:
        return header_key
    # SSE: browsers cannot set headers on EventSource — accept query param
    return request.query_params.get("api_key") or None
```

**Fix frontend SSE hook** — replace native EventSource with fetch-based reader:
```typescript
export function useGoalStream(goalId: string | null, opts?: UseGoalStreamOptions) {
  const [events, setEvents] = useState<GoalEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const onEventRef = useRef(opts?.onEvent);
  onEventRef.current = opts?.onEvent;

  useEffect(() => {
    if (!goalId) return;
    const apiKey = localStorage.getItem("av_api_key") ?? "";
    const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
    const url = `${API_BASE_URL}/goals/${goalId}/stream`;
    const abort = new AbortController();
    abortRef.current = abort;

    (async () => {
      try {
        const res = await fetch(url, {
          headers: { "X-API-Key": apiKey },
          signal: abort.signal,
        });
        if (!res.ok || !res.body) { setConnected(false); return; }
        setConnected(true);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\n\n");
          buffer = parts.pop() ?? "";
          for (const part of parts) {
            const line = part.replace(/^data: /, "").trim();
            if (!line) continue;
            try {
              const parsed = JSON.parse(line) as GoalEvent;
              setEvents(prev => [...prev, parsed]);
              onEventRef.current?.(parsed);
            } catch { /* ignore malformed */ }
          }
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") setConnected(false);
      } finally {
        setConnected(false);
      }
    })();

    return () => { abort.abort(); setConnected(false); };
  }, [goalId]);

  return { events, connected };
}
```

---

### C-2: FakeProvider Production Guard

**Files:**
- Modify: `app/scaling/tasks.py` — raise ConfigurationError in production mode
- Modify: `app/main.py` — warn/error on startup if no real LLM key in production
- Test: `tests/scaling/test_celery_app.py`

In `tasks.py` `run_goal()`, after building provider:
```python
import os
if provider.__class__.__name__ == "FakeProvider":
    env = os.getenv("ENVIRONMENT", "development")
    if env == "production":
        raise RuntimeError(
            "FakeProvider cannot be used in production. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY."
        )
```

---

### C-3: Enforce Plan Limits

**Files:**
- Modify: `app/services/goal_service.py` — check `goals_per_day` before submitting
- Modify: `app/api/agents.py` — check `max_agents` before creating
- Modify: `app/api/tenants.py` — check `max_api_keys` before creating
- Modify: `app/api/knowledge.py` — check `max_knowledge_collections` before creating
- Add: `app/tenancy/limits.py` — shared limit checker helpers
- Test: `tests/tenancy/test_limits.py`

```python
# app/tenancy/limits.py
from app.core.errors import PlatformError
from app.tenancy.context import PLAN_LIMITS, TenantContext

class PlanLimitExceededError(PlatformError):
    http_status = 429
    code = "PLAN_LIMIT_EXCEEDED"

def check_goal_limit(tenant_ctx: TenantContext, current_daily_count: int) -> None:
    limit = PLAN_LIMITS[tenant_ctx.plan].goals_per_day
    if current_daily_count >= limit:
        raise PlanLimitExceededError(
            f"Daily goal limit ({limit}) reached for plan {tenant_ctx.plan}"
        )

def check_agent_limit(tenant_ctx: TenantContext, current_count: int) -> None:
    limit = PLAN_LIMITS[tenant_ctx.plan].max_agents
    if current_count >= limit:
        raise PlanLimitExceededError(
            f"Agent limit ({limit}) reached for plan {tenant_ctx.plan}"
        )
```

In `GoalService.submit_goal()`, count today's goals and call `check_goal_limit()`.

---

### C-4: Webhook Trigger Actually Fires Goal

**Files:**
- Modify: `app/api/schedules.py` — fire goal from webhook
- Test: `tests/api/test_schedules_api.py`

```python
@webhooks_router.post("/{token}")
async def webhook_trigger(request: Request, token: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    token_map = _token_map(request)
    schedule_id = token_map.get(token)
    if schedule_id is None:
        raise HTTPException(status_code=404, detail="Unknown webhook token")

    store = _store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant)
    if rec is None:
        raise HTTPException(status_code=404, detail="Schedule not found")

    # Actually fire the goal
    goal_svc = request.app.state.goal_service
    goal_template = rec.get("goal_template", rec.get("goal", "Execute scheduled task"))
    agent_id = rec.get("agent_id")

    result = await goal_svc.submit_goal(
        goal=goal_template,
        priority="normal",
        dry_run=False,
        tenant_ctx=tenant,
        agent_id=agent_id,
    )
    return {
        "status": "fired",
        "schedule_id": schedule_id,
        "goal_id": result["goal_id"],
    }
```

---

### C-5: Regenerate OpenAPI Spec in CI

**Files:**
- Modify: `scripts/export_openapi.py` — ensure it exports current schema
- Modify: `.github/workflows/ci.yml` — export on every push
- Update: `openapi.json` — regenerate now

```bash
cd agent-verse-backend
uv run python scripts/export_openapi.py
```

---

### C-6: Persistence Startup Guard

**Files:**
- Modify: `app/main.py` — log structured warning when DB/Redis not reachable in production
- Modify: `app/core/config.py` — add `require_persistence: bool` setting
- Test: `tests/core/test_config.py`

In `create_app()` lifespan, after pools startup attempt:
```python
env = settings.environment
if env == "production" and not manage_pools:
    logger.warning(
        "Running in production with in-memory stores only. "
        "State will be lost on restart. Set DATABASE_URL and REDIS_URL."
    )
```

---

## PHASE 2 — QUALITY GAPS

### Q-1: Real Rollback Inverse Functions

**Files:**
- Modify: `app/agent/loop.py` — wire per-tool inverse from tool call result
- Modify: `app/agent/graph.py` — same
- Add: `app/reliability/tool_inverses.py` — inverse registry
- Test: `tests/reliability/test_rollback_enhanced.py`

```python
# app/reliability/tool_inverses.py
from typing import Any, Callable

_INVERSES: dict[str, Callable[[dict[str, Any]], None]] = {}

def register_inverse(tool_name: str, fn: Callable[[dict[str, Any]], None]) -> None:
    _INVERSES[tool_name] = fn

def get_inverse(tool_name: str, args: dict[str, Any]) -> Callable[[], None]:
    fn = _INVERSES.get(tool_name)
    if fn is None:
        return lambda: None
    return lambda: fn(args)
```

In `_execute_step()`, replace `lambda: None` with `get_inverse(tool_name, arguments)`.

---

### Q-2: EvalRunner Safety Score from Real Events

**Files:**
- Modify: `app/intelligence/eval_runner.py`
- Test: `tests/intelligence/test_eval_runner.py`

```python
# Count DENY events in agent state
deny_count = sum(
    1 for e in (state.events or [])
    if isinstance(e, dict) and e.get("action_level") == "DENY"
)
safety = max(0.0, 1.0 - (deny_count * 0.25))
```

---

### Q-3: AgentLoop Guardrail Output Check

**Files:**
- Modify: `app/agent/loop.py`
- Test: `tests/agent/test_agent_loop.py`

After executor response in `_run_step()`:
```python
if self._guardrail_checker is not None:
    issues = self._guardrail_checker.check_output(output_text)
    if issues:
        output_text = "[REDACTED: policy violation]"
```

---

### Q-4: Fix check_mcp_health Task

**Files:**
- Modify: `app/scaling/tasks.py` — actually query MCPRegistry
- Test: `tests/scaling/test_celery_app.py`

```python
async def _check_servers() -> list[dict[str, Any]]:
    from app.mcp.registry import MCPRegistry
    results = []
    try:
        registry_data = await MCPRegistry.get_all_servers_from_redis()
        for server in registry_data:
            results.append({"server_id": server["id"], "status": "checked"})
    except Exception as exc:
        results.append({"error": str(exc)})
    return results
```

---

### Q-5: Redis-Backed CostController

**Files:**
- Modify: `app/governance/cost.py` — use Redis for cross-replica daily totals
- Test: `tests/governance/test_cost_daily_reset.py`

Use Redis `INCRBYFLOAT` with a key expiring at end of UTC day.

---

### Q-6: Goal Crash Recovery on Startup

**Files:**
- Modify: `app/services/goal_service.py` — add `recover_executing_goals()` method
- Modify: `app/main.py` — call it in lifespan after sync_from_db
- Test: `tests/services/test_goal_service.py`

```python
async def recover_executing_goals(self) -> int:
    """Requeue goals stuck in 'executing' from before this process started."""
    recovered = 0
    for record in list(self._goals.values()):
        if record.status == GoalStatus.EXECUTING and record.task is None:
            # Resubmit as fresh goal execution
            record.status = GoalStatus.PLANNING
            asyncio.create_task(self._run_agent_loop(...))
            recovered += 1
    return recovered
```

---

### Q-7: Migrate 57 Modules from stdlib logging to structlog

**Files:** All Python files using `import logging` in `app/`
Replace `logging.getLogger(__name__)` → `from app.observability.logging import get_logger; logger = get_logger(__name__)`.

---

### Q-8: Tenant-Scoped DB-Backed Policy Registry

**Files:**
- Modify: `app/api/governance.py` — key by `tenant_id`, persist in DB
- Add: `app/db/models/governance.py` — `PolicyRecord` model if not exists
- Test: `tests/api/test_governance_api.py`

---

### Q-9: Input Length Validation

**Files:**
- Modify: `app/api/goals.py` — `goal: str = Field(..., min_length=1, max_length=10_000)`
- Modify: `app/api/agents.py` — goal_template max_length
- Modify: `app/api/knowledge.py` — content max_length
- Test: existing API tests

---

### Q-10: RPA Full Playwright Implementation

**Files:**
- Modify: `app/rpa/executor.py` — implement click/type/extract with real Playwright + session reuse
- Test: `tests/rpa/test_rpa_tools.py`

```python
async def _execute_with_playwright(self, *, tool_name, arguments, session_id, tenant_id):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        if tool_name == "rpa_click":
            url = arguments.get("url", "")
            if url: await page.goto(url)
            sel = arguments.get("selector") or arguments.get("text", "")
            if sel:
                if arguments.get("text"):
                    await page.get_by_text(sel).click()
                else:
                    await page.click(sel)
            return RPAResult(success=True, output=f"Clicked {sel}")
        elif tool_name == "rpa_type":
            await page.fill(arguments["selector"], arguments["text"])
            return RPAResult(success=True, output=f"Typed into {arguments['selector']}")
        elif tool_name == "rpa_extract_text":
            text = await page.inner_text(arguments.get("selector", "body"))
            return RPAResult(success=True, output=text[:5000])
        await browser.close()
```

---

### Q-11 through Q-18: Frontend and Misc Fixes

**Q-11: Dark mode persistence** — `stores/ui.ts`: read from/write to `localStorage.setItem("av-theme", next)`

**Q-12: ErrorBoundary** — wrap every `<Route>` in `App.tsx` with `<ErrorBoundary>`

**Q-13: Centralize API_BASE** — export `const API_BASE` from `src/lib/api/client.ts`, import in all 14 feature files

**Q-14: VITE_GRAFANA_URL** — `const GRAFANA_URL = import.meta.env.VITE_GRAFANA_URL ?? "http://localhost:3001"`

**Q-15: Provider capability guards** — check `isinstance(provider, LLMProvider)` and capability before dispatch

**Q-16: WS tenant-scoped connections** — key `_ws_connections` by `(session_id, tenant_id)` tuple

**Q-17: OTel spans for perception + RPA** — add `@tracer.start_as_current_span` decorators

**Q-18: agent_id on CostLedger** — DB migration + model update

---

## PHASE 3 — WORLD-CLASS FEATURES

### W-1: LLM Token Streaming

**Files:**
- Modify: `app/providers/base.py` — add `stream_complete()` to protocol
- Modify: `app/providers/anthropic_provider.py` — implement with `stream=True`
- Modify: `app/providers/openai_compatible.py` — implement with `stream=True`
- Add: `app/api/goals.py` — `GET /goals/{id}/stream/tokens` SSE endpoint
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx` — show token stream
- Test: `tests/providers/test_providers.py`

```python
# base.py addition
async def stream_complete(self, request: CompletionRequest) -> AsyncGenerator[str, None]: ...

# anthropic_provider.py
async def stream_complete(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
    async with self._client.messages.stream(
        model=request.model or self._model,
        messages=[...],
        max_tokens=request.max_tokens,
    ) as stream:
        async for text in stream.text_stream:
            yield text
```

### W-2: Multi-Model Routing

**Files:**
- Add: `app/agent/model_router.py` — route task type to optimal model
- Modify: `app/services/goal_service.py` — use model router
- Test: `tests/agent/test_model_router.py`

```python
class ModelRouter:
    TASK_MODELS = {
        "planning": "claude-opus-4-8",
        "execution": "claude-sonnet-4-5",
        "verification": "claude-haiku-3-5",
        "embedding": "voyage-3",
        "classification": "gpt-4o-mini",
    }
    def route(self, task_type: str, provider_name: str) -> str:
        return self.TASK_MODELS.get(task_type, "claude-opus-4-8")
```

### W-3: Agent Versioning + Rollback

**Files:**
- Modify: `app/db/models/agent.py` — add `version: int`, `is_archived: bool`
- Add: `app/db/migrations/versions/0013_agent_versioning.py`
- Modify: `app/api/agents.py` — `GET /agents/{id}/versions`, `POST /agents/{id}/rollback`
- Test: `tests/api/test_agents_api.py`

### W-4: Goal Retry with Exponential Backoff

**Files:**
- Modify: `app/services/goal_service.py` — `retry_goal()` method
- Modify: `app/api/goals.py` — `POST /goals/{id}/retry`
- Modify: frontend `GoalDetailPage.tsx` — show retry button on failed goals
- Test: `tests/services/test_goal_service.py`

### W-5: Goal Dependency DAG API + Frontend

**Files:**
- Modify: `app/api/goals.py` — `GET /goals/{id}/subtree`
- Modify: frontend `GoalDetailPage.tsx` — render dependency tree
- Test: `tests/api/test_goals.py`

### W-6: Agent Performance Benchmarking

**Files:**
- Add: `app/intelligence/benchmarking.py` — aggregate scorecard trends
- Modify: `app/api/enterprise.py` — `GET /intelligence/benchmarks`
- Modify: frontend `EvalPage.tsx` — trend charts
- Test: `tests/intelligence/test_benchmarking.py`

### W-7: Webhook Delivery Guarantees

**Files:**
- Add: `app/services/webhook_service.py` — outbound webhook with retry + DLQ
- Modify: `app/scaling/tasks.py` — `deliver_webhook` Celery task
- Modify: `app/api/schedules.py` — register outbound webhook on goal complete
- Test: `tests/services/test_webhook_service.py`

### W-8: Marketplace Publish Flow

**Files:**
- Modify: `app/enterprise/marketplace.py` — `publish()` stores to DB not memory
- Modify: `app/api/enterprise.py` — `POST /marketplace/publish`
- Modify: frontend `MarketplacePage.tsx` — Publish My Agent button
- Test: `tests/enterprise/test_enterprise_api.py`

### W-9: Prompt Injection Hardening

**Files:**
- Modify: `app/intelligence/guardrails.py` — add fuzzy matching, encoding bypass detection
- Test: `tests/intelligence/test_guardrails.py`

```python
import re, base64, codecs

def _detect_encoded_injection(text: str) -> list[str]:
    issues = []
    # Try base64 decode
    try:
        decoded = base64.b64decode(text + "==").decode("utf-8", errors="ignore")
        if any(p in decoded.lower() for p in _INJECTION_PHRASES):
            issues.append("base64-encoded injection detected")
    except Exception:
        pass
    # ROT13
    rot13 = codecs.encode(text.lower(), "rot_13")
    if any(p in rot13 for p in _INJECTION_PHRASES):
        issues.append("rot13-encoded injection detected")
    return issues
```

### W-10: Real-Time Presence in Collaboration

**Files:**
- Modify: `app/api/collab.py` — broadcast `presence` events on WS connect/disconnect
- Modify: frontend `CollaborationPage.tsx` — show online users
- Test: `tests/api/test_collab.py`

### W-11: Agent Export (OpenAI Assistants format)

**Files:**
- Add: `app/api/agents.py` — `GET /agents/{id}/export?format=openai|anthropic`
- Test: `tests/api/test_agents_api.py`

```python
@router.get("/{agent_id}/export")
async def export_agent(request, agent_id: str, format: str = "openai"):
    agent = ...
    if format == "openai":
        return {
            "name": agent["name"],
            "instructions": agent.get("system_prompt", ""),
            "tools": [],
            "model": agent.get("default_model", "gpt-4o"),
        }
    elif format == "anthropic":
        return {"system": agent.get("system_prompt", ""), "model": agent.get("default_model")}
```

---

## Test Coverage Requirements

Every item above ships with:
1. **Unit test** — isolated, no DB/Redis, covers happy path + error path
2. **API test** — httpx AsyncClient, auth guard test, 404 test, valid input test
3. **Frontend test** — Vitest for new UI behavior, Playwright for user flows

Commit after each phase completes.
