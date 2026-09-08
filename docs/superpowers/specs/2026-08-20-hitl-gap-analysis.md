# HITL (Human-in-the-Loop) — Complete Gap Analysis
**Date:** 2026-08-20  
**Method:** Deep code analysis across all backend + frontend files  
**Verdict:** Backend is production-grade. Frontend has 18 documented gaps.

---

## Legend
- ✅ Implemented and working  
- ⚠️ Partially implemented — bugs or missing pieces  
- ❌ Not implemented  

---

## Verified Working (Do Not Touch)

| Component | File | Status |
|---|---|---|
| `HITLGateway` core | `app/governance/hitl.py` | ✅ Production-grade, Redis cross-replica, startup restore |
| Governance REST API (10 endpoints) | `app/api/governance.py` | ✅ list/approve/reject/stream/batch/history/SLA stats |
| Workflow HITL REST API (9 endpoints) | `app/workflow/router_hitl.py` | ✅ decide/delegate/escalate/bulk/magic-link/delegate-all |
| Magic-link endpoints | `app/api/governance.py:889,946` | ✅ `GET /hitl/{id}/approve` + `GET /hitl/{id}/reject` |
| Agent executor HITL gate (step-level) | `app/agent/nodes/executor_mixin.py:671–715` | ✅ Blocks execution, emits `waiting_approval` → `approval_granted` |
| Agent executor HITL gate (tool-level) | `app/agent/nodes/executor_mixin.py:1399–1437` | ✅ Emits `tool_call_pending_approval` → `approval_granted` |
| DB model `approval_requests` | `app/db/models/governance.py:39` | ✅ Table exists with all columns |
| DB migration | `app/db/migrations/versions/0056_governance_v2.py` | ✅ Applied |
| App startup restore | `app/main.py:1508–1516` | ✅ Pending requests restored on restart |
| Gateway wired to app state | `app/main.py:1773` | ✅ `app.state.hitl_gateway = _hitl` |
| Notification service trigger | `app/services/notification_service.py:129` | ✅ `notify_approval_required()` exists |
| `ApprovalsPage.tsx` | `src/features/approvals/ApprovalsPage.tsx` | ✅ Full inbox — SSE, SLA countdown, bulk actions, keyboard nav |
| `PendingApprovalsBadge.tsx` | `src/components/ui/PendingApprovalsBadge.tsx` | ✅ TopBar amber bell with live count |
| Sidebar nav badge | `src/components/ui/Sidebar.tsx:57–98` | ✅ Badge on `/approvals` nav item (polls every 10s) |
| Dashboard HITL banner | `src/features/dashboard/DashboardPage.tsx:299–310` | ✅ Amber banner when pending > 0 |
| Workflow `ApprovalInboxPage` | `src/features/workflow/ApprovalInboxPage.tsx` | ✅ Workflow-specific inbox |
| Org SSE event constants | `src/features/org/OrgRealtimeManager.ts:34–37` | ✅ 4 org approval events defined |
| `chat.types.ts` | `src/features/chat/types/chat.types.ts:81` | ✅ `hitl_required` in union type |
| `ChatHITLCard.tsx` component | `src/features/chat/ChatHITLCard.tsx` | ⚠️ Built, not wired (see G-02, G-03) |
| `ApprovalCenter.tsx` component | `src/features/org/ApprovalCenter.tsx` | ⚠️ Built, not wired (see G-04, G-05) |

---

## Gap G-01 — GoalDetailPage: `waiting_approval` SSE event is silently ignored

**File:** `src/features/goals/GoalDetailPage.tsx`  
**Verified:** `grep -n "waiting_approval|tool_call_pending_approval|approval_granted|hitl_rejected"` → **zero results**  
**Backend emits:** `waiting_approval` at `executor_mixin.py:679,704` and `tool_call_pending_approval` at line 1415  
**Symptom:** When an agent hits an approval gate during goal execution, GoalDetailPage only learns about it by polling `goal.status === "waiting_human"` every 2 seconds. The live SSE stream delivers the event instantly but nothing in the page reacts to it.  

**What is missing:**
1. `useGoalStream` `onEvent` callback must handle `waiting_approval` → show inline approval panel immediately (not wait for polling)
2. `tool_call_pending_approval` → show tool name + amber inline card in the execution log
3. `approval_granted` → dismiss the approval panel with a green animation
4. `hitl_rejected` → show rejection notice in execution log

**Fix location:** `GoalDetailPage.tsx` around line 645 (existing `approvalNote` state), extend the `useGoalStream` `onEvent` handler to capture these 4 event types and drive local state directly.

---

## Gap G-02 — ChatPage: `hitl_required` event is defined but never rendered

**Files:**  
- `src/features/chat/types/chat.types.ts:81` — defines `hitl_required` in the event union  
- `app/chat/stream.py:141–159` — backend emits `hitl_required` with `approval_token`  
- `src/features/chat/ChatPage.tsx` — **zero references** to `hitl`, `HITL`, `ChatHITLCard`, or `waiting_approval`  

**Symptom:** When a chat-triggered goal hits an approval gate, the chat thread shows nothing. The user cannot approve or reject from the chat interface at all.

