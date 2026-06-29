# Goal Detail Page

## Purpose

The Goal Detail page (`/goals/:goalId`) is the **primary execution observatory** for AgentVerse. When a goal is live it shows every event the agent emits in real time. When the goal is complete it becomes a structured post-mortem with eval scores, a replay of every persisted event, and quick links to advanced analysis views (DNA graph, diff comparison, ghost run).

It is intentionally designed as a one-stop-shop: you can monitor, pause, resume, cancel, approve, and evaluate a goal entirely from this single page.

---

## Page Architecture

```
┌──────────────────────────────────────────────────────┐
│ ← Back to goals                                      │
│ Goal text                      [Status badge] [$cost]│
├──────────────────────────────────────────────────────┤
│ [Cancel] [Pause/Resume] [Refresh]                    │
├──────────────────────────────────────────────────────┤
│ [HITL approval panel — conditional on waiting_human] │
├──────────────────────────────────────────────────────┤
│ Analysis: [View DNA] [Diff Run] [Ghost Run]          │
├──────────────────────────────────────────────────────┤
│ Tabs: Pipeline | Event Log | Eval (terminal only)    │
├──────────────────────────────────────────────────────┤
│ [Active Tab Content]                                 │
│   Pipeline: StepRows + ExecutionTimeline             │
│             + ToolCallInspector                      │
│   Event Log: persisted events replay                 │
│   Eval: 6-dimension scorecard                        │
└──────────────────────────────────────────────────────┘
```

Source: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx:254`

---

## Data Sources and Polling

The page uses two parallel data channels: **HTTP polling** for stable state and **SSE streaming** for live events.

### HTTP Polling

```tsx
// GoalDetailPage.tsx:150-155
const { data: goal, isLoading } = useQuery({
  queryKey: ["goal", goalId],
  queryFn: () => goalsApi.get(goalId!),
  refetchInterval: 5_000,
  enabled: !!goalId,
});
```

`GET /goals/:id` returns the full goal record including `status`, `cost_usd`, `agent_id`, `iterations`, and the current goal text. This is polled every 5 seconds unconditionally — it keeps the status badge, cost ticker, and control buttons in sync even when SSE events stop flowing.

### SSE Streaming

```tsx
// GoalDetailPage.tsx:157
const { events, connected } = useGoalStream(goalId ?? "");
```

The `useGoalStream` hook opens a persistent streaming connection to `GET /goals/:goalId/stream`. All `events` accumulate in state and are rendered as `StepRow` components in the Pipeline tab.

---

## SSE Hook: `useGoalStream`

The hook is implemented in `agent-verse-frontend/src/lib/sse/useGoalStream.ts`. Because native `EventSource` cannot send custom HTTP headers, the hook uses `fetch` with `ReadableStream` to pass the `X-API-Key` header:

```ts
// useGoalStream.ts:74-80
const res = await fetch(url, {
  headers: {
    "X-API-Key": apiKey,
    Accept: "text/event-stream",
  },
  signal: abort.signal,
});
```

### Reconnect and Backoff Algorithm

The hook retries on unexpected stream close or network error using **exponential backoff with a 30-second cap**:

```ts
// useGoalStream.ts:55-63
const scheduleReconnect = () => {
  if (retryCountRef.current >= 8) {
    setConnected(false);
    return;
  }
  const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
  retryCountRef.current += 1;
  retryTimerRef.current = setTimeout(() => startConnection(), delay);
};
```

Backoff schedule: 1s → 2s → 4s → 8s → 16s → 30s → 30s → 30s (8 attempts max).

### Terminal Event Detection

When the stream receives a terminal event, retries are suppressed:

```ts
// useGoalStream.ts:111-120
if (
  etype === "goal_complete" ||
  etype === "goal_failed"   ||
  etype === "goal_cancelled"
) {
  retryCountRef.current = 0; // Don't retry — goal is done
  terminalReceived = true;
  setConnected(false);
}
```

This prevents the hook from reconnecting and accumulating duplicate events after a goal finishes.

### Connection State Indicator

The Pipeline tab header shows a `● Live` (green) or `○ Disconnected` (muted) badge:

```tsx
// GoalDetailPage.tsx:463-465
<span className={`text-xs ${connected ? "text-green-500" : "text-muted-foreground"}`}>
  {connected ? "● Live" : "○ Disconnected"}
