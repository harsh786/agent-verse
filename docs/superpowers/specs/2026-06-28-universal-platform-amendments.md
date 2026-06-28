# Universal Platform Amendments — Critical Cross-Spec Fixes

This document captures the 5 universal gaps that apply to ALL 10 world-class specs. Every engineer implementing any of the 10 specs MUST apply these before starting.

---

## U1. prefers-reduced-motion — Apply Once, Covers All Specs

Add a single rule to `agent-verse-frontend/src/index.css` that covers every animation across all 10 specs:

```css
/* Accessibility: respect user's motion preference (WCAG 2.1 Level AA — 2.3.3) */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

This single addition covers: all agent credential animations (Spec 1), role-grant animations (Spec 2), guardrail risk gauge (Spec 3), SLA countdown (Spec 4), audit chain verification (Spec 5), cost ticker (Spec 6), marketplace card hover (Spec 7), compliance badge flip (Spec 8), experiment timeline (Spec 9), knowledge graph nodes (Spec 10).

---

## U2. App.tsx Route Registration — All New Pages

Add all new pages from all 10 specs to `src/app/App.tsx`. Currently these routes are NOT registered:

```typescript
// Add these lazy imports at the top of App.tsx (after existing lazy imports):
const AgentIdentityPage     = lazy(() => import("@/features/agents/AgentIdentityPage").then(m => ({ default: m.AgentIdentityPage })));
const ScopeExplorerPage     = lazy(() => import("@/features/settings/ScopeExplorerPage").then(m => ({ default: m.ScopeExplorerPage })));
const GuardrailCenterPage   = lazy(() => import("@/features/settings/GuardrailCenterPage").then(m => ({ default: m.GuardrailCenterPage })));
const SelfImprovementPage   = lazy(() => import("@/features/analytics/SelfImprovementPage").then(m => ({ default: m.SelfImprovementPage })));
const SAMLCallbackPage      = lazy(() => import("@/features/auth/SAMLCallbackPage").then(m => ({ default: m.SAMLCallbackPage })));
const BudgetManagerPage     = lazy(() => import("@/features/settings/BudgetManagerPage").then(m => ({ default: m.BudgetManagerPage })));

// Add these routes inside the RequireAuth / AppLayout group:
<Route path="agents/:agentId/identity"     element={<Suspense fallback={<LoadingSpinner />}><AgentIdentityPage /></Suspense>} />
<Route path="settings/scopes"             element={<Suspense fallback={<LoadingSpinner />}><ScopeExplorerPage /></Suspense>} />
<Route path="settings/guardrails"         element={<Suspense fallback={<LoadingSpinner />}><GuardrailCenterPage /></Suspense>} />
<Route path="settings/budgets"            element={<Suspense fallback={<LoadingSpinner />}><BudgetManagerPage /></Suspense>} />
<Route path="self-improvement"            element={<Suspense fallback={<LoadingSpinner />}><SelfImprovementPage /></Suspense>} />

// Public route (outside RequireAuth — before the RequireAuth wrapper):
<Route path="/auth/saml/callback" element={<Suspense fallback={null}><SAMLCallbackPage /></Suspense>} />
```

---

## U3. Sidebar Registration — All New Pages

Add all new navigable pages to `src/components/ui/Sidebar.tsx` NAV_SECTIONS:

```typescript
// In the "Governance" section, add:
{ to: "/settings/guardrails", icon: Shield,    label: "Guardrails" },
{ to: "/settings/scopes",     icon: KeyRound,  label: "Scope Explorer" },

// In the "Enterprise" section, add:
{ to: "/self-improvement",    icon: TrendingUp, label: "Self-Improvement" },

// In the "Tooling" section, add:
{ to: "/settings/budgets",    icon: DollarSign, label: "Budget Manager" },

