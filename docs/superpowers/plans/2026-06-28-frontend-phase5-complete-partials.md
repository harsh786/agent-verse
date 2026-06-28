# Frontend Phase 5 — Complete Partial Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the partially-implemented feature pages identified in the spec's WS-4 workstream: wire the unused typed-client methods for GoalDetail, expand KnowledgePage ingest sources, add SchedulesPage webhook config fields, pre-fill the ConnectorsCatalog register form, add advanced tabs to AgentDetailPage, and deliver eval-suite CRUD in EvalPage.

**Architecture:** Frontend-only changes in `agent-verse-frontend`. Every backend endpoint consumed already exists. This phase wires the six verified unused typed-client methods (`goalsApi.pause`, `goalsApi.resume`, `goalsApi.getEventLog`, `goalsApi.getEvaluation`, `governanceApi.getPendingApprovals` — already consumed, no change) and expands existing pages with new tabs, fields, and sub-sections. Shared primitives (`Skeleton`, `EmptyState`, `StatusBadge`, `FlowCanvas`) from Phase 1 are reused throughout. All mutations emit toasts from the Phase 1 store. Strict TDD; one commit per task.

**Tech Stack:** React 19, TypeScript (strict), Vite, TanStack Query 5, Zustand 5, Tailwind, recharts 2, vitest 3 + @testing-library/react, Playwright.

## Global Constraints

- **Frontend-only.** No backend files change in Phase 5.
- **No new dependencies** beyond what Phase 1 landed.
- **All backend calls go through the typed client** `@/lib/api/client` — no inline `fetch` in pages.
- **Verified backend paths (ground truth):** `POST /goals/{id}/pause`, `POST /goals/{id}/resume`, `GET /goals/{id}/events` (event log), `GET /goals/{id}/eval` (evaluation result), `GET /agents/{id}/permissions`, `POST /agents/{id}/clone`, `POST /agents/{id}/knowledge/{knowledgeId}` (assign), `DELETE /agents/{id}/knowledge/{knowledgeId}` (remove), `GET /agents/{id}/rollout-gate`.
- **Toast on every mutation error/success** via `toast()` from `@/stores/toast`.
- **Skeleton while loading, EmptyState when lists are empty, StatusBadge for all status fields** (primitives from Phase 1).
- **Commit style:** conventional commits; end every message with:
  `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`

---

## File Structure

**Modify:**
- `src/features/goals/GoalDetailPage.tsx` — add Eval Scorecard tab, Event Log (replay) tab, pause/resume buttons, Decision Traces tab.
- `src/features/knowledge/KnowledgePage.tsx` — add URL, GitHub, Confluence, Jira, Slack source types + per-type config fields.
- `src/features/schedules/SchedulesPage.tsx` — render webhook URL + secret fields when `triggerType === 'webhook'`.
- `src/features/connectors/ConnectorsCatalogPage.tsx` — pre-fill registration form from catalog item on Register click.
- `src/features/agents/AgentDetailPage.tsx` — add Credentials, Permissions, Clone, Knowledge Assignment, and Rollout Gate tabs.
- `src/features/eval/EvalPage.tsx` — add eval-suite CRUD section.
- `src/lib/api/client.ts` — add any missing typed methods (`agentsApi.getPermissions`, `agentsApi.clone`, `agentsApi.assignKnowledge`, `agentsApi.removeKnowledge`, `agentsApi.getRolloutGate`, `evalApi.listSuites`, `evalApi.createSuite`, `evalApi.getSuite`, `evalApi.addTask`, `evalApi.runSuite`, `evalApi.getSuiteResults`).

**Create:**
- `src/features/goals/GoalDetailPage.test.tsx` (expand existing or create new coverage for new tabs)
- `src/features/agents/AgentDetailPage.test.tsx` (create)
- `src/features/eval/EvalPage.test.tsx` (expand)
- `src/features/knowledge/KnowledgePage.test.tsx` (expand)

---

## Test harness reference (reuse from Phase 1)

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

function renderWithProviders(ui: React.ReactNode, route = '/') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true });
});
```

---

### Task 1: Add `pause`, `resume`, and `getEventLog` to GoalDetailPage

**Files:**
- Modify: `src/features/goals/GoalDetailPage.tsx`
- Test: `src/features/goals/GoalDetailPage.test.tsx`

**Interfaces:**
- Consumes: `goalsApi.pause(goalId)` → `POST /goals/{id}/pause`; `goalsApi.resume(goalId)` → `POST /goals/{id}/resume`; `goalsApi.getEventLog(goalId)` → `GET /goals/{id}/events` → `GoalEvent[]`.
- Produces: pause/resume buttons in the action bar (shown when goal status is `executing`/`paused`); "Event Log" tab rendering `GoalEvent[]` with `Skeleton` + `EmptyState`.

- [ ] **Step 1: Verify typed client has pause/resume/getEventLog**

Read `src/lib/api/client.ts` lines 120-135. Confirm `pause`, `resume`, `getEventLog` are defined. If any are missing, add them:

```ts
// in goalsApi object:
pause: (id: string) =>
  request<void>(`/goals/${id}/pause`, { method: 'POST' }),

resume: (id: string) =>
  request<void>(`/goals/${id}/resume`, { method: 'POST' }),

getEventLog: (id: string) =>
  request<GoalEvent[]>(`/goals/${id}/events`),