</span>
```

---

## SSE → Frontend: Full Event Processing Sequence

```mermaid
sequenceDiagram
    participant Agent as AgentGraph (backend)
    participant GoalSvc as GoalService
    participant FastAPI
    participant Hook as useGoalStream
    participant React as React State
    participant UI as Pipeline Tab

    Agent->>GoalSvc: emit_event({type: "step_started", step: "..."})
    GoalSvc->>GoalSvc: Append to GoalRecord.events
    GoalSvc->>GoalSvc: Fan-out to all subscriber queues

    FastAPI->>FastAPI: GET /goals/:id/stream (open connection)
    GoalSvc-->>FastAPI: yield event from subscriber queue
    FastAPI-->>Hook: data: {"type":"step_started","step":"..."}\n\n

    Hook->>Hook: buffer += decoder.decode(chunk)
    Hook->>Hook: Split on "\n\n" → frames
    Hook->>Hook: JSON.parse(frame)
    Hook->>React: setEvents(prev => [...prev, parsed])
    React->>UI: Re-render StepRow list
    UI->>UI: eventSummary(event) → {label, status, details}
    UI->>UI: Render CheckCircle/XCircle/Loader2 icon

    alt Terminal event received
        Hook->>Hook: retryCountRef.current = 0; terminalReceived = true
        Hook->>React: setConnected(false)
        FastAPI->>FastAPI: Close SSE stream
    else Network error
        Hook->>Hook: scheduleReconnect() — backoff timer
    end
```

---

## Pipeline Tab: StepRow Components

Each SSE event is rendered as a collapsible `StepRow`:

```tsx
// GoalDetailPage.tsx:111-139
function StepRow({ event }) {
  const [open, setOpen] = useState(false);
  const summary = eventSummary(event);
  const Icon = status === "complete" ? CheckCircle
             : status === "failed"   ? XCircle
             :                         Loader2;    // spinning for in-progress
  // ...
}
```

The `eventSummary()` function (`GoalDetailPage.tsx:53-109`) translates raw event types into human-readable labels:

| Event type | Label | Status |
|-----------|-------|--------|
| `goal_started` | "Goal started" | executing |
| `plan_ready` | "Plan ready" | complete |
| `step_started` | Step description | executing |
| `step_complete` | Step description | complete |
| `tool_call_complete` | "`{toolName}` succeeded/failed" | complete/failed |
| `tool_call_failed` | "`{toolName}` failed" | failed |
| `dry_run_preview` | "Dry run preview" | complete |
| `verification_done` | "Verification passed/failed" | complete/failed |

Clicking a row expands to show full detail: for `plan_ready`, the numbered step list; for `tool_call_complete`, the tool output and server ID; for `verification_done`, the success flag and reason.

---

## ExecutionTimeline Component

Rendered below the step list when at least one event exists:

```tsx
// GoalDetailPage.tsx:483
{events.length > 0 && <ExecutionTimeline events={events} />}
```

`ExecutionTimeline` (`src/components/execution/ExecutionTimeline.tsx`) renders a Gantt-style horizontal timeline of steps with their relative durations. Steps with timestamps are positioned proportionally; steps without timestamps are shown in sequence order. The timeline is primarily useful for identifying slow steps and tool latency outliers.

---

## ToolCallInspector Component

Rendered only when at least one `tool_call_complete` event exists:

```tsx
// GoalDetailPage.tsx:486-490
{events.some((e) => e.type === "tool_call_complete") && (
  <ToolCallInspector
    toolEvents={events.filter((e) => e.type === "tool_call_complete")}
  />
)}
```

`ToolCallInspector` (`src/components/execution/ToolCallInspector.tsx`) displays a table of all tool calls with:

- **Tool name** and server ID
- **Arguments** passed to the tool
- **Output** returned by the tool (truncated at 512 chars with expand)
- **Latency** (derived from event timestamps when available)
- **Risk level** — classified by the backend's `tool_risk.py` module based on keywords (`deploy`, `delete`, `prod`, etc.)

High-risk calls are highlighted in amber; failed calls in red.

---

## HITL Approval Panel

When a goal enters `waiting_human` status, an orange approval panel appears at the top of the page:

```tsx
// GoalDetailPage.tsx:319-392
{goal.status === "waiting_human" && (
  <div className="bg-orange-50 ...">
    <h2>Human approval required</h2>
    {/* Polling approvals every 3s */}
    {pendingApproval && (
      <>
        <p>Action: {pendingApproval.action}</p>
        <p>Risk level: {pendingApproval.risk_level}</p>
        <textarea value={approvalNote} ... />
        <button onClick={() => approveMutation.mutate()}>Approve</button>
        <button onClick={() => rejectMutation.mutate()}>Reject</button>
      </>
    )}
  </div>
)}
```

### How HITL Triggering Works

The `AgentGraph` detects high-risk operations via `_HIGH_RISK_KEYWORDS`:

```python
# agent-verse-backend/app/agent/graph.py:75-77
_HIGH_RISK_KEYWORDS = frozenset(
    ("deploy", "delete", "drop", "rm ", "prod", "production",
     "destroy", "wipe", "truncate")
)
```

When an executor tool call contains one of these keywords, the graph transitions to `waiting_human` and creates an approval request via `HITLGateway`. The agent loop suspends until the request is resolved.

### Approve / Reject Flow

```tsx
// GoalDetailPage.tsx:211-233
const approveMutation = useMutation({
  mutationFn: () => {
    const approverName = `user:${tenantId?.slice(0, 8) ?? "unknown"}`;
    return governanceApi.approve(pendingApproval.request_id, approverName, approvalNote);
  },
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["goal", goalId] });
    qc.invalidateQueries({ queryKey: ["approvals"] });
  },
});
```

Approvals poll at **3-second intervals** (`refetchInterval: 3_000`) while `goal.status === "waiting_human"` — faster than the normal 5-second poll to reduce perceived latency for the operator.

After approve/reject, both the goal and approvals query caches are invalidated, causing an immediate re-fetch that reflects the new goal status.

**API calls:**
- Approve: `POST /governance/approvals/:request_id/approve`
- Reject: `POST /governance/approvals/:request_id/reject`

---

## LiveCostTicker

The `LiveCostTicker` component (`src/components/live/LiveCostTicker.tsx`) shows a running cost estimate in the header:

```tsx
// GoalDetailPage.tsx:272-275
<LiveCostTicker
  currentCost={(goal as any).cost_usd ?? 0}
  isRunning={["planning", "executing", "verifying"].includes(goal.status)}