// Note: Agent Identity is accessible via AgentDetailPage → "Identity" button
// (not a direct sidebar link — too granular for top-level nav)
```

---

## U4. Celery Task Registration — All New Background Jobs

Add ALL new Celery tasks from all 10 specs to `app/scaling/celery_app.py` beat_schedule:

```python
# In celery_app.py, add to beat_schedule dict:
beat_schedule = {
    # ── Existing tasks (keep these) ──────────────────────────────────────────
    "run-maintenance": {...},
    "check-schedule-health": {...},

    # ── NEW: Spec 1 — Agent Identity ─────────────────────────────────────────
    "warm-jwks-cache": {
        "task": "app.scaling.tasks.warm_jwks_cache",
        "schedule": crontab(minute="*/9"),  # before 10-min Redis TTL
    },

    # ── NEW: Spec 3 — Guardrails ─────────────────────────────────────────────
    "create-guardrail-partitions": {
        "task": "app.scaling.tasks.create_guardrail_partitions",
        "schedule": crontab(day_of_month="1", hour="2"),  # monthly
    },

    # ── NEW: Spec 4 — Governance ─────────────────────────────────────────────
    "enforce-hitl-sla": {
        "task": "app.scaling.tasks.enforce_hitl_sla",
        "schedule": crontab(minute="*/5"),
    },

    # ── NEW: Spec 5 — Audit Rails ─────────────────────────────────────────────
    "flush-audit-wal": {
        "task": "app.scaling.tasks.flush_audit_wal",
        "schedule": 10.0,  # every 10 seconds
    },
    "process-audit-dlq": {
        "task": "app.scaling.tasks.process_audit_dlq",
        "schedule": crontab(minute="*/30"),
    },
    "create-audit-partitions": {
        "task": "app.scaling.tasks.create_audit_partitions",
        "schedule": crontab(day_of_month="25", hour="2"),  # before month end
    },
    "run-audit-retention-sweep": {
        "task": "app.scaling.tasks.run_audit_retention_sweep",
        "schedule": crontab(hour="2", minute="0"),  # daily 02:00 UTC
    },

    # ── NEW: Spec 6 — Cost Optimization ──────────────────────────────────────
    "scan-cost-anomalies": {
        "task": "app.scaling.tasks.scan_cost_anomalies",
        "schedule": crontab(minute="0"),  # hourly
    },

    # ── NEW: Spec 7 — Marketplace ─────────────────────────────────────────────
    "embed-marketplace-templates": {
        "task": "app.scaling.tasks.embed_marketplace_templates",
        "schedule": crontab(minute="*/15"),
    },

    # ── NEW: Spec 9 — Self-Improvement ───────────────────────────────────────
    "conclude-stale-experiments": {
        "task": "app.scaling.tasks.conclude_stale_experiments",
        "schedule": crontab(hour="3", minute="0"),  # daily 03:00 UTC
    },

    # ── NEW: Spec 10 — Knowledge Bases ───────────────────────────────────────
    "expire-stale-documents": {
        "task": "app.scaling.tasks.expire_stale_documents",
        "schedule": crontab(hour="1", minute="0"),  # daily 01:00 UTC
    },
}

# Also add these queues to the worker -Q flag in docker-compose.yml:
# goals.persistence (from Loop Engineering spec)
# governance (for enforce-hitl-sla)
# maintenance (for all maintenance tasks)
# Full -Q flag:
# goals,goals.free,goals.starter,goals.professional,goals.enterprise,goals.persistence,
# schedules,maintenance,governance,goals_dlq
```

---

## U5. Integration Points in Existing Code — Where New Subsystems Hook In

Every spec describes its subsystem but doesn't specify WHERE in the existing agent execution loop it integrates. This is the most critical gap for engineers.

### U5.1 — Guardrails integration in graph.py

```python
# In app/agent/graph.py, in _node_execute():
# BEFORE the tool call (Layer 4 — tool args):
guardrail_engine = getattr(self._app_state, "guardrail_engine", None) if self._app_state else None
if guardrail_engine:
    tool_args_result = await guardrail_engine.evaluate_tool_args(
        tool_name=tool_name, arguments=tool_arguments,
        context=GuardrailContext(tenant_id=self._tenant_ctx.tenant_id, goal_id=self._goal_id, agent_id=self._agent_id, domain=domain_context)
    )
    if not tool_args_result.passed:
        raise ToolCallBlocked(f"Guardrail blocked tool call: {tool_args_result.violations[0].pattern_matched}")

# AFTER the tool call (Layer 5 — tool output):
if guardrail_engine:
    output_result = guardrail_engine.evaluate_output(
        output=str(tool_result),
        context=GuardrailContext(...)
    )
    if output_result.redacted_content:
        tool_result = output_result.redacted_content
```

### U5.2 — Cost tracking integration in providers

```python
# In app/providers/anthropic_provider.py, app/providers/openai_compatible.py, etc.
# After each LLM call, record actual token usage:
# ALREADY SPECIFIED in Spec 6 as CompletionResponse.usage
# The key integration: app/agent/graph.py must READ response.usage after each _call_llm():

response = await self._provider.complete(request)
if response.usage and self._cost_controller:
    actual_cost = calculate_cost(
        model=model_name,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )
    await self._cost_controller.check_and_record(
        cost_usd=actual_cost,
        tenant_ctx=self._tenant_ctx,
        goal_id=self._goal_id,
    )
    state.context["total_cost_usd"] = state.context.get("total_cost_usd", 0) + actual_cost
    state.context.setdefault("token_counts", {"prompt": 0, "completion": 0})
    state.context["token_counts"]["prompt"] += response.usage.prompt_tokens
    state.context["token_counts"]["completion"] += response.usage.completion_tokens