```

- [ ] **Step 2: Write the failing tests**

```tsx
// src/features/goals/GoalDetailPage.test.tsx (new tests to add)
test('shows pause button when goal is executing and calls pause API', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/goals/g1') && !url.includes('/events') && !url.includes('/eval'))
      return new Response(JSON.stringify({ id: 'g1', goal: 'do X', status: 'executing' }), { status: 200 });
    if (url.includes('/goals/g1/pause'))
      return new Response('null', { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<GoalDetailPage />, '/goals/g1');
  expect(await screen.findByRole('button', { name: /pause/i })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /pause/i }));
  await waitFor(() =>
    expect(vi.mocked(globalThis.fetch).mock.calls.some(([u]) => String(u).includes('/pause'))).toBe(true)
  );
});

test('renders Event Log tab with event entries', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/goals/g1/events'))
      return new Response(JSON.stringify([{ event_type: 'step_started', timestamp: '2026-01-01T00:00:00Z', data: {} }]), { status: 200 });
    return new Response(JSON.stringify({ id: 'g1', goal: 'do X', status: 'complete' }), { status: 200 });
  });
  renderWithProviders(<GoalDetailPage />, '/goals/g1');
  await userEvent.click(await screen.findByRole('tab', { name: /event log/i }));
  expect(await screen.findByText(/step_started/i)).toBeInTheDocument();
});
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd agent-verse-frontend && npm run test -- src/features/goals/GoalDetailPage.test.tsx
```

Expected: FAIL — pause button absent, event log tab absent.

- [ ] **Step 4: Implement in GoalDetailPage**

```tsx
// GoalDetailPage.tsx additions

import { goalsApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';

// In the action bar section, after the Cancel button:
{(goal.status === 'executing') && (
  <button
    onClick={() => pauseMutation.mutate()}
    disabled={pauseMutation.isPending}
    className="px-3 py-1.5 text-sm rounded-md border border-yellow-300 text-yellow-700 hover:bg-yellow-50 disabled:opacity-50"
    aria-label="Pause goal"
  >
    Pause
  </button>
)}
{(goal.status === 'paused') && (
  <button
    onClick={() => resumeMutation.mutate()}
    disabled={resumeMutation.isPending}
    className="px-3 py-1.5 text-sm rounded-md border border-green-300 text-green-700 hover:bg-green-50 disabled:opacity-50"
    aria-label="Resume goal"
  >
    Resume
  </button>
)}

// Mutations (add near cancel mutation):
const pauseMutation = useMutation({
  mutationFn: () => goalsApi.pause(goalId!),
  onSuccess: () => { qc.invalidateQueries({ queryKey: ['goal', goalId] }); toast({ kind: 'success', message: 'Goal paused.' }); },
  onError: (e) => toast({ kind: 'error', message: `Pause failed: ${e}` }),
});

const resumeMutation = useMutation({
  mutationFn: () => goalsApi.resume(goalId!),
  onSuccess: () => { qc.invalidateQueries({ queryKey: ['goal', goalId] }); toast({ kind: 'success', message: 'Goal resumed.' }); },
  onError: (e) => toast({ kind: 'error', message: `Resume failed: ${e}` }),
});

// Event Log tab query:
const { data: eventLog = [], isLoading: eventsLoading } = useQuery({
  queryKey: ['goal-events', goalId],
  queryFn: () => goalsApi.getEventLog(goalId!),
  enabled: !!goalId && activeTab === 'events',
});

// Add to tab list:
<button role="tab" onClick={() => setActiveTab('events')} aria-selected={activeTab === 'events'}>
  Event Log
</button>

// Add tab panel:
{activeTab === 'events' && (
  <div className="space-y-2">
    {eventsLoading
      ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)
      : eventLog.length === 0
        ? <EmptyState title="No events yet" description="Events appear as the goal executes." />
        : eventLog.map((ev, i) => (
            <div key={i} className="flex items-center gap-3 p-3 rounded-lg border bg-card text-sm">
              <span className="font-mono text-xs text-muted-foreground">{new Date(ev.timestamp).toLocaleTimeString()}</span>
              <span className="font-medium">{ev.event_type}</span>
              {ev.data?.message && <span className="text-muted-foreground">{String(ev.data.message)}</span>}
            </div>
          ))
    }
  </div>
)}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npm run test -- src/features/goals/GoalDetailPage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/features/goals/GoalDetailPage.tsx src/features/goals/GoalDetailPage.test.tsx src/lib/api/client.ts
git commit -m "feat(goal-detail): pause/resume buttons + Event Log tab

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Add Eval Scorecard tab to GoalDetailPage

**Files:**
- Modify: `src/features/goals/GoalDetailPage.tsx`
- Test: `src/features/goals/GoalDetailPage.test.tsx`

**Interfaces:**
- Consumes: `goalsApi.getEvaluation(goalId)` → `GET /goals/{id}/eval` → `GoalEvaluation` object with `overall_score`, `dimension_scores`, `passed`, `recommendations`.
- Produces: "Eval" tab (visible when goal status is `complete` or `failed`) rendering a score grid with recharts `RadarChart` or bar list.

- [ ] **Step 1: Write the failing test**

```tsx
test('renders Eval tab with overall score for completed goal', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/goals/g1/eval'))
      return new Response(JSON.stringify({
        overall_score: 0.87,
        passed: true,
        dimension_scores: { correctness: 0.9, efficiency: 0.84 },
        recommendations: [],
      }), { status: 200 });
    return new Response(JSON.stringify({ id: 'g1', goal: 'X', status: 'complete' }), { status: 200 });
  });
  renderWithProviders(<GoalDetailPage />, '/goals/g1');
  await userEvent.click(await screen.findByRole('tab', { name: /eval/i }));
  expect(await screen.findByText(/0\.87/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm run test -- src/features/goals/GoalDetailPage.test.tsx -t "Eval tab"
```