**What is missing:**
1. ChatPage's `useChatStream` event handler must intercept `hitl_required` events
2. Inject a `<ChatHITLCard>` into the message thread at the correct position
3. The card's `onApprove`/`onReject` must call `governanceApi.approve()` / `governanceApi.reject()`
4. After resolution, insert a follow-up "Approved ✓" or "Rejected ✗" system message

---

## Gap G-03 — `ChatHITLCard.tsx`: approve/reject buttons have no implementation

**File:** `src/features/chat/ChatHITLCard.tsx`  
**Verified:** `onApprove` and `onReject` are props with `() => void` type, no API calls inside the component  
**Additional bug:** The countdown timer (`timeoutSeconds` prop) is static — it reads the initial value but never decrements. There is no `useEffect` or `setInterval` for the countdown.

**What is missing:**
1. `onApprove` caller (parent) must pass a handler that calls `governanceApi.approve(requestId, approver, note)`
2. `onReject` caller must call `governanceApi.reject(requestId, approver, note)`
3. Replace static countdown with a live `useInterval`-driven countdown that turns red in last 60 seconds
4. Show `requestId` in the card (needed to make the API call)

---

## Gap G-04 — Backend: `GET /v1/org/{org_id}/approvals` endpoint does not exist

**File:** `app/org/router.py`  
**Verified:** `grep -rn "v1/org.*approvals"` → no router route found  
**Consumer:** `src/features/org/ApprovalCenter.tsx` calls `apiFetch('/v1/org/${orgId}/approvals')` → **returns 404**

**What is missing:**  
New endpoint in `app/org/router.py`:
```python
@router.get("/{org_id}/approvals", operation_id="org_list_approvals")
async def list_org_approvals(org_id: str, service: OrgService = Depends(get_org_service)) -> list[dict]:
    """Return pending approval requests scoped to missions within this org."""
    # Query approval_requests table filtered by goal_id/mission_id belonging to this org
```
This endpoint should return approvals scoped to the org's missions/tasks, not all tenant approvals.

---

## Gap G-05 — Frontend: `ApprovalCenter.tsx` is not rendered in `OrgPage.tsx`

**File:** `src/features/org/OrgPage.tsx`  
**Verified:** `grep -n "ApprovalCenter"` → **zero results**  
**Component:** `src/features/org/ApprovalCenter.tsx` — fully built with grouped risk view, approve/reject/modify/delegate, Framer Motion  

**Symptom:** The ApprovalCenter component exists but is dead code — never imported, never shown.

