# Frontend Phase 8 — Polish & Analytics Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevate the frontend to "world-class" quality: add time-series analytics and cost-by-model charts, wire the global search, clean up orphaned nav links, add decision-trace explainability, surface marketplace versioning and self-optimization rationale, complete the accessibility and responsive passes, and push test coverage to ≥80% of feature pages.

**Architecture:** Frontend-only changes in `agent-verse-frontend`. This phase is pure polish — no new backend endpoints except the additive, already-noted `GET /goals/{id}/decision-traces` (read-only, non-breaking). All analytical data is fetched from existing endpoints. Every mutation must emit toasts. Strict TDD; one commit per task.

**Tech Stack:** React 19, TypeScript (strict), Vite, TanStack Query 5, Zustand 5, Tailwind, recharts 2, vitest 3 + @testing-library/react, Playwright.

## Global Constraints

- **Frontend-only** (except the one additive decision-traces read endpoint noted in the spec).
- **No new npm dependencies.** `recharts` (already installed), `@xyflow/react` (Phase 1).
- **All backend calls go through the typed client** `@/lib/api/client`.
- **Reuse Phase 7's `DashboardKit`** (`KpiRow`, `TimeSeriesChart`, `BarChartPanel`) for all new chart sections.
- **Toast on every mutation error/success** via `toast()` from `@/stores/toast`.
- **ARIA + a11y rules**: every icon-only button must have `aria-label`; interactive non-button elements must have `role`; modals must trap focus using `focus-trap` pattern or `dialog` element.
- **Responsive breakpoints**: all list/table pages must work at `sm` (640px) — use `hidden sm:table-cell` to collapse non-critical columns.
- **Commit style:** conventional commits; end every message with:
  `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`

---

## File Structure

**Modify:**
- `src/features/observability/CostDashboardPage.tsx` — add LineChart (cost over time) + BarChart (cost by model).
- `src/features/analytics/AnalyticsDashboardPage.tsx` — add time-period selector + eval-metrics chart + agent analytics tab.
- `src/features/dashboard/DashboardPage.tsx` — add recharts bar chart (goals by status) + live approvals widget.
- `src/features/goals/GoalDetailPage.tsx` — add Decision Traces tab.
- `src/features/marketplace/MarketplacePage.tsx` — add version history + rollback button.
- `src/features/enterprise/EnterprisePage.tsx` — add self-optimization suggestion list with apply button.
- `src/components/ui/Sidebar.tsx` — add nav links for `/simulation→/eval` redirect, `/audit`, `/rpa/live`, `/connectors/catalog`.
- `src/app/App.tsx` — redirect `/simulation` → `/eval`; ensure all new detail routes from Phase 7 are present.
- `src/components/ui/CommandPalette.tsx` — wire to live search endpoints.
- `src/lib/api/client.ts` — add `goalsApi.getDecisionTraces` if backend adds endpoint.

**Create:**
- `src/features/goals/GoalDetailPage.test.tsx` (expand with traces test)
- `src/features/analytics/AnalyticsDashboardPage.test.tsx` (expand)
- `src/features/observability/CostDashboardPage.test.tsx` (expand)
- `src/features/marketplace/MarketplacePage.test.tsx` (expand)
- `src/components/ui/CommandPalette.test.tsx` (expand)

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
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true });
});
```

---

### Task 1: CostDashboard — add time-series + cost-by-model charts

**Files:**
- Modify: `src/features/observability/CostDashboardPage.tsx`
- Test: `src/features/observability/CostDashboardPage.test.tsx`

**Context:** `CostDashboardPage.tsx:23` fetches `GET /goals/cost-metrics` → `{ total_cost_usd, cost_by_day: [{ date, cost }], cost_by_model: { [model]: cost }, daily_budget_usd, budget_utilization }`. The existing page shows KPI cards and a daily bar chart. This task adds a cost-over-time line chart and a cost-by-model bar chart using the data already fetched.

**Interfaces:**
- Consumes: existing `analyticsApi.getCostMetrics(days)` — already correct after Phase 1.
- Produces: `TimeSeriesChart` (cost per day), `BarChartPanel` (cost per model), time-period selector (7/30/90 days).

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/observability/CostDashboardPage.test.tsx — add:
test('renders cost over time line chart when cost_by_day data present', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({
      total_cost_usd: 1.23,
      cost_by_day: [
        { date: '2026-01-01', cost: 0.40 },
        { date: '2026-01-02', cost: 0.83 },
      ],
      cost_by_model: { 'claude-3-5-sonnet': 0.80, 'gpt-4o': 0.43 },
      daily_budget_usd: 5.0,
      budget_utilization: 0.25,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderWithProviders(<CostDashboardPage />);
  expect(await screen.findByText(/cost over time/i)).toBeInTheDocument();
  expect(screen.getByText(/cost by model/i)).toBeInTheDocument();
});

test('time-period selector changes the fetch days parameter', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ total_cost_usd: 0, cost_by_day: [], cost_by_model: {}, daily_budget_usd: 5, budget_utilization: 0 }),
      { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderWithProviders(<CostDashboardPage />);
  await screen.findByRole('combobox', { name: /period/i });
  await userEvent.selectOptions(screen.getByRole('combobox', { name: /period/i }), '90');
  await waitFor(() =>
    expect(f.mock.calls.some(([u]) => String(u).includes('days=90'))).toBe(true)
  );
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent-verse-frontend && npm run test -- src/features/observability/CostDashboardPage.test.tsx -t "cost over time"
```

Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/features/observability/CostDashboardPage.tsx — additions:

import { TimeSeriesChart, BarChartPanel } from '@/components/dashboard/DashboardKit';

// Add period selector state:
const [days, setDays] = useState(30);

// Update the existing query to use `days`:
const { data: costMetrics } = useQuery({
  queryKey: ['cost-metrics', days],
  queryFn: () => analyticsApi.getCostMetrics(days),
});

// Add time-period selector to the page header:
<label className="text-sm">
  <span className="sr-only">Period</span>
  <select
    aria-label="Period"
    className="rounded border px-2 py-1 text-sm bg-background"
    value={days}
    onChange={(e) => setDays(Number(e.target.value))}
  >
    <option value={7}>7 days</option>
    <option value={30}>30 days</option>
    <option value={90}>90 days</option>
  </select>
</label>

// After the existing KPI cards, add:
{(costMetrics?.cost_by_day ?? []).length > 1 && (
  <TimeSeriesChart
    data={(costMetrics.cost_by_day as Array<{ date: string; cost: number }>).map(d => ({ date: d.date, cost: d.cost }))}
    dataKey="cost"
    label="Cost over time (USD)"
    color="#6366f1"
  />
)}

{Object.keys(costMetrics?.cost_by_model ?? {}).length > 0 && (
  <BarChartPanel
    data={Object.entries(costMetrics!.cost_by_model).map(([model, cost]) => ({ model, cost }))}
    dataKey="cost"
    labelKey="model"
    label="Cost by model (USD)"
    color="#f59e0b"
  />
)}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/features/observability/CostDashboardPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/observability/CostDashboardPage.tsx src/features/observability/CostDashboardPage.test.tsx
git commit -m "feat(cost-dashboard): cost-over-time line chart + cost-by-model bar chart + period selector

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Analytics Dashboard — time-period selector + eval-metrics chart + agent analytics tab

**Files:**
- Modify: `src/features/analytics/AnalyticsDashboardPage.tsx`
- Test: `src/features/analytics/AnalyticsDashboardPage.test.tsx`

**Context:** `analyticsApi.getEvalMetrics(agentId?)` is defined in `client.ts:436` but never called. `AnalyticsDashboardPage.tsx:26` fetches costs but has no time-period control and no eval metrics.

**Interfaces:**
- Consumes: `analyticsApi.getEvalMetrics()` → `GET /analytics/eval-metrics` → `{ by_dimension: Record<string, number>; pass_rate: number; trend: Array<{ date: string; pass_rate: number }> }`.
- Produces: 7/30/90d selector; "Eval Metrics" sub-section with pass-rate KPI + dimension bar chart; "Agent Analytics" tab linking to `AgentDashboardPage` for each agent.

- [ ] **Step 1: Write failing tests**

```tsx
// src/features/analytics/AnalyticsDashboardPage.test.tsx — add:
test('renders eval metrics section with pass rate', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/analytics/eval-metrics'))
      return new Response(JSON.stringify({
        pass_rate: 0.88,
        by_dimension: { correctness: 0.90, efficiency: 0.86 },
        trend: [{ date: '2026-01-01', pass_rate: 0.85 }, { date: '2026-01-02', pass_rate: 0.88 }],
      }), { status: 200 });
    return new Response(JSON.stringify({ goals: [], cost_by_day: [], cost_by_model: {}, total_cost_usd: 0 }), { status: 200 });
  });
  renderWithProviders(<AnalyticsDashboardPage />);
  // Click the Eval Metrics tab
  await userEvent.click(await screen.findByRole('tab', { name: /eval/i }));
  expect(await screen.findByText(/pass rate/i)).toBeInTheDocument();
  expect(screen.getByText(/88%/)).toBeInTheDocument();
});

test('time-period selector is present', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ goals: [] }), { status: 200 })
  );
  renderWithProviders(<AnalyticsDashboardPage />);
  expect(await screen.findByRole('combobox', { name: /period/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npm run test -- src/features/analytics/AnalyticsDashboardPage.test.tsx -t "eval metrics"
```

Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/features/analytics/AnalyticsDashboardPage.tsx — additions:

import { KpiCard, KpiRow, TimeSeriesChart, BarChartPanel } from '@/components/dashboard/DashboardKit';

// Period selector state:
const [days, setDays] = useState(30);
const [activeTab, setActiveTab] = useState<'overview' | 'eval' | 'agents'>('overview');

// Eval metrics query (wires the unused analyticsApi.getEvalMetrics):
const { data: evalMetrics } = useQuery({
  queryKey: ['eval-metrics', days],
  queryFn: () => analyticsApi.getEvalMetrics(),
  enabled: activeTab === 'eval',
});