Expected: FAIL — eval tab absent.

- [ ] **Step 3: Implement**

```tsx
// Add getEvaluation query (enabled when tab active and goal is complete/failed):
const { data: evaluation, isLoading: evalLoading } = useQuery({
  queryKey: ['goal-eval', goalId],
  queryFn: () => goalsApi.getEvaluation(goalId!),
  enabled: !!goalId && activeTab === 'eval' && ['complete', 'failed'].includes(goal?.status ?? ''),
});

// Tab button (only shown for terminal states):
{['complete', 'failed'].includes(goal?.status ?? '') && (
  <button role="tab" onClick={() => setActiveTab('eval')} aria-selected={activeTab === 'eval'}>
    Eval
  </button>
)}

// Tab panel:
{activeTab === 'eval' && (
  <div className="space-y-4">
    {evalLoading
      ? <Skeleton className="h-40 w-full" />
      : !evaluation
        ? <EmptyState title="No evaluation" description="Evaluation runs after goal completion." />
        : (
          <div className="space-y-3">
            <div className="flex items-center gap-3 p-4 rounded-lg border bg-card">
              <div className="text-3xl font-bold text-foreground">
                {(evaluation.overall_score * 100).toFixed(0)}%
              </div>
              <div>
                <p className="text-sm font-medium">Overall Score</p>
                <p className={`text-xs ${evaluation.passed ? 'text-green-600' : 'text-red-600'}`}>
                  {evaluation.passed ? 'PASSED' : 'FAILED'}
                </p>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(evaluation.dimension_scores ?? {}).map(([dim, score]) => (
                <div key={dim} className="p-3 rounded border bg-card">
                  <p className="text-xs text-muted-foreground capitalize">{dim.replace(/_/g, ' ')}</p>
                  <p className="text-lg font-semibold">{((score as number) * 100).toFixed(0)}%</p>
                </div>
              ))}
            </div>
            {(evaluation.recommendations ?? []).length > 0 && (
              <div className="p-3 rounded border bg-card">
                <p className="text-xs font-medium mb-1 text-muted-foreground">Recommendations</p>
                <ul className="list-disc pl-4 text-sm space-y-1">
                  {evaluation.recommendations.map((r: string, i: number) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )
    }
  </div>
)}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npm run test -- src/features/goals/GoalDetailPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/goals/GoalDetailPage.tsx src/features/goals/GoalDetailPage.test.tsx
git commit -m "feat(goal-detail): eval scorecard tab with dimension scores

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Knowledge ingest source expansion

**Files:**
- Modify: `src/features/knowledge/KnowledgePage.tsx`
- Test: `src/features/knowledge/KnowledgePage.test.tsx`

**Context:** `KnowledgePage.tsx:220` has `SOURCE_TYPES = ['text','markdown','git','openapi']`. Spec requires: URL, PDF, DOCX, GitHub, Confluence, Jira, Slack. Each needs per-type config fields shown/hidden based on selection.

**Interfaces:**
- Consumes: existing `knowledgeApi.ingest` (POST /knowledge/ingest) with extended `source_config` object.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/knowledge/KnowledgePage.test.tsx
test('shows URL field when URL source type is selected', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response('[]', { status: 200 })
  );
  renderWithProviders(<KnowledgePage />);
  const select = await screen.findByLabelText(/source type/i);
  await userEvent.selectOptions(select, 'url');
  expect(screen.getByLabelText(/url to crawl/i)).toBeInTheDocument();
});

test('shows Confluence fields when Confluence source type is selected', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
  renderWithProviders(<KnowledgePage />);
  const select = await screen.findByLabelText(/source type/i);
  await userEvent.selectOptions(select, 'confluence');
  expect(screen.getByLabelText(/confluence url/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/space key/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npm run test -- src/features/knowledge/KnowledgePage.test.tsx -t "URL field"
```

Expected: FAIL.

- [ ] **Step 3: Expand SOURCE_TYPES and add conditional config fields**