```

### U5.3 — Self-improvement A/B arm injection in graph.py

```python
# In app/agent/graph.py _node_initialize():
# Check for active experiment arm BEFORE goal planning:
self_optimizer = getattr(self._app_state, "self_optimizer", None) if self._app_state else None
if self_optimizer and self._agent_id:
    arm_config = await self_optimizer.get_arm_config(
        agent_id=self._agent_id, goal_id=self._goal_id,
        tenant_id=self._tenant_ctx.tenant_id,
    )
    if arm_config:
        if "system_prompt" in arm_config:
            self._system_prompt = arm_config["system_prompt"]
        if "max_iterations" in arm_config:
            self._max_iterations = arm_config["max_iterations"]
        state.context["_experiment_arm"] = arm_config.get("arm_name", "control")

# In app/agent/graph.py after _node_verify() completes:
if self_optimizer and state.context.get("_experiment_arm"):
    eval_score = state.eval_score or (state.context.get("eval_scorecard", {}) or {}).get("average_score")
    if eval_score is not None:
        await self_optimizer.record_result(
            goal_id=self._goal_id, agent_id=self._agent_id,
            arm_name=state.context["_experiment_arm"], eval_score=eval_score,
            cost_usd=state.context.get("total_cost_usd", 0),
            tenant_id=self._tenant_ctx.tenant_id,
        )
```

### U5.4 — Audit admin actions via decorator

```python
# In app/api/agents.py, decorate destructive endpoints:
from app.governance.audit import audit_admin_action

@router.delete("/{agent_id}")
@audit_admin_action(action_type="agent.deleted", resource_type="agent")
async def delete_agent(agent_id: str, request: Request):
    ...

# In app/api/tenants.py:
@router.post("/me/keys")
@audit_admin_action(action_type="api_key.created", resource_type="api_key")
async def create_api_key(...):
    ...

@router.delete("/me/keys/{key_id}")
@audit_admin_action(action_type="api_key.revoked", resource_type="api_key")
async def revoke_api_key(...):
    ...
```

### U5.5 — Knowledge search with tenant_id defence-in-depth

```python
# In app/rag/store.py hybrid_search_db() — add explicit tenant_id filter:
# The existing code relies ONLY on RLS (SET LOCAL app.tenant_id).
# Add explicit WHERE clause as defence-in-depth:
WHERE kc.tenant_id = :tenant_id  -- ← ADD THIS to every query
AND d.tenant_id = :tenant_id     -- ← ADD THIS to every query
# Even if RLS is misconfigured, explicit tenant_id prevents cross-tenant data access
```

---

## U6. Toast Notification Standards

Every mutation across all 10 specs MUST use consistent toast messages:

| Operation | Kind | Message |
|-----------|------|---------|
| Create (any resource) | `success` | `"[Resource] created"` |
| Update | `success` | `"[Resource] updated"` |
| Delete (after confirm) | `success` | `"[Resource] deleted"` |
| Revoke | `warning` | `"[Resource] revoked — takes effect immediately"` |
| Rollback | `warning` | `"Rolled back to previous version"` |
| Export started | `info` | `"Export started — you'll be notified when ready"` |
| Error | `error` | `"Failed: [operation]. Please try again."` |
| Async job queued | `info` | `"[Job] queued — check back in a few minutes"` |
| Security action | `warning` | `"Security action: [description]"` |

---

## U7. LoadingSpinner Reusable Component

Many specs reference `<LoadingSpinner />` but it may not exist as a reusable component. Ensure it exists:

```typescript
// src/components/ui/LoadingSpinner.tsx
import { Loader2 } from "lucide-react";
interface LoadingSpinnerProps { size?: "sm" | "md" | "lg"; className?: string; }
export function LoadingSpinner({ size = "md", className = "" }: LoadingSpinnerProps) {
  const sizes = { sm: "h-4 w-4", md: "h-5 w-5", lg: "h-8 w-8" };
  return (
    <div className={`flex items-center justify-center h-64 ${className}`} role="status" aria-label="Loading">
      <Loader2 className={`animate-spin text-muted-foreground ${sizes[size]}`} />
    </div>
  );
}
```

---

## Summary: What These Amendments Fix

| Amendment | Specs | What It Fixes |
|-----------|-------|--------------|
| U1 | All 10 | prefers-reduced-motion WCAG compliance |
| U2 | All 10 | App.tsx route registration (new pages are 404 without this) |
| U3 | All 10 | Sidebar navigation (pages are unreachable without this) |
| U4 | All 10 | Celery beat schedule (background jobs never fire without this) |
| U5 | 1,3,6,9,10 | Integration points in graph.py, providers, audit decorator |
| U6 | All 10 | Consistent toast message standards |
| U7 | All 10 | Shared LoadingSpinner component |