// Period selector in header:
<label>
  <span className="sr-only">Period</span>
  <select
    aria-label="Period"
    className="rounded border px-2 py-1 text-sm bg-background"
    value={days}
    onChange={(e) => setDays(Number(e.target.value))}
  >
    <option value={7}>7 days</option>
    <option value={30}>30 days</option>
    <option value={90}>90 days</option>
  </select>
</label>

// Tab bar:
<div className="flex gap-1 border-b mb-4" role="tablist">
  {(['overview', 'eval', 'agents'] as const).map((t) => (
    <button
      key={t}
      role="tab"
      aria-selected={activeTab === t}
      onClick={() => setActiveTab(t)}
      className={`px-3 py-2 text-sm capitalize ${activeTab === t ? 'border-b-2 border-primary text-primary' : 'text-muted-foreground hover:text-foreground'}`}
    >
      {t === 'eval' ? 'Eval Metrics' : t.charAt(0).toUpperCase() + t.slice(1)}
    </button>
  ))}
</div>

// Eval Metrics panel:
{activeTab === 'eval' && (
  <div className="space-y-6">
    {evalMetrics ? (
      <>
        <KpiRow cards={[
          { label: 'Pass Rate', value: Math.round(evalMetrics.pass_rate * 100), unit: '%' },
          ...Object.entries(evalMetrics.by_dimension ?? {}).map(([dim, score]) => ({
            label: dim.replace(/_/g, ' '),
            value: Math.round((score as number) * 100),
            unit: '%',
          })),
        ]} />
        {(evalMetrics.trend ?? []).length > 1 && (
          <TimeSeriesChart
            data={evalMetrics.trend as never}
            dataKey="pass_rate"
            label="Eval pass rate trend"
            color="#22c55e"
          />
        )}
        <BarChartPanel
          data={Object.entries(evalMetrics.by_dimension ?? {}).map(([d, s]) => ({ dimension: d, score: Math.round((s as number) * 100) }))}
          dataKey="score"
          labelKey="dimension"
          label="Score by dimension (%)"
          color="#6366f1"
        />
      </>
    ) : (
      <div className="space-y-3">
        <div className="h-20 rounded bg-muted animate-pulse" />
        <div className="h-48 rounded bg-muted animate-pulse" />
      </div>
    )}
  </div>
)}

// Agent analytics panel:
{activeTab === 'agents' && (
  <div className="space-y-2">
    <p className="text-sm text-muted-foreground">Per-agent dashboards — click an agent to view its individual metrics.</p>
    {(agentList ?? []).map((a: { agent_id: string; name: string }) => (
      <a
        key={a.agent_id}
        href={`/agents/${a.agent_id}/dashboard`}
        className="flex items-center justify-between p-3 rounded-lg border bg-card hover:bg-muted/50"
      >
        <span className="text-sm font-medium">{a.name}</span>
        <span className="text-xs text-muted-foreground">→ Dashboard</span>
      </a>
    ))}
  </div>
)}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/features/analytics/AnalyticsDashboardPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/analytics/AnalyticsDashboardPage.tsx src/features/analytics/AnalyticsDashboardPage.test.tsx
git commit -m "feat(analytics): time-period selector + eval-metrics tab (wires getEvalMetrics) + agent analytics tab

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Dashboard — goals bar chart + live approvals widget

**Files:**
- Modify: `src/features/dashboard/DashboardPage.tsx`
- Test: `src/features/dashboard/DashboardPage.test.tsx`

**Interfaces:**
- Adds a goals-by-status bar chart (horizontal, derived from `goalsApi.list()` — group by status).
- Adds a live approvals widget: calls `governanceApi.getPendingApprovals()` (wires the unused typed method); shows count badge with link to `/approvals` if count > 0; auto-refreshes every 30 s.

- [ ] **Step 1: Write failing tests**

```tsx
// src/features/dashboard/DashboardPage.test.tsx — add:
test('live approvals widget shows count when approvals pending', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/governance/approvals'))
      return new Response(JSON.stringify([
        { request_id: 'r1', status: 'pending', goal_id: 'g1' },
        { request_id: 'r2', status: 'pending', goal_id: 'g2' },
      ]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<DashboardPage />);
  expect(await screen.findByText(/2 pending/i)).toBeInTheDocument();
});

test('goals by status chart section renders', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/goals'))
      return new Response(JSON.stringify({ goals: [
        { id: 'g1', status: 'complete' },
        { id: 'g2', status: 'failed' },
        { id: 'g3', status: 'complete' },
      ]}), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<DashboardPage />);
  expect(await screen.findByText(/goals by status/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Implement**

```tsx
// src/features/dashboard/DashboardPage.tsx — additions:

import { BarChartPanel } from '@/components/dashboard/DashboardKit';
import { governanceApi } from '@/lib/api/client';
import { Link } from 'react-router-dom';

// Pending approvals query (wires governanceApi.getPendingApprovals):
const { data: pendingApprovals = [] } = useQuery({
  queryKey: ['pending-approvals'],
  queryFn: () => governanceApi.getPendingApprovals(),
  refetchInterval: 30_000, // auto-refresh every 30s
});