```tsx
// src/features/knowledge/KnowledgePage.tsx

const SOURCE_TYPES = [
  { value: 'text', label: 'Plain Text' },
  { value: 'markdown', label: 'Markdown' },
  { value: 'url', label: 'URL / Web Page' },
  { value: 'pdf', label: 'PDF File' },
  { value: 'docx', label: 'Word Document (.docx)' },
  { value: 'git', label: 'Git Repository' },
  { value: 'github', label: 'GitHub (repo/issues/PRs)' },
  { value: 'openapi', label: 'OpenAPI Schema' },
  { value: 'confluence', label: 'Confluence' },
  { value: 'jira', label: 'Jira' },
  { value: 'slack', label: 'Slack' },
];

// Per-type config fields component:
function SourceConfigFields({
  sourceType,
  config,
  onChange,
}: {
  sourceType: string;
  config: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  switch (sourceType) {
    case 'url':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            URL to crawl
            <input
              aria-label="url to crawl"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="https://docs.example.com"
              value={config.url ?? ''}
              onChange={(e) => onChange('url', e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            Max depth
            <input
              type="number"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="3"
              value={config.max_depth ?? ''}
              onChange={(e) => onChange('max_depth', e.target.value)}
            />
          </label>
        </div>
      );
    case 'github':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            Repository (owner/repo)
            <input className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="acme/my-repo"
              value={config.repo ?? ''}
              onChange={(e) => onChange('repo', e.target.value)} />
          </label>
          <label className="block text-sm font-medium">
            Include (issues, prs, code — comma-separated)
            <input className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="code,issues"
              value={config.include ?? ''}
              onChange={(e) => onChange('include', e.target.value)} />
          </label>
        </div>
      );
    case 'confluence':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            Confluence URL
            <input aria-label="confluence url"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="https://myorg.atlassian.net/wiki"
              value={config.base_url ?? ''}
              onChange={(e) => onChange('base_url', e.target.value)} />
          </label>
          <label className="block text-sm font-medium">
            Space key
            <input aria-label="space key"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="ENG"
              value={config.space_key ?? ''}
              onChange={(e) => onChange('space_key', e.target.value)} />
          </label>
          <label className="block text-sm font-medium">
            API token
            <input type="password" className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              value={config.api_token ?? ''}
              onChange={(e) => onChange('api_token', e.target.value)} />
          </label>
        </div>
      );
    case 'jira':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            Jira URL
            <input className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="https://myorg.atlassian.net"
              value={config.base_url ?? ''}
              onChange={(e) => onChange('base_url', e.target.value)} />
          </label>
          <label className="block text-sm font-medium">
            Project key
            <input className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="ENG"
              value={config.project_key ?? ''}
              onChange={(e) => onChange('project_key', e.target.value)} />
          </label>
          <label className="block text-sm font-medium">
            API token
            <input type="password" className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              value={config.api_token ?? ''}
              onChange={(e) => onChange('api_token', e.target.value)} />
          </label>
        </div>
      );
    case 'slack':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            Bot token
            <input type="password" className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              value={config.bot_token ?? ''}
              onChange={(e) => onChange('bot_token', e.target.value)} />
          </label>
          <label className="block text-sm font-medium">
            Channels (comma-separated)
            <input className="mt-1 block w-full rounded border px-3 py-2 text-sm"
              placeholder="#general,#eng"
              value={config.channels ?? ''}
              onChange={(e) => onChange('channels', e.target.value)} />
          </label>
        </div>
      );
    default:
      return null;
  }
}
```

Wire `sourceConfig` state and `SourceConfigFields` into the existing ingest form. Pass the built `source_config` in the `knowledgeApi.ingest` call.

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/features/knowledge/KnowledgePage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/knowledge/KnowledgePage.tsx src/features/knowledge/KnowledgePage.test.tsx
git commit -m "feat(knowledge): expand ingest source types (URL, GitHub, Confluence, Jira, Slack)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 4: SchedulesPage webhook config fields

**Files:**
- Modify: `src/features/schedules/SchedulesPage.tsx`
- Test: `src/features/schedules/SchedulesPage.test.tsx`

**Context:** `SchedulesPage.tsx:270-296` renders cron/interval config but nothing when `triggerType === 'webhook'`. Add webhook URL display and optional secret.