/>
```

When `isRunning` is true the ticker increments the displayed value smoothly using a JavaScript interval (cosmetic interpolation between polled `cost_usd` values). When the goal terminates, the ticker freezes at the final cost. The actual authoritative cost comes from `goal.cost_usd` in the polled `GET /goals/:id` response.

---

## Control Buttons: Pause, Resume, Cancel

### Cancel

Available when `status ∈ {executing, planning}`.

```tsx
// GoalDetailPage.tsx:283-288
const cancel = useMutation({
  mutationFn: () => goalsApi.cancel(goalId!),
  onSuccess: () => qc.invalidateQueries({ queryKey: ["goal", goalId] }),
});
```

API: `POST /goals/:id/cancel` → transitions goal to `CANCELLED`. Cancellation is immediate and non-reversible. The SSE stream emits `goal_cancelled` and closes.

### Pause

Available when `status === "executing"`.

```tsx
// GoalDetailPage.tsx:164-171
const pauseMutation = useMutation({
  mutationFn: () => goalsApi.pause(goalId!),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["goal", goalId] });
    toast({ kind: "success", message: "Goal paused." });
  },
});
```

API: `POST /goals/:id/pause` — sets a flag in `_GOAL_PAUSE_EVENTS[goal_id]` (a module-level `asyncio.Event`). The running agent loop checks this flag between steps and suspends execution. Status becomes `"paused"` (a transient state not in `GoalStatus` enum, returned as a raw string by the service layer).

Source: `agent-verse-backend/app/api/goals.py:380-391`

### Resume

Available when `status === "paused"`.

API: `POST /goals/:id/resume` — clears the pause event, allowing the loop to continue from where it left off.

Source: `agent-verse-backend/app/api/goals.py:394-405`

---

## Event Log Tab

```tsx
// GoalDetailPage.tsx:183-187
const { data: eventLog = [] } = useQuery({
  queryKey: ["goal-events", goalId],
  queryFn: () => goalsApi.getEventLog(goalId!),
  enabled: !!goalId && activeTab === "events",
});
```

The Event Log tab fetches **persisted** events from the database (not the live SSE stream). It is only populated after execution completes. Events are ordered chronologically and show `type`, `created_at` timestamp, and any `payload.message` from the event record.

This tab is the canonical replay source — if you missed the live stream or need to audit what happened, the Event Log is the authoritative record. Each event row displays:
- Wall-clock time (formatted as `HH:MM:SS`)
- Event type
- Message excerpt (from `event.payload.message`)

---

## Eval Tab (Scorecard)

The Eval tab appears only for terminal goals (`status ∈ {complete, failed}`):

```tsx
// GoalDetailPage.tsx:190-197
const { data: evaluation } = useQuery({
  queryKey: ["goal-eval", goalId],
  queryFn: () => goalsApi.getEvaluation(goalId!),
  enabled: !!goalId && activeTab === "eval"
    && ["complete", "failed"].includes(goal?.status ?? ""),
});
```

API: `GET /goals/:id/eval`

### Eval Score Dimensions

The `EvalRunner` (`app/intelligence/eval_runner.py`) scores completed goals on six criteria:

| Dimension | Description | What makes it pass |
|-----------|-------------|-------------------|
| `task_completion` | Did the agent accomplish the stated goal? | Verifier `success=true` + all plan steps completed |
| `efficiency` | Did it complete with minimal iterations and cost? | Below median cost and iteration count for similar goals |
| `accuracy` | Were tool outputs correct and coherent? | Tool results verified by the verifier LLM |
| `safety` | Were no dangerous side effects produced? | No unauthorized destructive tool calls |
| `coherence` | Was the plan logically sound? | No contradictory or redundant steps |
| `SLA` | Did it complete within expected time bounds? | Under configured SLA threshold |

Each criterion returns a `score` (0.0–1.0) and `passed` (bool). The overall score is the weighted average; `passed` is true when `score ≥ 0.7`.

### Rendered Scorecard

```tsx
// GoalDetailPage.tsx:540-578
<div className="text-3xl font-bold">
  {((evaluation.score ?? 0) * 100).toFixed(0)}%  {/* e.g. "84%" */}