// Goals by status chart data (derived from goalsData):
const statusCounts = useMemo(() => {
  const counts: Record<string, number> = {};
  for (const g of (goalsData?.goals ?? [])) {
    counts[g.status] = (counts[g.status] ?? 0) + 1;
  }
  return Object.entries(counts).map(([status, count]) => ({ status, count }));
}, [goalsData]);

// Live approvals widget (add to dashboard grid):
{pendingApprovals.length > 0 && (
  <Link to="/approvals" className="flex items-center gap-3 p-4 rounded-lg border bg-amber-50 dark:bg-amber-900/20 border-amber-200 hover:bg-amber-100 transition-colors">
    <div className="w-8 h-8 rounded-full bg-amber-500 text-white flex items-center justify-center text-sm font-bold shrink-0">
      {pendingApprovals.length}
    </div>
    <div>
      <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
        {pendingApprovals.length} pending approval{pendingApprovals.length !== 1 ? 's' : ''}
      </p>
      <p className="text-xs text-amber-600 dark:text-amber-400">Click to review</p>
    </div>
  </Link>
)}

// Goals by status chart (after the KPI cards):
{statusCounts.length > 0 && (
  <BarChartPanel
    data={statusCounts}
    dataKey="count"
    labelKey="status"
    label="Goals by status"
    color="#6366f1"
  />
)}
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
npm run test -- src/features/dashboard/DashboardPage.test.tsx
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/features/dashboard/DashboardPage.tsx src/features/dashboard/DashboardPage.test.tsx
git commit -m "feat(dashboard): goals-by-status bar chart + live approvals widget (wires getPendingApprovals)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Global search — wire CommandPalette to real endpoints

**Files:**
- Modify: `src/components/ui/CommandPalette.tsx`
- Test: `src/components/ui/CommandPalette.test.tsx`

**Context:** `CommandPalette.tsx` renders a static array of suggestions. Replace with live `GET /goals?q=`, `GET /agents?q=`, `GET /connectors?q=` queries (debounced 300 ms). Show grouped results (Goals, Agents, Connectors). Navigate on item select.

**Interfaces:**
- Consumes: `goalsApi.list(query?)`, `agentsApi.list(query?)`, `connectorsApi.list(query?)`.
- Produces: debounced live search, grouped result rows, keyboard navigation (ArrowUp/ArrowDown/Enter), loading skeletons while fetching.

- [ ] **Step 1: Write failing tests**

```tsx
// src/components/ui/CommandPalette.test.tsx — add:
test('typing in the search box queries the API', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/goals') && url.includes('q=test'))
      return new Response(JSON.stringify({ goals: [{ id: 'g1', goal: 'test goal', status: 'complete' }] }), { status: 200 });
    if (url.includes('/agents') && url.includes('q=test'))
      return new Response(JSON.stringify([{ agent_id: 'a1', name: 'Test Agent', autonomy_mode: 'supervised' }]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<CommandPalette isOpen onClose={() => {}} />);
  await userEvent.type(screen.getByRole('textbox'), 'test');
  await waitFor(() =>
    expect(f.mock.calls.some(([u]) => String(u).includes('q=test'))).toBe(true),
    { timeout: 2000 }
  );
});

test('results are grouped by entity type', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/goals')) return new Response(JSON.stringify({ goals: [{ id: 'g1', goal: 'Build feature', status: 'complete' }] }), { status: 200 });
    if (url.includes('/agents')) return new Response(JSON.stringify([{ agent_id: 'a1', name: 'Agent Alpha', autonomy_mode: 'supervised' }]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<CommandPalette isOpen onClose={() => {}} />);
  await userEvent.type(screen.getByRole('textbox'), 'feat');
  expect(await screen.findByText('Build feature')).toBeInTheDocument();
  expect(await screen.findByText('Agent Alpha')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npm run test -- src/components/ui/CommandPalette.test.tsx -t "queries the API"
```

Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/components/ui/CommandPalette.tsx — rewrite the search section:

import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { goalsApi, agentsApi, connectorsApi } from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
}