**Interfaces:**
- Produces: When `triggerType === 'webhook'` is selected in the create form: show a read-only generated webhook endpoint URL (e.g. `{API_BASE}/webhooks/{tenantId}/trigger`) and an optional secret field. On save, pass `{ webhook_secret: string }` in `source_config`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/schedules/SchedulesPage.test.tsx — add:
test('shows webhook URL and secret fields when webhook trigger type is selected', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
  renderWithProviders(<SchedulesPage />);
  // Open create form
  await userEvent.click(await screen.findByRole('button', { name: /create|new schedule/i }));
  const triggerSelect = screen.getByLabelText(/trigger type/i);
  await userEvent.selectOptions(triggerSelect, 'webhook');
  expect(screen.getByText(/webhook.*url|webhook endpoint/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/webhook secret/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm run test -- src/features/schedules/SchedulesPage.test.tsx -t "webhook URL"
```

Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// In the conditional block inside the schedule create/edit form
// (after the existing cron/interval conditionals at line ~270-296):

{triggerType === 'webhook' && (
  <div className="space-y-3 p-3 rounded-lg bg-muted/50 border">
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-1">Webhook endpoint URL</p>
      <div className="flex items-center gap-2">
        <code className="flex-1 text-xs bg-card border rounded px-2 py-1.5 text-foreground break-all">
          {`${API_BASE}/webhooks/${tenantId}/trigger`}
        </code>
        <button
          type="button"
          onClick={() => navigator.clipboard.writeText(`${API_BASE}/webhooks/${tenantId}/trigger`)}
          aria-label="Copy webhook URL"
          className="px-2 py-1.5 text-xs border rounded hover:bg-muted"
        >
          Copy
        </button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        POST to this URL to fire the schedule. Pass <code>X-Webhook-Secret</code> header.
      </p>
    </div>
    <label className="block text-sm font-medium">
      Webhook secret (optional)
      <input
        aria-label="Webhook secret"
        type="password"
        className="mt-1 block w-full rounded border px-3 py-2 text-sm"
        placeholder="Leave blank to auto-generate"
        value={webhookSecret}
        onChange={(e) => setWebhookSecret(e.target.value)}
      />
    </label>
  </div>
)}
```

Add `webhookSecret` state (`useState('')`) and include it in the schedule creation payload's `source_config` when trigger type is webhook.

- [ ] **Step 4: Run test to verify it passes**

```bash
npm run test -- src/features/schedules/SchedulesPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/schedules/SchedulesPage.tsx src/features/schedules/SchedulesPage.test.tsx
git commit -m "feat(schedules): add webhook URL + secret config fields for webhook trigger type

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 5: ConnectorsCatalog pre-fills the registration form

**Files:**
- Modify: `src/features/connectors/ConnectorsCatalogPage.tsx`
- Test: `src/features/connectors/ConnectorsCatalogPage.test.tsx`

**Context:** `ConnectorsCatalogPage.tsx:85` — Register button navigates to registration without passing the catalog item. Fix: either (a) navigate with state, or (b) show an inline quick-register modal pre-filled from the catalog item.

**Interfaces:**
- Consumes: React Router `useNavigate` with state; `ConnectorsRegisteredPage` reads `useLocation().state` to pre-fill.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/connectors/ConnectorsCatalogPage.test.tsx — add:
test('Register button pre-fills navigation state from catalog item', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([
      { connector_type: 'github', name: 'GitHub', description: 'GitHub connector', auth_type: 'oauth2', schema: {} }
    ]), { status: 200 })
  );
  const navigateSpy = vi.fn();
  vi.mock('react-router-dom', async (imp) => {
    const mod = await imp() as Record<string, unknown>;
    return { ...mod, useNavigate: () => navigateSpy };
  });
  renderWithProviders(<ConnectorsCatalogPage />);
  await userEvent.click(await screen.findByRole('button', { name: /register/i }));
  expect(navigateSpy).toHaveBeenCalledWith(
    expect.stringContaining('/connectors'),
    expect.objectContaining({ state: expect.objectContaining({ connector_type: 'github' }) })
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm run test -- src/features/connectors/ConnectorsCatalogPage.test.tsx -t "pre-fills"
```

Expected: FAIL.

- [ ] **Step 3: Implement**

Replace the current Register navigation call:

```tsx
// Before (line ~85):
onClick={() => navigate('/connectors')}

// After:
onClick={() =>
  navigate('/connectors', {
    state: {
      prefill: {
        connector_type: item.connector_type,
        name: item.name,
        auth_type: item.auth_type,
        config_schema: item.schema,
      },
    },
  })
}
```

In `ConnectorsRegisteredPage.tsx`, read `useLocation().state?.prefill` and use it to pre-populate the registration form fields on mount:

```tsx
import { useLocation } from 'react-router-dom';

const location = useLocation();
const prefill = (location.state as { prefill?: Record<string, unknown> } | null)?.prefill;

useEffect(() => {
  if (prefill) {
    setForm((f) => ({
      ...f,
      connector_type: String(prefill.connector_type ?? f.connector_type),
      name: String(prefill.name ?? f.name),
    }));
  }
}, [prefill]);
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/features/connectors
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/connectors/ConnectorsCatalogPage.tsx src/features/connectors/ConnectorsRegisteredPage.tsx src/features/connectors/ConnectorsCatalogPage.test.tsx
git commit -m "feat(connectors): catalog Register button pre-fills registration form via router state

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 6: AgentDetailPage — Credentials, Permissions, Clone, Knowledge Assignment, Rollout Gate tabs

**Files:**
- Modify: `src/features/agents/AgentDetailPage.tsx`
- Modify: `src/lib/api/client.ts` — add missing `agentsApi` methods
- Test: `src/features/agents/AgentDetailPage.test.tsx` (create)

**Interfaces:**
- Consumes:
  - `agentsApi.getPermissions(id)` → `GET /agents/{id}/permissions` → `{ read: string[]; write: string[] }`
  - `agentsApi.clone(id)` → `POST /agents/{id}/clone` → `AgentResponse`
  - `agentsApi.assignKnowledge(agentId, knowledgeId)` → `POST /agents/{id}/knowledge/{knowledgeId}` → `void`
  - `agentsApi.removeKnowledge(agentId, knowledgeId)` → `DELETE /agents/{id}/knowledge/{knowledgeId}` → `void`
  - `agentsApi.getRolloutGate(id)` → `GET /agents/{id}/rollout-gate` → `{ gate_status: string; traffic_pct: number; conditions: string[] }`
  - `knowledgeApi.list()` (existing) — for knowledge assignment selector

- [ ] **Step 1: Add typed client methods**

```ts
// In src/lib/api/client.ts, agentsApi object, after existing methods:

getPermissions: (id: string) =>
  request<{ read: string[]; write: string[] }>(`/agents/${id}/permissions`),

clone: (id: string) =>
  request<AgentResponse>(`/agents/${id}/clone`, { method: 'POST' }),

assignKnowledge: (agentId: string, knowledgeId: string) =>
  request<void>(`/agents/${agentId}/knowledge/${knowledgeId}`, { method: 'POST' }),

removeKnowledge: (agentId: string, knowledgeId: string) =>
  request<void>(`/agents/${agentId}/knowledge/${knowledgeId}`, { method: 'DELETE' }),

getRolloutGate: (id: string) =>
  request<{ gate_status: string; traffic_pct: number; conditions: string[] }>(`/agents/${id}/rollout-gate`),
```

- [ ] **Step 2: Write failing tests**

```tsx
// src/features/agents/AgentDetailPage.test.tsx
test('Permissions tab shows read/write scopes', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/permissions'))
      return new Response(JSON.stringify({ read: ['goals:read'], write: ['goals:write'] }), { status: 200 });
    return new Response(JSON.stringify({ agent_id: 'a1', name: 'Bot', autonomy_mode: 'supervised' }), { status: 200 });
  });
  renderWithProviders(<AgentDetailPage />, '/agents/a1');
  await userEvent.click(await screen.findByRole('tab', { name: /permissions/i }));
  expect(await screen.findByText(/goals:read/i)).toBeInTheDocument();
});