**What is missing:**
1. Add `import { ApprovalCenter } from './ApprovalCenter'` to `OrgPage.tsx`
2. Add a button/tab/panel to show it (e.g. in the existing action button row or as a drawer triggered by the health widget's `pending_approvals` count)
3. Pass `orgId` prop

---

## Gap G-06 — Backend: `app/org/approval_chain.py` is disconnected from all execution paths

**File:** `app/org/approval_chain.py`  
**Verified:** `grep -rn "approval_chain|ApprovalChain|ChainEngine"` across all `.py` files excluding the file itself → **zero results**  

The cross-department approval chain engine defines:
- `ApprovalChain` — trigger pattern, required roles, strategy (all/any/majority), timeout, escalation path
- `ApprovalRequest` — chain-specific request with multi-step tracking
- `ChainEngine` — executes the chain

**What is missing:**
1. `OrgService` must import and invoke `ChainEngine.start()` when a high-risk mission action is triggered
2. A REST endpoint to configure org-level approval chains (CRUD)
3. Frontend UI to view/manage chains in org settings
4. The chain engine must feed into `HITLGateway` (currently they are parallel systems that never interact)

---

## Gap G-07 — Frontend: `MissionDetail.tsx` has no approval panel for blocked tasks

**File:** `src/features/org/components/MissionDetail.tsx`  
**Verified:** `grep -n "approval|hitl|HITL|waiting"` → only shows `"Queued — waiting for agent worker"` static text  

**Symptom:** When a mission task reaches `approval_required` status (defined in `types.ts:28`), `MissionDetail` shows the task list with an amber `AlertTriangle` icon but provides:
- No approve/reject buttons
- No link to ApprovalsPage
- No note input

**What is missing:**
1. When any task in `data.tasks` has `status === 'approval_required'`, render an inline `ApprovalCallout` component
2. The callout must show: task title, risk level, action requested, approve/reject buttons
3. Approved/rejected tasks must re-fetch via `queryClient.invalidateQueries`

---

## Gap G-08 — Frontend: `OrgRealtimeManager` only invalidates query on HITL events — no user notification

**File:** `src/features/org/OrgRealtimeManager.ts:196–200`  
**Current behavior:**
```typescript
case ORG_EVENTS.APPROVAL_REQUESTED:
case ORG_EVENTS.APPROVAL_GRANTED:
case ORG_EVENTS.APPROVAL_REJECTED:
case ORG_EVENTS.APPROVAL_TIMEOUT:
  qc.invalidateQueries({ queryKey: ['approvals', orgId] });
  break;
```
This re-fetches the approvals list silently. No toast, no navigation, no visual indicator on OrgPage.

**What is missing:**
1. `APPROVAL_REQUESTED` → show a toast: `"Approval required: {action}"` with amber styling + link to ApprovalsPage
2. `APPROVAL_TIMEOUT` → show a red toast: `"Approval timed out: {action} was rejected automatically"`
3. `APPROVAL_GRANTED` → show a green toast: `"Approved by {approver}"`
4. `APPROVAL_REQUESTED` → increment a local approval counter badge in OrgPage header (existing `health.pending_approvals` widget)

---

## Gap G-09 — Frontend: Dashboard approval banner shows count but not risk or goal context

**File:** `src/features/dashboard/DashboardPage.tsx:299–310`  
**Current behavior:** Shows `"{n} action(s) require your approval"` — clickable → navigates to `/approvals`

**What is missing:**
1. Show risk breakdown in the banner: e.g. `"1 critical · 2 high · 1 medium"`
2. Show the highest-risk item inline: `"Awaiting: email_send to 1,200 recipients (CRITICAL)"`
3. Add direct quick-action buttons: `Approve` / `Reject` for the highest-priority item without navigating away

---

## Gap G-10 — Backend: No org-scoped approval filtering in governance API

**File:** `app/api/governance.py:386–406`  
**Current behavior:** `GET /approvals` returns ALL pending approval requests for the tenant, regardless of which org or mission they belong to  

**Symptom:** A tenant with multiple orgs sees approvals from all orgs mixed together in ApprovalsPage. There is no way to filter by org.

**What is missing:**
1. Add `org_id?: str` query param to `GET /approvals`
2. Filter `ApprovalRequest` list by matching `goal_id` → `goal.org_id` when `org_id` is provided
3. Update frontend `ApprovalsPage.tsx` to pass `org_id` when navigated from an org context

---

## Gap G-11 — Frontend: No approval history in OrgPage or MissionDetail

**Files:** `ApprovalsPage.tsx` has a history tab (✅), but it is not accessible from the org or mission context  

**Symptom:** Looking at a completed mission, you cannot see which actions were approved, who approved them, or how long approval took.

**What is missing:**
1. `MissionDetail.tsx` — add an "Approvals" section showing resolved approvals for that mission's goal
2. `OrgPage` — add an approval history view accessible from the activity feed
3. Query: `GET /approvals/history?goal_id={goal_id}` (endpoint exists via `/approvals/history` but needs `goal_id` filter added)

---

## Gap G-12 — Backend: No `notify_approval_timeout` — timeout silently drops

**File:** `app/services/notification_service.py`  
**Verified:** Has `notify_approval_required()` (line 129) but no `notify_approval_timeout()` method  
**Backend behavior:** `HITLGateway.expire_timed_out_requests()` (line 474) marks requests as `timed_out` but calls nothing on the notification service  

**What is missing:**
1. Add `notify_approval_timeout(goal_id, action, approvers)` to `NotificationService`
2. Call it from `HITLGateway.expire_timed_out_requests()`
3. Frontend must handle the `org.approval.timeout` event (currently defined in OrgRealtimeManager but takes no action beyond query invalidation — see G-08)

---

## Gap G-13 — Backend: `hitl_workflow_gateway` initialization is silent-fail — gateway may be None

**File:** `app/main.py:1839–1857`  
**Current behavior:**
```python
try:
    from app.workflow.hitl_extension import HITLWorkflowGateway
    _hitl_wf_gateway = HITLWorkflowGateway()
    app.state.hitl_workflow_gateway = _hitl_wf_gateway
except Exception:
    pass   # silent fail
```
**Verified:** `app/workflow/router_hitl.py:37` does `svc = getattr(request.app.state, "hitl_workflow_gateway", None)` — if initialization failed, `svc` is `None` for every request  

**What is missing:**
1. Log a warning (not silent pass) when workflow HITL gateway fails to initialize
2. Return `503 Service Unavailable` with a clear message when `svc is None` in workflow HITL endpoints
3. Add a health check for `hitl_workflow_gateway` in `GET /health`

---

## Gap G-14 — Frontend: No per-org scoped approval badge in OrgPage

**Current:** `PendingApprovalsBadge` in TopBar shows ALL tenant approvals. When on OrgPage, there's no scoped badge showing only this org's pending approvals.

**Symptom:** If a user manages 3 orgs, the badge count is the sum across all orgs. Navigating between orgs doesn't change the badge.

**What is missing:**
1. Create `OrgApprovalBadge` component that queries `GET /approvals?org_id={orgId}` (requires G-10 fix)
2. Render it in OrgPage header near the existing `OrgHealthWidget` 
3. Show org-specific count: `"3 pending this org"`

---

## Gap G-15 — Frontend: No HITL visualization in GoalDetailPage execution graph

**Reference:** `2026-08-19-agentverse-jarvis-visualization-master-spec.md` section 4 (Goal Execution Neural Theater)  
**Verified:** `HITLGateNode`, `GuardrailShield` components — **do not exist**  

**Symptom:** Even if G-01 is fixed (SSE event handling), there's no visual representation — no amber gate, no blocking animation, no gate-opens-on-approve.

**What is missing:** As specified in the JARVIS visualization spec:
1. `src/components/neural/HITLGateNode.tsx` — amber spinning diamond, countdown, approver name
2. `src/components/neural/GuardrailShield.tsx` — red shield materializer for `guardrail_rejected`
3. Integration into `GoalExecutionGraph` (once built) or into the existing execution tab as an animated card

---

## Gap G-16 — Backend: `expire_timed_out_requests()` has no Celery beat task — timeouts never fire automatically

**File:** `app/governance/hitl.py:474`  
**Verified:** `HITLGateway.expire_timed_out_requests()` exists and correctly marks stale requests, but there is **no Celery beat schedule** that calls it  

**What is missing:**
1. Add a Celery periodic task in `app/scaling/tasks.py`:
```python
@celery_app.task(name="hitl.expire_timed_out", bind=True)
def expire_hitl_timeouts(self) -> dict:
    gateway = # get from app.state
    expired = gateway.expire_timed_out_requests()
    return {"expired": expired}
```
2. Register it in `celery_app.conf.beat_schedule` to run every 60 seconds
3. The task must also call `notify_approval_timeout()` for each expired request (requires G-12)

---

## Gap G-17 — Backend: Magic link URLs are never generated or sent in notifications

**Files:**  
- `app/api/governance.py:889` — `GET /hitl/{id}/approve` exists  
- `app/api/governance.py:946` — `GET /hitl/{id}/reject` exists  
- `app/services/notification_service.py:129` — `notify_approval_required()` sends notification but does not include magic link URL  

**Symptom:** Magic link endpoints exist and work, but no notification (email/Slack/webhook) ever includes the magic link URL. The feature is built end-to-end but never used.

**What is missing:**
1. `notify_approval_required()` must build the magic link URL:
```python
approve_url = f"{settings.PUBLIC_BASE_URL}/hitl/{request_id}/approve?token={approval_token}"
reject_url  = f"{settings.PUBLIC_BASE_URL}/hitl/{request_id}/reject?token={approval_token}"
```
2. Include URLs in the notification payload sent to Slack/email/webhook channels
3. `PUBLIC_BASE_URL` must be added to `app/core/config.py`

---

## Gap G-18 — Frontend: `approval_token` in `hitl_required` chat event is unused

**Files:**  
- `src/features/chat/types/chat.types.ts:81` — `hitl_required` event with `approval_token` field defined  
- `app/chat/stream.py:159` — backend generates and sends `approval_token` in the SSE payload  

**Symptom:** Even if G-02 is fixed and ChatPage renders `ChatHITLCard`, the card should be able to generate a copyable magic link. The `approval_token` is already in the event but no frontend code ever reads it.

**What is missing:**
1. `ChatHITLCard` must accept `approvalToken?: string` prop
2. Render a "Copy magic link" button that constructs the URL:
   `${window.location.origin}/hitl/${requestId}/approve?token=${approvalToken}`
3. This allows approving from a different device/browser tab

---

## Summary: Fix Priority Order

| Gap | Severity | Fix complexity | User impact |
|---|---|---|---|
| G-01 — GoalDetailPage ignores HITL SSE events | 🔴 Critical | Low — add 4 event handlers | Goal execution freezes silently |
| G-02 — ChatPage ignores `hitl_required` | 🔴 Critical | Medium — wire card to SSE | Chat-triggered goals block silently |
| G-04 — Org approvals endpoint missing | 🔴 Critical | Low — add 1 router endpoint | ApprovalCenter crashes with 404 |
| G-05 — ApprovalCenter not in OrgPage | 🔴 Critical | Low — 2 lines import + render | Org mission approval flow broken |
| G-16 — Celery beat missing for HITL timeouts | 🔴 Critical | Low — add 1 beat task | Approvals never auto-expire |
| G-03 — ChatHITLCard no API calls | 🟠 High | Low — add API calls in props | Approve/reject button does nothing |
| G-06 — approval_chain.py not connected | 🟠 High | High — wire into OrgService | Cross-dept approval chains never fire |
| G-07 — MissionDetail has no approval panel | 🟠 High | Medium — add inline callout | Blocked missions have no action button |
| G-08 — OrgRealtimeManager no toast on HITL | 🟠 High | Low — add toast calls | Users miss real-time approval alerts |
| G-12 — No timeout notification | 🟠 High | Low — add notify call | Users never know approval timed out |
| G-13 — Workflow gateway silent-fail | 🟠 High | Low — log + 503 response | Workflow HITL silently unavailable |
| G-09 — Dashboard banner lacks risk context | 🟡 Medium | Low — extend banner | Approval urgency not communicated |
| G-10 — Approvals not org-scoped | 🟡 Medium | Medium — add query param + filter | Multi-org tenants see mixed approvals |
| G-11 — No approval history in mission view | 🟡 Medium | Medium — add section | Audit trail hidden from mission context |
| G-14 — No per-org approval badge | 🟡 Medium | Low — new component | Org-specific urgency not visible |
| G-17 — Magic links never sent in notifications | 🟡 Medium | Low — add URL to payload | One-click approval from email/Slack broken |
| G-15 — No HITL visual in execution graph | 🟡 Medium | High — new components | JARVIS viz spec not met |
| G-18 — `approval_token` unused in chat | 🟢 Low | Low — add prop to card | Magic link from chat not available |

---

## Files to Create

| File | Purpose |
|---|---|
| `src/components/neural/HITLGateNode.tsx` | Animated HITL gate for execution graph (G-15) |
| `src/components/neural/GuardrailShield.tsx` | Guardrail block visualizer (G-15) |

## Files to Modify

| File | Changes needed |
|---|---|
| `src/features/goals/GoalDetailPage.tsx` | Handle `waiting_approval`, `tool_call_pending_approval`, `approval_granted`, `hitl_rejected` in SSE `onEvent` (G-01) |
| `src/features/chat/ChatPage.tsx` | Inject `<ChatHITLCard>` when `hitl_required` fires (G-02) |
| `src/features/chat/ChatHITLCard.tsx` | Wire approve/reject to governance API, add live countdown, add `approvalToken` prop (G-03, G-18) |
| `src/features/org/OrgPage.tsx` | Import and render `ApprovalCenter` (G-05) |
| `src/features/org/components/MissionDetail.tsx` | Add inline approval panel for `approval_required` tasks (G-07) |
| `src/features/org/OrgRealtimeManager.ts` | Add toast notifications for HITL events (G-08) |
| `src/features/dashboard/DashboardPage.tsx` | Add risk breakdown and quick actions to approval banner (G-09) |
| `app/org/router.py` | Add `GET /{org_id}/approvals` endpoint (G-04, G-10) |
| `app/scaling/tasks.py` | Add `expire_hitl_timeouts` Celery beat task (G-16) |
| `app/services/notification_service.py` | Add `notify_approval_timeout()`, add magic link URLs to `notify_approval_required()` (G-12, G-17) |
| `app/governance/hitl.py` | Call `notification_service.notify_approval_timeout()` in `expire_timed_out_requests()` (G-16) |
| `app/workflow/router_hitl.py` | Return 503 when `hitl_workflow_gateway is None` (G-13) |
| `app/main.py` | Log warning on workflow gateway init failure (G-13) |
| `app/core/config.py` | Add `PUBLIC_BASE_URL: str` setting (G-17) |

---

## PART 2 — AI Org Team HITL Flow (Deep Analysis)

> These gaps are specific to the cross-department, org-level HITL flow.  
> They are **separate from** the goal/agent-level HITL (covered in Part 1 above).  
> The AI Org Team has its own approval engine (`approval_chain.py`), event publisher (`events.py`), and org health metrics — none of which are connected to each other.

---

## Gap G-19 — CRITICAL: `OrgEventPublisher` is never initialized — `org.approval.*` SSE events are never sent

**Files:**  
- `app/org/events.py:283` — `get_org_event_publisher()` factory exists  
- `app/org/events.py:296` — `setup_org_event_publisher(redis_client, ...)` exists  
- `app/main.py` — **zero calls** to `setup_org_event_publisher` or `get_org_event_publisher`  
- `app/org/service.py` — **zero calls** to `OrgEventPublisher`  

**Verified:** `grep -rn "get_org_event_publisher|setup_org_event_publisher|OrgEventPublisher"` across all `.py` files → only found inside `events.py` itself.

**Consequence:** The 4 org approval events (`org.approval.requested`, `org.approval.granted`, `org.approval.rejected`, `org.approval.timeout`) are defined in `ORG_AUDIT_EVENTS` and handled in `OrgRealtimeManager.ts` — but the backend **never publishes them**. `OrgRealtimeManager`'s `APPROVAL_*` case handlers (lines 196-200) **never fire** because no SSE event is ever sent.

**What is missing:**
1. Call `setup_org_event_publisher(redis_client, audit_service, notification_router)` in `app/main.py` lifespan after Redis is initialized
2. Call `publisher.publish_approval_event(lifecycle, approval_id, action, approver)` from `OrgService` when tasks transition to/from `approval_required` status
3. Wire the publisher singleton into `app.state` so it's accessible throughout the app

---

## Gap G-20 — CRITICAL: `ApprovalChainEngine` uses in-memory dict — not persistent, not cross-replica

**File:** `app/org/approval_chain.py:234`  
```python
def __init__(self, store: dict[str, ApprovalRequest] | None = None) -> None:
    self._store: dict[str, ApprovalRequest] = store if store is not None else {}
```
**Comment in the code:** `"In-memory store for tests / demo; production wires a DB-backed store"`  
**Verified:** No DB-backed store implementation exists. `_store` is always a Python dict.

**Consequences:**
- All cross-department approval requests are **lost on server restart**
- In multi-replica Kubernetes deployments, approval requests exist only in the replica that received the request
- Cannot query "all pending org approvals" reliably — each replica has partial state
- `ApprovalChainEngine` is a completely separate system from `HITLGateway` (which has proper DB persistence via `app/db/models/governance.py:approval_requests`)

**What is missing:**
1. Implement a DB-backed store using the existing `OrgTask` or `approval_requests` table
2. OR: integrate `ApprovalChainEngine` with `HITLGateway` (which already has DB persistence + Redis cross-replica)
3. Implement `async def startup_restore()` (analogous to `HITLGateway.startup_restore()`)

---

## Gap G-21 — CRITICAL: `approval_gates` are computed but never enforced during org mission execution

**File:** `app/org/meta_orchestrator.py:266,278`  
```python
approval_gates = _compute_approval_gates(goal_analysis, manifest)
# ...
OrchestrationPlan(approval_gates=approval_gates, ...)
```
**File:** `app/org/service.py:1156`  
```python
plan_summary["approval_gates"] = orch_plan.approval_gates  # stored in metadata
```

The `_compute_approval_gates()` function correctly identifies that a mission needs "high_risk_action", "legal_review", "financial_commitment", or "budget_threshold" approval. However:
- The gates are stored in `mission.metadata["approval_gates"]`
- **No code in `OrgService.execute_mission()` checks these gates before dispatching to `GoalService`**
- **No code in the agent executor reads `mission.metadata["approval_gates"]`**
- A high-risk mission (e.g., "external data sharing") is dispatched immediately with no approval, even if it matched the `external_data_sharing` approval chain

**What is missing:**
1. In `OrgService.execute_mission()`, after computing `approval_gates`, check against `ApprovalChainEngine.check_requires_approval(action, context, org)`
2. If approval is required: create the chain request, emit `org.approval.requested` SSE, **pause execution**, wait for approval resolution
3. Only dispatch to `GoalService` after all required approvals are resolved

---

## Gap G-22 — CRITICAL: `OrgTask.status = "approval_required"` is never set by any execution path

**File:** `app/org/service.py:903` — queries tasks with `OrgTask.status == "approval_required"` to count pending approvals  
**Verified:** `grep -rn "update_task_status.*approval_required"` → **zero results**  
**Verified:** Only `app/agent/workflow_executor.py:402` uses `"approval_required"` as a reason string, not as a task status update through OrgService.

**Consequence:** The `pending_approvals` count in `OrgService.get_org_health()` is **always 0** because no code path ever sets an OrgTask to `approval_required` status. The `OrgHealthWidget` shows "0 pending approvals" even when approvals are genuinely needed. The morning brief voice greeting counts are wrong. Dashboard amber banner never shows.

**What is missing:**
1. When the org approval flow decides a task needs approval (from G-21), call `OrgService.update_task_status(task_id, "approval_required")`
2. When approval is granted, call `OrgService.update_task_status(task_id, "running")`
3. When approval is rejected, call `OrgService.update_task_status(task_id, "failed")`

---

## Gap G-23 — CRITICAL: `GET /v1/org/{orgId}/events/stream` does NOT exist — `OrgRealtimeManager` subscribes to a 404

**File:** `app/org/router.py:547` — `GET /{org_id}/events` exists but returns paginated JSON (`CursorPage[OrgEventResponse]`)  
**Frontend:** `OrgRealtimeManager.ts:111` — `const url = '/v1/org/${this.orgId}/events/stream'`  

The frontend subscribes to `/v1/org/{orgId}/events/stream` (with `/stream` suffix, SSE format) but this endpoint **does not exist**. The actual endpoint is `GET /v1/org/{orgId}/events` which is a regular JSON REST endpoint, not a Server-Sent Events stream.

**Consequence:** Every `OrgRealtimeManager` instance gets an immediate `onerror` from the browser because `/events/stream` returns 404. The reconnect timer fires every 2 seconds indefinitely. **All 35 org SSE events never reach the frontend.** The mission-level stream (`/{org_id}/missions/{mission_id}/stream`) does exist as SSE, but there is no org-level SSE stream.

**What is missing:**
1. Add `GET /{org_id}/events/stream` as an SSE endpoint in `app/org/router.py`:
```python
@router.get("/{org_id}/events/stream", operation_id="org_events_sse")
async def org_events_stream(org_id: str, request: Request) -> StreamingResponse:
    # Subscribe to Redis pub/sub channel: f"org:{org_id}:events"
    # Yield SSE data frames for each org event
```
2. `OrgEventPublisher.publish()` must publish to `f"org:{org_id}:events"` Redis channel
3. Both fixes require G-19 (OrgEventPublisher initialization) to be fixed first

---

## Gap G-24 — CRITICAL: `ApprovalCenter.tsx` calls three endpoints that don't exist in org router

**File:** `src/features/org/ApprovalCenter.tsx:55,67`  
```typescript
// GET /v1/org/${orgId}/approvals          → 404 (endpoint missing)
// POST /v1/org/${orgId}/approvals/${id}/approve → 404
// POST /v1/org/${orgId}/approvals/${id}/reject  → 404
```
**Verified:** `grep -n "@router" app/org/router.py` — no `/approvals` route exists  

Additionally, `ApprovalCenter.tsx` uses an `Approval` TypeScript interface with fields:
```typescript
{ id, title, description, action_type, risk_level, estimated_cost_usd,
  mission_id, agent_id, prerequisite_approvals, already_approved_by, approvers_needed }
```
But `HITLGateway.list_pending()` returns `ApprovalRequest` objects with different field names (`request_id`, `action`, `risk_level`, `required_approvers`, `approvals_received`). Even if the endpoint existed, the shape mismatch would cause runtime errors.

**What is missing:**
1. Add 3 endpoints to `app/org/router.py`:
   - `GET /{org_id}/approvals` — scoped pending list
   - `POST /{org_id}/approvals/{id}/approve`
   - `POST /{org_id}/approvals/{id}/reject`
2. These endpoints should aggregate from BOTH `HITLGateway` (goal-level HITL) AND `ApprovalChainEngine` (org-level chains)
3. Return a unified shape matching `ApprovalCenter.tsx`'s `Approval` interface

---

## Gap G-25 — HIGH: `pending_approvals` in OrgHealthWidget counts task status, not actual approval requests

**File:** `app/org/service.py:903` — counts `OrgTask.status == "approval_required"`  
**File:** `src/features/org/components/OrgHealthWidget.tsx` — displays `health.pending_approvals`  
**File:** `src/features/org/ApprovalCenter.tsx` — calls `GET /v1/org/{orgId}/approvals` (404)

These two systems are siloed:
- The health widget count comes from **task status** in the `org_tasks` table
- The ApprovalCenter shows data from **HITLGateway** (goal execution HITL) via a missing endpoint
- They measure different things and neither is correctly wired

**Consequence:** A user sees "3 pending approvals" in the OrgHealthWidget but clicks "Approvals" and sees nothing (ApprovalCenter errors). Or vice versa.

**What is missing:**
1. Unified `pending_approvals` count that aggregates both sources:
   - `HITLGateway.list_pending()` filtered by org's goals
   - `ApprovalChainEngine.list_pending(tenant_id, org_id)`
   - `OrgTask` count with `status == "approval_required"`
2. Frontend badge and health widget must use the same data source

---

## Gap G-26 — HIGH: `ApprovalChainEngine.escalate_timeout()` logs warning but takes no action

**File:** `app/org/approval_chain.py:351–368`  
```python
async def escalate_timeout(self, request_id: str) -> None:
    req = self._store.get(request_id)
    # ...
    req.status = "escalated"
    _log.warning("approval_chain.timeout_escalated", ...)
    # That's it — no notification, no escalation to actual humans
```

When an approval chain times out, the engine:
- Sets `status = "escalated"` in the in-memory store (already lost after restart per G-20)
- Logs a warning
- **Does NOT notify the escalation path** (`chain.escalation_path` is ignored)
- **Does NOT create a new approval request** for the escalation roles
- **Does NOT publish `org.approval.timeout` event** to SSE

**What is missing:**
1. `escalate_timeout()` must call `OrgEventPublisher.publish_approval_event("timeout", ...)`
2. Must create new `HITLGateway.request_approval()` directed at the `escalation_path` roles
3. Must call `NotificationService.notify_approval_timeout()`
4. Must call `OrgService.update_task_status(task_id, "blocked")` if the task was waiting on this chain

---

## Gap G-27 — HIGH: `OrgPage.tsx` does not render `ApprovalCenter` — full org approval flow is inaccessible

**File:** `src/features/org/OrgPage.tsx`  
**Verified:** `grep -n "ApprovalCenter"` → zero results  

The `ApprovalCenter.tsx` component is built with full approve/reject/delegate/modify UI but is never imported or shown anywhere. Users in an org context have no way to see or act on org-level approval requests without navigating to the global `/approvals` page (which shows goal-level HITL, not org-chain approvals).

**What is missing:**
1. Add `import { ApprovalCenter } from './ApprovalCenter'` to `OrgPage.tsx`
2. Add a trigger button (e.g., in the action row next to "Voice" and "Connectors") that opens `ApprovalCenter` when `health.pending_approvals > 0`
3. OR: Add as a panel in the existing `NowNextWhy` or `ActivityFeed` area when pending > 0

---

## Gap G-28 — MEDIUM: No endpoint to approve/reject individual OrgTask with `approval_required` status

**File:** `app/org/router.py` — `POST /{org_id}/tasks/{task_id}/status` exists  

While `OrgTask.status` can theoretically be updated to `running` (approval granted) or `failed` (approval rejected) via `POST /{org_id}/tasks/{task_id}/status`, this generic status endpoint:
- Has no `approver` field
- Has no `note` field for rejection reason
- Has no RBAC check that only allowed roles can approve
- Does not emit `org.approval.granted` / `org.approval.rejected` event
- Does not unblock the waiting agent execution

**What is missing:**
1. `POST /{org_id}/tasks/{task_id}/approve` with `{ approver, note }` payload
2. `POST /{org_id}/tasks/{task_id}/reject` with `{ approver, reason }` payload
3. Both must: validate approver role, emit `org.approval.granted/rejected`, update task status, resume blocked execution

---

## Gap G-29 — MEDIUM: `org.approval.requested` event never triggers a notification

**File:** `app/services/notification_service.py:129`  
`notify_approval_required()` exists and sends notifications — but is only called from the **goal-level** HITL flow (`executor_mixin.py` indirectly via `HITLGateway`)

The org-level flow (`ApprovalChainEngine.create_approval_request()`) **never calls** `notify_approval_required()`. When an org-level approval is needed, no email/Slack/webhook notification is sent.

**What is missing:**  
`ApprovalChainEngine.create_approval_request()` must call `notification_service.notify_approval_required(goal_id, action, approvers=chain.required_roles)` after creating the request.

---

## Updated Summary Table

| Gap | Area | Severity | Root cause |
|---|---|---|---|
| G-19 | Org Team | 🔴 Critical | `OrgEventPublisher` never initialized — org approval SSE never sent |
| G-20 | Org Team | 🔴 Critical | `ApprovalChainEngine` in-memory only — data lost on restart |
| G-21 | Org Team | 🔴 Critical | `approval_gates` computed but never enforced during mission execution |
| G-22 | Org Team | 🔴 Critical | `OrgTask.status = "approval_required"` never set — health count always 0 |
| G-23 | Org Team | 🔴 Critical | `/v1/org/{id}/events/stream` doesn't exist — OrgRealtimeManager gets 404 |
| G-24 | Org Team | 🔴 Critical | `ApprovalCenter.tsx` calls 3 missing endpoints + shape mismatch |
| G-25 | Org Team | 🟠 High | Two siloed approval count systems measure different things |
| G-26 | Org Team | 🟠 High | `escalate_timeout()` logs only — no notification, no escalation action |
| G-27 | Org Team | 🟠 High | `ApprovalCenter` never rendered in `OrgPage` |
| G-28 | Org Team | 🟡 Medium | No dedicated approve/reject task endpoint with RBAC + audit |
| G-29 | Org Team | 🟡 Medium | Org-chain approval requests never trigger notification |
| G-01 | Goal | 🔴 Critical | `GoalDetailPage` ignores HITL SSE events |
| G-02 | Chat | 🔴 Critical | `ChatPage` ignores `hitl_required` event |
| G-04 | Org | 🔴 Critical | `GET /v1/org/{id}/approvals` endpoint missing |
| G-05 | Org | 🔴 Critical | `ApprovalCenter` not in `OrgPage` |
| G-16 | System | 🔴 Critical | No Celery beat for HITL timeout expiry |
| G-03 | Chat | 🟠 High | `ChatHITLCard` buttons not wired to API |
| G-06 | Org Team | 🟠 High | `approval_chain.py` disconnected from execution |
| G-07 | Org | 🟠 High | `MissionDetail` has no approval panel |
| G-08 | Org | 🟠 High | `OrgRealtimeManager` no toast on HITL events |
| G-12 | System | 🟠 High | No `notify_approval_timeout()` |
| G-13 | System | 🟠 High | `hitl_workflow_gateway` silent-fail |
| G-09 | Dashboard | 🟡 Medium | Approval banner lacks risk context |
| G-10 | API | 🟡 Medium | Approvals not org-scoped |
| G-11 | Org | 🟡 Medium | No approval history in mission view |
| G-14 | Org | 🟡 Medium | No per-org approval badge |
| G-15 | Viz | 🟡 Medium | No HITL visualization components |
| G-17 | System | 🟡 Medium | Magic links not in notifications |
| G-18 | Chat | 🟢 Low | `approval_token` unused in chat card |

---

## AI Org Team HITL End-to-End Flow: What Should Happen vs What Does

```
DESIRED FLOW:
  1. OrgService.execute_mission() called for high-risk mission
  2. MetaOrchestrator.plan_mission() → approval_gates = ["financial_commitment"]
  3. ApprovalChainEngine.check_requires_approval("financial commitment", ...) → matches chain
  4. ApprovalChainEngine.create_approval_request() → creates request for [CFO, financial_controller]
  5. OrgService.update_task_status(task_id, "approval_required")     ← G-22
  6. OrgEventPublisher.publish_approval_event("requested", ...)      ← G-19
  7. NotificationService.notify_approval_required(...)               ← G-29
  8. Frontend: OrgRealtimeManager receives org.approval.requested    ← G-23
  9. Frontend: OrgPage shows amber badge / opens ApprovalCenter      ← G-27
  10. CFO clicks "Approve" in ApprovalCenter                         ← G-24 (404)
  11. Backend: ApprovalChainEngine.record_approval(request_id, "CFO", True)
  12. Backend: check strategy "all" → still needs financial_controller
  13. financial_controller approves → check_approval_complete() returns True
  14. OrgService.update_task_status(task_id, "running")              ← G-22
  15. OrgEventPublisher.publish_approval_event("granted", ...)       ← G-19
  16. GoalService.dispatch_goal() called                              ← G-21
  17. Goal executes normally
  18. If timeout: escalate_timeout() → notify CTO, block task        ← G-26

ACTUAL FLOW (today):
  1. OrgService.execute_mission() called ✅
  2. MetaOrchestrator computes approval_gates ✅
  3. approval_gates stored in mission.metadata ✅ — NEVER ENFORCED ❌ (G-21)
  4. GoalService.dispatch_goal() called immediately ✅ — skips all approval logic ❌
  5. Goal executes with no approval check ❌
  6. Org health shows pending_approvals=0 always ❌ (G-22)
  7. No SSE events published ❌ (G-19)
  8. OrgRealtimeManager gets 404 ❌ (G-23)
  9. ApprovalCenter shows 404 error ❌ (G-24)
  10. Timeouts silently drop ❌ (G-20, G-26)
```