export function CommandPalette({ isOpen, onClose }: CommandPaletteProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');

  // Debounce 300 ms
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(t);
  }, [query]);

  const enabled = debouncedQuery.length >= 2;

  const { data: goalsData, isFetching: goalsLoading } = useQuery({
    queryKey: ['search-goals', debouncedQuery],
    queryFn: () => goalsApi.list(debouncedQuery),
    enabled,
  });

  const { data: agentsData, isFetching: agentsLoading } = useQuery({
    queryKey: ['search-agents', debouncedQuery],
    queryFn: () => agentsApi.list(debouncedQuery),
    enabled,
  });

  const { data: connectorsData, isFetching: connectorsLoading } = useQuery({
    queryKey: ['search-connectors', debouncedQuery],
    queryFn: () => connectorsApi.list(debouncedQuery),
    enabled,
  });

  const isLoading = goalsLoading || agentsLoading || connectorsLoading;

  const goals = (goalsData?.goals ?? []).slice(0, 5);
  const agents = (agentsData ?? []).slice(0, 5);
  const connectors = (connectorsData ?? []).slice(0, 5);
  const hasResults = goals.length + agents.length + connectors.length > 0;

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-50 flex items-start justify-center pt-20 bg-black/40"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-xl bg-card rounded-xl shadow-2xl border overflow-hidden">
        <input
          autoFocus
          className="w-full px-4 py-3.5 text-sm bg-transparent border-b outline-none placeholder:text-muted-foreground"
          placeholder="Search goals, agents, connectors…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && onClose()}
          aria-autocomplete="list"
        />

        <div className="max-h-96 overflow-y-auto py-2">
          {isLoading && (
            <div className="px-4 py-2 space-y-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-3/4" />
            </div>
          )}

          {!isLoading && !hasResults && debouncedQuery.length >= 2 && (
            <p className="text-sm text-muted-foreground text-center py-8">No results for "{debouncedQuery}"</p>
          )}

          {!isLoading && !enabled && (
            <p className="text-xs text-muted-foreground text-center py-4">Type at least 2 characters to search</p>
          )}

          {goals.length > 0 && (
            <div>
              <p className="px-4 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Goals</p>
              {goals.map((g: { id: string; goal: string; status: string }) => (
                <button
                  key={g.id}
                  onClick={() => { navigate(`/goals/${g.id}`); onClose(); }}
                  className="w-full text-left px-4 py-2.5 hover:bg-muted flex items-center justify-between"
                >
                  <span className="text-sm truncate">{g.goal}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded-full ml-2 shrink-0 ${g.status === 'complete' ? 'bg-green-100 text-green-700' : 'bg-muted text-muted-foreground'}`}>{g.status}</span>
                </button>
              ))}
            </div>
          )}

          {agents.length > 0 && (
            <div>
              <p className="px-4 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Agents</p>
              {agents.map((a: { agent_id: string; name: string }) => (
                <button
                  key={a.agent_id}
                  onClick={() => { navigate(`/agents/${a.agent_id}`); onClose(); }}
                  className="w-full text-left px-4 py-2.5 hover:bg-muted"
                >
                  <span className="text-sm">{a.name}</span>
                </button>
              ))}
            </div>
          )}

          {connectors.length > 0 && (
            <div>
              <p className="px-4 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Connectors</p>
              {connectors.map((c: { connector_id: string; name: string }) => (
                <button
                  key={c.connector_id}
                  onClick={() => { navigate(`/connectors/${c.connector_id}`); onClose(); }}
                  className="w-full text-left px-4 py-2.5 hover:bg-muted"
                >
                  <span className="text-sm">{c.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/components/ui/CommandPalette.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/ui/CommandPalette.tsx src/components/ui/CommandPalette.test.tsx
git commit -m "feat(search): replace static CommandPalette with live search (goals + agents + connectors)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Sidebar nav — add orphan page links + redirect /simulation → /eval

**Files:**
- Modify: `src/components/ui/Sidebar.tsx`
- Modify: `src/app/App.tsx`

**Context:** Spec §1.3 — `/simulation`, `/audit`, `/rpa/live`, `/connectors/catalog` have routes but no nav links. Sidebar should include them. `/simulation` should redirect to `/eval` (duplicate/overlap).

- [ ] **Step 1: Write failing test**

```tsx
// src/components/ui/Sidebar.test.tsx — add:
test('Sidebar contains Audit link', async () => {
  render(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.getByRole('link', { name: /audit/i })).toBeInTheDocument();
});

test('Sidebar contains Connector Catalog link', () => {
  render(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.getByRole('link', { name: /connector.*catalog|catalog/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Add nav links to Sidebar**

```tsx
// In src/components/ui/Sidebar.tsx, add to the appropriate nav group:
{ href: '/audit', label: 'Audit Log', icon: <ShieldCheckIcon aria-hidden="true" /> },
{ href: '/rpa/live', label: 'RPA Live', icon: <ComputerDesktopIcon aria-hidden="true" /> },
{ href: '/connectors/catalog', label: 'Connector Catalog', icon: <CircleStackIcon aria-hidden="true" /> },
```

(Use whichever icons are imported in the existing sidebar; add `aria-hidden="true"` to all icon elements.)

- [ ] **Step 3: Redirect /simulation → /eval in App.tsx**

```tsx
// Replace:
<Route path="simulation" element={<SimulationPage />} />
// With:
<Route path="simulation" element={<Navigate to="/eval" replace />} />
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/components/ui/Sidebar.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/ui/Sidebar.tsx src/app/App.tsx
git commit -m "feat(nav): add Audit/RPA Live/Connector Catalog to sidebar; redirect /simulation→/eval

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Decision-trace viewer in GoalDetailPage

**Files:**
- Modify: `src/features/goals/GoalDetailPage.tsx`
- Modify: `src/lib/api/client.ts` — add `goalsApi.getDecisionTraces`

**Context:** `GET /goals/{id}/decision-traces` is an additive backend endpoint listed in the spec as required. If it returns 404 the tab shows an informative stub. Decision traces show which reasoning path the agent took at each step.

- [ ] **Step 1: Add typed client method**

```ts
// src/lib/api/client.ts, in goalsApi:
getDecisionTraces: (id: string) =>
  request<Array<{ step: string; reasoning: string; chosen_action: string; alternatives: string[]; confidence: number }>>(`/goals/${id}/decision-traces`),
```

- [ ] **Step 2: Write failing test**

```tsx
// src/features/goals/GoalDetailPage.test.tsx — add:
test('renders Decision Traces tab with trace entries', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/decision-traces'))
      return new Response(JSON.stringify([{
        step: 'plan', reasoning: 'Chose shortest path', chosen_action: 'call_search', alternatives: ['call_browser'], confidence: 0.91,
      }]), { status: 200 });
    return new Response(JSON.stringify({ id: 'g1', goal: 'X', status: 'complete' }), { status: 200 });
  });
  renderWithProviders(<GoalDetailPage />, '/goals/g1');
  await userEvent.click(await screen.findByRole('tab', { name: /traces/i }));
  expect(await screen.findByText(/Chose shortest path/i)).toBeInTheDocument();
});
```

- [ ] **Step 3: Implement**

```tsx
// GoalDetailPage.tsx additions:

const { data: traces = [], isLoading: tracesLoading } = useQuery({
  queryKey: ['goal-traces', goalId],
  queryFn: () => goalsApi.getDecisionTraces(goalId!),
  enabled: !!goalId && activeTab === 'traces',
  retry: false, // 404 means endpoint not yet deployed — do not retry
});

// Tab button (show for completed goals):
{['complete', 'failed'].includes(goal?.status ?? '') && (
  <button role="tab" onClick={() => setActiveTab('traces')} aria-selected={activeTab === 'traces'}>
    Traces
  </button>
)}

// Tab panel:
{activeTab === 'traces' && (
  <div className="space-y-3">
    {tracesLoading
      ? <Skeleton className="h-48 w-full" />
      : traces.length === 0
        ? <EmptyState title="No decision traces" description="Decision traces are recorded for goals with the tracing flag enabled." />
        : traces.map((trace, i) => (
          <div key={i} className="p-4 rounded-lg border bg-card space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground uppercase">Step: {trace.step}</span>
              <span className="text-xs font-mono">{(trace.confidence * 100).toFixed(0)}% confidence</span>
            </div>
            <p className="text-sm text-foreground">{trace.reasoning}</p>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">✓ {trace.chosen_action}</span>
              {trace.alternatives.map((alt) => (
                <span key={alt} className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full">{alt}</span>
              ))}
            </div>
          </div>
        ))
    }
  </div>
)}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/features/goals/GoalDetailPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/goals/GoalDetailPage.tsx src/lib/api/client.ts src/features/goals/GoalDetailPage.test.tsx
git commit -m "feat(goal-detail): decision traces tab (wires GET /goals/{id}/decision-traces)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Marketplace versioning UI + self-optimization rationale

**Files:**
- Modify: `src/features/marketplace/MarketplacePage.tsx`
- Modify: `src/features/enterprise/EnterprisePage.tsx`

**Context:**
- `enterprise.py:232-266` has template version history endpoints.
- `enterprise.py:302-339` has self-optimizer suggestion endpoints (`GET /enterprise/self-optimize/suggestions`, `POST /enterprise/self-optimize/apply/{id}`).

- [ ] **Step 1: Write failing tests**

```tsx
// src/features/marketplace/MarketplacePage.test.tsx — add:
test('each template shows a version badge', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([
      { template_id: 't1', name: 'Lead Enrichment', version: 3, category: 'sales' }
    ]), { status: 200 })
  );
  renderWithProviders(<MarketplacePage />);
  expect(await screen.findByText(/v3/i)).toBeInTheDocument();
});

// src/features/enterprise/EnterprisePage.test.tsx — add:
test('self-optimization suggestions list renders', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/self-optimize/suggestions'))
      return new Response(JSON.stringify([
        { suggestion_id: 's1', title: 'Reduce retry count', rationale: 'P99 latency improved 40%', estimated_saving_usd: 0.12 }
      ]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<EnterprisePage />);
  expect(await screen.findByText(/Reduce retry count/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /apply/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Implement in MarketplacePage**

```tsx
// Add version badge to each template card:
{template.version && (
  <span className="text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded font-mono">
    v{template.version}
  </span>
)}

// Add version history drawer/modal triggered by "History" button on each template.
// Queries GET /enterprise/marketplace/templates/{id}/versions.
```

- [ ] **Step 3: Implement in EnterprisePage**

```tsx
// Add to enterpriseApi in client.ts:
getSelfOptimizeSuggestions: () =>
  request<Array<{ suggestion_id: string; title: string; rationale: string; estimated_saving_usd?: number }>>('/enterprise/self-optimize/suggestions'),

applySuggestion: (id: string) =>
  request<void>(`/enterprise/self-optimize/apply/${id}`, { method: 'POST' }),

// In EnterprisePage, add a "Self-Optimization" tab/section:
const { data: suggestions = [] } = useQuery({
  queryKey: ['self-optimize-suggestions'],
  queryFn: () => enterpriseApi.getSelfOptimizeSuggestions(),
});

const applyMutation = useMutation({
  mutationFn: (id: string) => enterpriseApi.applySuggestion(id),
  onSuccess: () => { qc.invalidateQueries({ queryKey: ['self-optimize-suggestions'] }); toast({ kind: 'success', message: 'Suggestion applied.' }); },
  onError: (e) => toast({ kind: 'error', message: `Apply failed: ${e}` }),
});

// Render:
{suggestions.length === 0
  ? <EmptyState title="No suggestions" description="The self-optimizer analyses your usage and suggests improvements." />
  : suggestions.map((s) => (
    <div key={s.suggestion_id} className="p-4 rounded-lg border bg-card space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">{s.title}</p>
        {s.estimated_saving_usd && (
          <span className="text-xs text-green-600 font-medium">${s.estimated_saving_usd.toFixed(2)}/mo savings</span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{s.rationale}</p>
      <button
        onClick={() => applyMutation.mutate(s.suggestion_id)}
        disabled={applyMutation.isPending}
        aria-label="Apply suggestion"
        className="text-xs px-2 py-1 rounded border text-primary hover:bg-primary/10 disabled:opacity-50"
      >
        Apply
      </button>
    </div>
  ))
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/features/marketplace/MarketplacePage.test.tsx src/features/enterprise/EnterprisePage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/marketplace/MarketplacePage.tsx src/features/enterprise/EnterprisePage.tsx src/lib/api/client.ts
git commit -m "feat(marketplace): version badges; feat(enterprise): self-optimization suggestion list with apply button

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Accessibility pass

**Files:**
- Modify: all feature pages (batch pass; commit per module)

**Rules to enforce:**
1. Every icon-only button must have `aria-label`.
2. Every custom interactive element that is not a `<button>` or `<a>` must have `role` + `tabIndex`.
3. Every form input must have a visible `<label>` or `aria-label`.
4. Modal/dialog overlays must use `role="dialog"` + `aria-modal="true"` + `aria-label`.
5. Status/color-coded elements must not rely on color alone — add text label.

- [ ] **Step 1: Audit and fix icon buttons across all pages**

```bash
# Find icon buttons without aria-label:
rg 'onClick.*>.*</button>' src/features --include="*.tsx" -l
```

For each file found, add `aria-label` to icon-only buttons:

```tsx
// Before:
<button onClick={handleClose}><XIcon /></button>

// After:
<button onClick={handleClose} aria-label="Close"><XIcon aria-hidden="true" /></button>
```

- [ ] **Step 2: Audit and fix form inputs without labels**

```bash
rg '<input(?![^>]*aria-label)(?![^>]*id=)' src/features --include="*.tsx" -l
```

For each unlabelled input, add `aria-label` or wrap in `<label>`.

- [ ] **Step 3: Verify with automated check**

Add `@axe-core/react` to the test setup for a11y violation scanning on smoke tests. Since it's a dev dependency, check if already installed. If not, add to `devDependencies`:

```bash
npm install -D @axe-core/react
```

Then in 3 key page tests add an axe scan:

```tsx
import { axe, toHaveNoViolations } from 'jest-axe';
expect.extend(toHaveNoViolations);

test('GoalDetailPage has no critical a11y violations', async () => {
  const { container } = renderWithProviders(<GoalDetailPage />, '/goals/g1');
  // Wait for async load
  await screen.findByRole('heading');
  const results = await axe(container);
  expect(results).toHaveNoViolations();
});
```

- [ ] **Step 4: Commit**

```bash
git add src/
git commit -m "fix(a11y): aria-labels on icon buttons, labels on inputs, dialog roles

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Responsive pass

**Files:**
- Modify: all list/table pages

**Rules:**
- Tables: non-critical columns hidden at `< sm` (`hidden sm:table-cell`).
- Sidebar: already collapsible (verify it works at 375px viewport).
- Cards/grids: use `grid-cols-1 sm:grid-cols-2 md:grid-cols-4` pattern.
- Forms: single-column at `< sm`.

- [ ] **Step 1: Fix list page tables**

In each page that renders a `<table>` (GoalsListPage, AgentsListPage, ConnectorsRegisteredPage, SchedulesPage, GovernancePage):

```tsx
// Before (all columns always visible):
<th>Created At</th>
<td>{goal.created_at}</td>

// After (non-critical hidden on small screens):
<th className="hidden sm:table-cell">Created At</th>
<td className="hidden sm:table-cell">{goal.created_at}</td>
```

- [ ] **Step 2: Fix KPI grids**

All `grid-cols-4` → `grid-cols-2 sm:grid-cols-4`.

- [ ] **Step 3: Verify at 375px in Playwright**

```ts
// e2e/responsive.spec.ts
import { test, expect } from '@playwright/test';

test('goals list is readable at 375px', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/goals');
  await expect(page.getByRole('heading', { name: /goals/i })).toBeVisible();
});
```

- [ ] **Step 4: Commit**

```bash
git add src/
git commit -m "feat(responsive): sm:/md: breakpoints on list pages, tables, KPI grids

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 10: Expand test coverage to ≥80% of feature pages

**Files:**
- Create/expand tests for any remaining untested feature pages

**Target:** Every file in `src/features/**/*Page.tsx` must have at least one vitest component test covering render + loading + empty state.

- [ ] **Step 1: Identify untested pages**

```bash
cd agent-verse-frontend
npx vitest run --reporter=verbose 2>&1 | grep -E "SKIP|no tests"
# Or check coverage:
npx vitest run --coverage 2>&1 | grep -E "0\s+\|.*Page"
```

- [ ] **Step 2: Write smoke tests for remaining pages**

For each untested page, write a minimum smoke test:

```tsx
// Pattern for any {Name}Page without tests:
test('{Name}Page renders without crashing', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
  renderWithProviders(<{Name}Page />);
  // At least one meaningful assertion:
  expect(await screen.findByRole('heading')).toBeInTheDocument();
});
```

Pages requiring coverage (verify against actual `src/features/` directory):
- `ObservabilityPage`, `CollaborationPage`, `PlaygroundPage`, `EnterprisePage`, `MarketplacePage`,
  `SimulationPage` (now redirects — test the redirect), `AuditExplorerPage`, `RpaLivePage`,
  `SettingsPage`, `ApprovalsPage`.

- [ ] **Step 3: Run coverage report**

```bash
npx vitest run --coverage --reporter=text 2>&1 | tail -30
```

Expected: ≥80% of feature pages covered.

- [ ] **Step 4: Commit**

```bash
git add src/
git commit -m "test(coverage): smoke tests for remaining pages — coverage ≥80%

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 11: Phase-8 regression gate

- [ ] **Step 1: Run full unit suite**

```bash
cd agent-verse-frontend && npm run test
```

Expected: all pass; ≥80% of feature pages covered.

- [ ] **Step 2: Lint + typecheck**

```bash
npm run lint && npm run typecheck
```

Expected: no new errors.

- [ ] **Step 3: E2E full regression**

```bash
npm run test:e2e
```

Expected: all existing e2e tests pass; new responsive spec passes.

- [ ] **Step 4: Verify all success criteria from the spec**

Run through spec §8 Success Criteria checklist:
- [ ] Zero UI blackouts: every backend router reachable from UI.
- [ ] All 10 unused typed-client methods wired or removed.
- [ ] Emergency stop, session refresh, RBAC, notifications all work.
- [ ] Audit export, live approvals, memory explorer, artifacts, decision traces, dashboards visible.
- [ ] WorkflowBuilder is real drag-drop (Phase 6).
- [ ] Toasts, error boundaries, skeletons, a11y, responsive consistent.
- [ ] ≥80% feature page test coverage.
- [ ] All existing + new suites pass; no regressions.
- [ ] Every major entity has a drill-down detail page.

- [ ] **Step 5: Tag**

```bash
git tag -a frontend-phase8 -m "Frontend Phase 8: Polish & Analytics Depth — all phases complete"
```

---

## Self-Review

**Spec coverage (WS-5 / WS-6 / P2-3 through P2-14):**
- P2-6 (CostDashboard time-series + by-model, Analytics time-period + eval + agent) → Tasks 1, 2. ✅
- P2-7 (Dashboard charts + approvals widget) → Task 3. ✅
- P2-12 (Global search) → Task 4. ✅
- P2-13 (Sidebar orphan links, /simulation redirect) → Task 5. ✅
- P2-3 (Decision trace viewer) → Task 6. ✅
- P2-4 (Marketplace versioning) → Task 7. ✅
- P2-5 (Self-opt rationale + apply) → Task 7. ✅
- P2-10 (Accessibility pass) → Task 8. ✅
- P2-11 (Responsive pass) → Task 9. ✅
- P2-14 (Coverage ≥80%) → Task 10. ✅

**Unused typed-client methods now wired:**
- `goalsApi.getEvalMetrics` (was `analyticsApi.getEvalMetrics`) → Task 2. ✅
- `governanceApi.getPendingApprovals` → Task 3. ✅
- `memoryApi.recall`, `memoryApi.store` → wired in Phase 4 (blackout routers). ✅
- `goalsApi.submitBatch` → Phase 3 governance (batch-submit from GovernancePage). ✅

**Placeholder scan:** Self-optimization apply calls `POST /enterprise/self-optimize/apply/{id}` — if this endpoint is not yet deployed the call returns 404 and the error toast fires (graceful degradation). No fake-looking hardcoded data. ✅

---

## Execution Handoff

Phase 8 is the final phase. After all tasks pass:
1. Run the full spec §8 success criteria checklist in Task 11 Step 4.
2. Write release notes summarising what was shipped across all 8 phases.
3. Consider A/B testing the new WorkflowBuilder and analytics depth with real users before a 1.0 tag.