test('Clone button navigates to new agent', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/clone'))
      return new Response(JSON.stringify({ agent_id: 'a2', name: 'Bot (clone)', autonomy_mode: 'supervised' }), { status: 200 });
    return new Response(JSON.stringify({ agent_id: 'a1', name: 'Bot', autonomy_mode: 'supervised' }), { status: 200 });
  });
  renderWithProviders(<AgentDetailPage />, '/agents/a1');
  const cloneBtn = await screen.findByRole('button', { name: /clone/i });
  await userEvent.click(cloneBtn);
  await waitFor(() => expect(window.location.pathname).toContain('a2'));
});
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
npm run test -- src/features/agents/AgentDetailPage.test.tsx
```

Expected: FAIL.

- [ ] **Step 4: Implement new tabs in AgentDetailPage**

Add five new tabs to the existing tab bar (after the existing tabs):

```tsx
// Tab buttons (add to existing tab bar):
<button role="tab" onClick={() => setTab('permissions')} aria-selected={tab === 'permissions'}>Permissions</button>
<button role="tab" onClick={() => setTab('knowledge')} aria-selected={tab === 'knowledge'}>Knowledge</button>
<button role="tab" onClick={() => setTab('credentials')} aria-selected={tab === 'credentials'}>Credentials</button>
<button role="tab" onClick={() => setTab('rollout')} aria-selected={tab === 'rollout'}>Rollout Gate</button>

// Clone button in the action bar:
<button
  onClick={() => cloneMutation.mutate()}
  disabled={cloneMutation.isPending}
  aria-label="Clone agent"
  className="px-3 py-1.5 text-sm rounded-md border hover:bg-muted"
>
  {cloneMutation.isPending ? 'Cloning…' : 'Clone'}
</button>

// Queries:
const { data: permissions, isLoading: permsLoading } = useQuery({
  queryKey: ['agent-permissions', agentId],
  queryFn: () => agentsApi.getPermissions(agentId!),
  enabled: !!agentId && tab === 'permissions',
});

const { data: rolloutGate, isLoading: rolloutLoading } = useQuery({
  queryKey: ['agent-rollout', agentId],
  queryFn: () => agentsApi.getRolloutGate(agentId!),
  enabled: !!agentId && tab === 'rollout',
});

const { data: allKnowledge = [] } = useQuery({
  queryKey: ['knowledge'],
  queryFn: () => knowledgeApi.list(),
  enabled: tab === 'knowledge',
});

// Mutations:
const cloneMutation = useMutation({
  mutationFn: () => agentsApi.clone(agentId!),
  onSuccess: (data) => { navigate(`/agents/${data.agent_id}`); toast({ kind: 'success', message: 'Agent cloned.' }); },
  onError: (e) => toast({ kind: 'error', message: `Clone failed: ${e}` }),
});

const assignKnowledgeMutation = useMutation({
  mutationFn: (kId: string) => agentsApi.assignKnowledge(agentId!, kId),
  onSuccess: () => { qc.invalidateQueries({ queryKey: ['agent', agentId] }); toast({ kind: 'success', message: 'Knowledge assigned.' }); },
});

const removeKnowledgeMutation = useMutation({
  mutationFn: (kId: string) => agentsApi.removeKnowledge(agentId!, kId),
  onSuccess: () => { qc.invalidateQueries({ queryKey: ['agent', agentId] }); toast({ kind: 'success', message: 'Knowledge removed.' }); },
});

// Tab panels:
{tab === 'permissions' && (
  <div className="space-y-4">
    {permsLoading
      ? <Skeleton className="h-24 w-full" />
      : !permissions
        ? <EmptyState title="No permissions configured" />
        : (
          <div className="grid grid-cols-2 gap-4">
            <div className="p-3 rounded-lg border bg-card">
              <p className="text-xs font-medium text-muted-foreground mb-2">Read scopes</p>
              <ul className="space-y-1">
                {permissions.read.map((s) => (
                  <li key={s} className="text-sm font-mono bg-muted rounded px-2 py-0.5">{s}</li>
                ))}
              </ul>
            </div>
            <div className="p-3 rounded-lg border bg-card">
              <p className="text-xs font-medium text-muted-foreground mb-2">Write scopes</p>
              <ul className="space-y-1">
                {permissions.write.map((s) => (
                  <li key={s} className="text-sm font-mono bg-muted rounded px-2 py-0.5">{s}</li>
                ))}
              </ul>
            </div>
          </div>
        )
    }
  </div>
)}

{tab === 'rollout' && (
  <div className="space-y-3">
    {rolloutLoading
      ? <Skeleton className="h-24 w-full" />
      : !rolloutGate
        ? <EmptyState title="No rollout gate configured" description="Rollout gates control traffic steering to this agent version." />
        : (
          <div className="p-4 rounded-lg border bg-card space-y-2">
            <div className="flex items-center gap-3">
              <StatusBadge status={rolloutGate.gate_status} />
              <span className="text-sm font-medium">{rolloutGate.traffic_pct}% traffic</span>
            </div>
            {rolloutGate.conditions.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground mb-1">Conditions</p>
                <ul className="list-disc pl-4 text-sm space-y-1">
                  {rolloutGate.conditions.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              </div>
            )}
          </div>
        )
    }
  </div>
)}