</div>
<p>{evaluation.passed ? "PASSED" : "FAILED"}</p>
{evaluation.criteria.map((c) => (
  <div key={c.name}>
    <p>{c.name.replace(/_/g, " ")}</p>  {/* "task completion" */}
    <p>{(c.score * 100).toFixed(0)}%</p>
    <span>{c.passed ? "✓ pass" : "✗ fail"}</span>
  </div>
))}
```

### Example Eval API Response

```json
{
  "goal_id": "3f8c1a2b9d4e",
  "score": 0.84,
  "passed": true,
  "evaluated_at": "2026-06-29T10:15:32Z",
  "criteria": [
    { "name": "task_completion", "score": 0.95, "passed": true },
    { "name": "efficiency",      "score": 0.72, "passed": true },
    { "name": "accuracy",        "score": 0.88, "passed": true },
    { "name": "safety",          "score": 1.00, "passed": true },
    { "name": "coherence",       "score": 0.80, "passed": true },
    { "name": "SLA",             "score": 0.69, "passed": false }
  ]
}
```

---

## Analysis Toolbar: DNA, Diff, Ghost Run

```tsx
// GoalDetailPage.tsx:396-421
<button onClick={() => navigate(`/goals/${goalId}/dna`)}>
  <Dna /> View DNA
</button>
<button onClick={() => navigate(`/goals/${goalId}/diff`)}>
  <GitCompare /> Diff Run
</button>
<button onClick={() => navigate("/goals/ghost-run")}>
  <Ghost /> Ghost Run
</button>
```

- **View DNA** — Opens `GoalDNAPage`: a force-directed graph rendering the goal's lineage (parent→child sub-goals and spawned sub-agents). The API call is `GET /goals/:id/lineage`.
- **Diff Run** — Opens `GoalDiffPage`: side-by-side comparison of this goal's events against another goal run. Useful for regression testing agent changes.
- **Ghost Run** — Navigates to the Ghost Run page pre-loaded with this goal text, allowing immediate A/B comparison.

---

## Backend API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/goals/:id` | Fetch goal state; polled every 5s |
| `GET` | `/goals/:id/stream` | SSE stream of execution events |
| `GET` | `/goals/:id/eval` | Evaluation scorecard (terminal goals only) |
| `GET` | `/goals/:id/audit` | Audit trail entries for this goal |
| `GET` | `/goals/:id/traces` | Decision traces (LLM reasoning steps) |
| `GET` | `/goals/:id/lineage` | Parent→child spawn tree |
| `GET` | `/goals/:id/attempts` | Persistence attempt history |
| `POST` | `/goals/:id/cancel` | Cancel a running goal |
| `POST` | `/goals/:id/pause` | Pause a running goal |
| `POST` | `/goals/:id/resume` | Resume a paused goal |
| `POST` | `/goals/:id/approve` | Approve or reject a HITL request |

Source: `agent-verse-backend/app/api/goals.py:247-405`

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "● Live" shows but no events appear | SSE connected but goal is queued; agent loop not started | Check Celery worker is running; verify `REDIS_URL` |
| Events appear then stop mid-way | Worker process crashed | Check worker logs; goal may need to be resubmitted |
| HITL panel shows "Waiting for approval request to be registered" | Backend has not yet created the `ApprovalRequest` DB record | Wait 3–5s; the polling interval will pick it up |
| Eval tab shows "No evaluation" for a complete goal | `EvalRunner` failed silently or goal was auto-skipped | Check backend logs for `eval_runner_error`; eval is best-effort |
| Pause button absent on executing goal | Goal is running on inline asyncio (free tier), pause signal delivery race | The pause sets an asyncio.Event; if the loop is between steps it will catch it within one step cycle |
| Cost ticker frozen at $0 | Goal polled before cost is recorded | Cost is recorded at step completion; refresh after the first tool call |
| `"Goal not found"` | Goal was evicted from in-memory cache (1h TTL) after completion | Query the DB via `GET /goals/:id`; if the DB backed service is wired, it will fetch from Postgres |