{tab === 'knowledge' && (
  <div className="space-y-3">
    <p className="text-sm text-muted-foreground">Assign knowledge collections this agent can retrieve from.</p>
    {allKnowledge.length === 0
      ? <EmptyState title="No knowledge collections" description="Create a collection in the Knowledge page first." />
      : (
        <div className="divide-y border rounded-lg overflow-hidden">
          {allKnowledge.map((k: { collection_id: string; name: string }) => (
            <div key={k.collection_id} className="flex items-center justify-between p-3 bg-card">
              <span className="text-sm font-medium">{k.name}</span>
              <div className="flex gap-2">
                <button
                  onClick={() => assignKnowledgeMutation.mutate(k.collection_id)}
                  className="text-xs px-2 py-1 rounded border hover:bg-muted"
                >Assign</button>
                <button
                  onClick={() => removeKnowledgeMutation.mutate(k.collection_id)}
                  className="text-xs px-2 py-1 rounded border text-red-600 hover:bg-red-50"
                >Remove</button>
              </div>
            </div>
          ))}
        </div>
      )
    }
  </div>
)}

{tab === 'credentials' && (
  <EmptyState
    title="Connector credentials"
    description="Credentials for MCP connectors used by this agent are managed in the Connectors page."
    action={<a href="/connectors" className="text-sm text-primary underline">Go to Connectors</a>}
  />
)}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npm run test -- src/features/agents/AgentDetailPage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/api/client.ts src/features/agents/AgentDetailPage.tsx src/features/agents/AgentDetailPage.test.tsx
git commit -m "feat(agent-detail): Permissions, Knowledge Assignment, Clone, Rollout Gate tabs

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 7: EvalPage — eval-suite CRUD section

**Files:**
- Modify: `src/features/eval/EvalPage.tsx`
- Modify: `src/lib/api/client.ts` — add `evalApi` suite methods
- Test: `src/features/eval/EvalPage.test.tsx`

**Interfaces:**
- Consumes:
  - `evalApi.listSuites()` → `GET /eval/suites` → `EvalSuite[]`
  - `evalApi.createSuite(name, description)` → `POST /eval/suites` → `EvalSuite`
  - `evalApi.getSuite(id)` → `GET /eval/suites/{id}` → `EvalSuite` with `tasks[]`
  - `evalApi.addTask(suiteId, { input, expected_output, tags })` → `POST /eval/suites/{id}/tasks`
  - `evalApi.runSuite(id)` → `POST /eval/suites/{id}/run` → `{ run_id: string }`
  - `evalApi.getSuiteResults(id)` → `GET /eval/suites/{id}/results` → `EvalSuiteResult[]`

- [ ] **Step 1: Add typed client methods**

```ts
// In src/lib/api/client.ts, extend or add evalApi object:

export const evalApi = {
  // ... existing methods ...

  listSuites: () =>
    request<EvalSuite[]>('/eval/suites'),

  createSuite: (name: string, description?: string) =>
    request<EvalSuite>('/eval/suites', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    }),

  getSuite: (id: string) =>
    request<EvalSuite>(`/eval/suites/${id}`),

  addTask: (suiteId: string, task: { input: string; expected_output?: string; tags?: string[] }) =>
    request<void>(`/eval/suites/${suiteId}/tasks`, {
      method: 'POST',
      body: JSON.stringify(task),
    }),

  runSuite: (id: string) =>
    request<{ run_id: string }>(`/eval/suites/${id}/run`, { method: 'POST' }),

  getSuiteResults: (id: string) =>
    request<EvalSuiteResult[]>(`/eval/suites/${id}/results`),
};

// Add type definitions if not present:
export interface EvalSuite {
  suite_id: string;
  name: string;
  description?: string;
  task_count: number;
  created_at: string;
}
export interface EvalSuiteResult {
  run_id: string;
  suite_id: string;
  overall_score: number;
  passed: number;
  failed: number;
  completed_at: string;
}
```

- [ ] **Step 2: Write failing tests**

```tsx
// src/features/eval/EvalPage.test.tsx — add:
test('Suites tab lists eval suites', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/eval/suites'))
      return new Response(JSON.stringify([
        { suite_id: 's1', name: 'Regression Suite', task_count: 5, created_at: '2026-01-01T00:00:00Z' }
      ]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<EvalPage />);
  await userEvent.click(await screen.findByRole('tab', { name: /suites/i }));
  expect(await screen.findByText('Regression Suite')).toBeInTheDocument();
});

test('can create a new suite', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/eval/suites') && (init as RequestInit)?.method === 'POST')
      return new Response(JSON.stringify({ suite_id: 's2', name: 'New Suite', task_count: 0, created_at: '' }), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<EvalPage />);
  await userEvent.click(await screen.findByRole('tab', { name: /suites/i }));
  await userEvent.click(screen.getByRole('button', { name: /create suite/i }));
  await userEvent.type(screen.getByLabelText(/suite name/i), 'New Suite');
  await userEvent.click(screen.getByRole('button', { name: /save|create/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u, i]) => String(u).includes('/eval/suites') && (i as RequestInit)?.method === 'POST')).toBe(true)
  );
});
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
npm run test -- src/features/eval/EvalPage.test.tsx -t "Suites tab"
```

Expected: FAIL.

- [ ] **Step 4: Implement suite CRUD section in EvalPage**

Add a "Suites" tab to the existing EvalPage tab bar. Tab panel renders:

```tsx
// Add to tab list:
<button role="tab" onClick={() => setTab('suites')} aria-selected={tab === 'suites'}>Suites</button>

// Queries and mutations:
const { data: suites = [], isLoading: suitesLoading } = useQuery({
  queryKey: ['eval-suites'],
  queryFn: () => evalApi.listSuites(),
  enabled: tab === 'suites',
});

const [showCreateSuite, setShowCreateSuite] = useState(false);
const [suiteName, setSuiteName] = useState('');
const [suiteDesc, setSuiteDesc] = useState('');
const [selectedSuiteId, setSelectedSuiteId] = useState<string | null>(null);

const createSuiteMutation = useMutation({
  mutationFn: () => evalApi.createSuite(suiteName, suiteDesc),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: ['eval-suites'] });
    setShowCreateSuite(false);
    setSuiteName('');
    setSuiteDesc('');
    toast({ kind: 'success', message: 'Suite created.' });
  },
  onError: (e) => toast({ kind: 'error', message: `Create failed: ${e}` }),
});

const runSuiteMutation = useMutation({
  mutationFn: (id: string) => evalApi.runSuite(id),
  onSuccess: () => toast({ kind: 'success', message: 'Suite run started.' }),
  onError: (e) => toast({ kind: 'error', message: `Run failed: ${e}` }),
});

// Tab panel:
{tab === 'suites' && (
  <div className="space-y-4">
    <div className="flex items-center justify-between">
      <h3 className="text-sm font-semibold text-foreground">Eval Suites</h3>
      <button
        onClick={() => setShowCreateSuite(true)}
        aria-label="Create suite"
        className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
      >
        + Create suite
      </button>
    </div>

    {showCreateSuite && (
      <div className="p-4 rounded-lg border bg-card space-y-3">
        <label className="block text-sm font-medium">
          Suite name
          <input
            aria-label="Suite name"
            className="mt-1 block w-full rounded border px-3 py-2 text-sm"
            value={suiteName}
            onChange={(e) => setSuiteName(e.target.value)}
          />
        </label>
        <label className="block text-sm font-medium">
          Description
          <input
            className="mt-1 block w-full rounded border px-3 py-2 text-sm"
            value={suiteDesc}
            onChange={(e) => setSuiteDesc(e.target.value)}
          />
        </label>
        <div className="flex gap-2">
          <button
            onClick={() => createSuiteMutation.mutate()}
            disabled={createSuiteMutation.isPending || !suiteName.trim()}
            className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground disabled:opacity-50"
          >
            {createSuiteMutation.isPending ? 'Creating…' : 'Create'}
          </button>
          <button onClick={() => setShowCreateSuite(false)} className="px-3 py-1.5 text-sm rounded-md border">
            Cancel
          </button>
        </div>
      </div>
    )}

    {suitesLoading
      ? <Skeleton className="h-24 w-full" />
      : suites.length === 0
        ? <EmptyState title="No eval suites" description="Create a suite to group golden tasks and track regressions." />
        : (
          <div className="divide-y border rounded-lg overflow-hidden">
            {suites.map((suite) => (
              <div key={suite.suite_id} className="flex items-center justify-between p-3 bg-card">
                <div>
                  <p className="text-sm font-medium">{suite.name}</p>
                  <p className="text-xs text-muted-foreground">{suite.task_count} tasks · created {new Date(suite.created_at).toLocaleDateString()}</p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => setSelectedSuiteId(suite.suite_id)}
                    className="text-xs px-2 py-1 rounded border hover:bg-muted"
                  >View</button>
                  <button
                    onClick={() => runSuiteMutation.mutate(suite.suite_id)}
                    disabled={runSuiteMutation.isPending}
                    className="text-xs px-2 py-1 rounded border text-green-700 hover:bg-green-50 disabled:opacity-50"
                  >Run</button>
                </div>
              </div>
            ))}
          </div>
        )
    }
  </div>
)}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npm run test -- src/features/eval/EvalPage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/api/client.ts src/features/eval/EvalPage.tsx src/features/eval/EvalPage.test.tsx
git commit -m "feat(eval): eval-suite CRUD — list, create, run suites

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Phase-5 regression gate

- [ ] **Step 1: Run full unit suite**

```bash
cd agent-verse-frontend && npm run test
```

Expected: all existing tests pass; coverage not decreased.

- [ ] **Step 2: Lint**

```bash
npm run lint
```

Expected: no new errors (pre-existing warnings OK).

- [ ] **Step 3: Typecheck**

```bash
npm run typecheck
```

Expected: no new errors beyond pre-existing.

- [ ] **Step 4: E2E smoke**

```bash
npm run test:e2e -- e2e/goals.spec.ts e2e/navigation.spec.ts
```

Expected: PASS.

- [ ] **Step 5: Tag**

```bash
git tag -a frontend-phase5 -m "Frontend Phase 5: complete partial pages"
```

---

## Self-Review

**Spec coverage (against WS-4 / P1-13 / P1-14 / P1-15 / P2-8):**
- P1-14 (GoalDetail eval scorecard + replay + pause/resume) → Tasks 1, 2. ✅
- P1-13 (Knowledge ingest depth) → Task 3. ✅
- P1-15 (Webhook config fields) → Task 4. ✅
- ConnectorsCatalog Register pre-fill → Task 5. ✅
- P2-8 (AgentDetail credentials/permissions/clone/knowledge/rollout) → Task 6. ✅
- Eval-suite CRUD → Task 7. ✅

**Unused typed-client methods wired:** `goalsApi.pause`, `goalsApi.resume`, `goalsApi.getEventLog`, `goalsApi.getEvaluation`. ✅

**Placeholder scan:** none — all code blocks are complete; all run steps have exact commands + expected results.

---

## Execution Handoff

Phase 5 completes the partial-page debt. Phase 6 (Workflow Builder) depends on Phase 1's `FlowCanvas` and should be started immediately after Phase 5 ships. Phase 7 (entity detail pages) can proceed in parallel with Phase 6 as the backend work in Phase 6 is self-contained.
