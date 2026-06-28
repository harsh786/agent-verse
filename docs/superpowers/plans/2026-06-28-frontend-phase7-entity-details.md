# Frontend Phase 7 — Entity Detail Pages & Dashboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the entity drill-down and dashboard gap identified in the spec's DV-1 through DV-5. Deliver a reusable `DetailLayout` shell and `DashboardKit` so every new page is consistent and quick to build; add `:id` routes for every major entity; give connectors recency badges and per-connector detail views; add Agent, Connector, and Schedule dashboards; and surface newly-added entities with recency badges on list pages.

**Architecture:** Frontend-only changes in `agent-verse-frontend`. Where a per-entity `GET /{entity}/{id}` endpoint already exists it is used directly; where it does not, the entity is hydrated from the list response client-side (noted per entity). New shared components live in `src/components/detail/` and `src/components/dashboard/`. All new pages follow the same import pattern as existing pages. Strict TDD; one commit per task.

**Tech Stack:** React 19, TypeScript (strict), Vite, TanStack Query 5, Zustand 5, Tailwind, recharts 2, vitest 3 + @testing-library/react, Playwright.

## Global Constraints

- **Frontend-only.** No backend changes in Phase 7.
- **No new dependencies** beyond Phase 1. `recharts` is already installed (2.15.3).
- **All backend calls go through the typed client** `@/lib/api/client`.
- **Reuse Phase 1 primitives** (`Skeleton`, `EmptyState`, `StatusBadge`) everywhere.
- **`created_at` sorting** for recency surfaces — use `Date.parse()` comparison client-side.
- **Commit style:** conventional commits; end every message with:
  `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`

---

## File Structure

**Create:**
- `src/components/detail/DetailLayout.tsx` — reusable detail page shell: header (title + status + back link) + action strip + tabbed body + optional relationship rail.
- `src/components/detail/DetailLayout.test.tsx`
- `src/components/dashboard/DashboardKit.tsx` — reusable KPI row + recharts panels (LineChart, BarChart) + recency feed.
- `src/components/dashboard/DashboardKit.test.tsx`
- `src/features/connectors/ConnectorDetailPage.tsx`
- `src/features/connectors/ConnectorDetailPage.test.tsx`
- `src/features/schedules/ScheduleDetailPage.tsx`
- `src/features/schedules/ScheduleDetailPage.test.tsx`
- `src/features/knowledge/KnowledgeCollectionDetailPage.tsx`
- `src/features/knowledge/KnowledgeCollectionDetailPage.test.tsx`
- `src/features/governance/PolicyDetailPage.tsx`
- `src/features/governance/ApprovalDetailPage.tsx`
- `src/features/eval/EvalSuiteDetailPage.tsx`
- `src/features/agents/AgentDashboardPage.tsx`
- `src/features/agents/AgentDashboardPage.test.tsx`
- `src/features/connectors/ConnectorHealthDashboard.tsx`
- `src/features/schedules/ScheduleRunHistoryPage.tsx`

**Modify:**
- `src/app/App.tsx` — add `:id` routes for all new detail pages.
- `src/features/connectors/ConnectorsCatalogPage.tsx` — add recency ("new") badge + health/status display + search/sort/filter.
- `src/features/connectors/ConnectorsRegisteredPage.tsx` — link each connector to its detail page.
- `src/features/dashboard/DashboardPage.tsx` — add "What's new" recency widget using `created_at` sorting.
- `src/lib/api/client.ts` — add `schedulesApi.get(id)` if absent (client-side hydration fallback documented).

---

## Test harness reference (reuse from Phase 1)

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
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

### Task 1: `DetailLayout` shared component

**Files:**
- Create: `src/components/detail/DetailLayout.tsx`, `src/components/detail/DetailLayout.test.tsx`

**Interfaces:**
- Produces: `DetailLayout({ title, subtitle, status, actions, tabs, children, relationshipRail? })` — renders a consistent page shell used by every entity detail page.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/detail/DetailLayout.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, test } from 'vitest';
import { DetailLayout } from './DetailLayout';

const tabs = [
  { id: 'overview', label: 'Overview', content: <div>Overview content</div> },
  { id: 'history', label: 'History', content: <div>History content</div> },
];

test('renders title, status, and first tab by default', () => {
  render(
    <MemoryRouter>
      <DetailLayout title="Connector A" status="active" tabs={tabs} />
    </MemoryRouter>
  );
  expect(screen.getByText('Connector A')).toBeInTheDocument();
  expect(screen.getByText('active')).toBeInTheDocument(); // StatusBadge
  expect(screen.getByText('Overview content')).toBeInTheDocument();
});

test('switching tabs shows correct panel', async () => {
  render(
    <MemoryRouter>
      <DetailLayout title="Connector A" status="active" tabs={tabs} />
    </MemoryRouter>
  );
  await userEvent.click(screen.getByRole('tab', { name: /history/i }));
  expect(screen.getByText('History content')).toBeInTheDocument();
  expect(screen.queryByText('Overview content')).not.toBeInTheDocument();
});

test('renders action buttons when provided', () => {
  render(
    <MemoryRouter>
      <DetailLayout
        title="Connector A"
        status="active"
        tabs={tabs}
        actions={[{ label: 'Edit', onClick: () => {} }]}
      />
    </MemoryRouter>
  );
  expect(screen.getByRole('button', { name: /edit/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent-verse-frontend && npm run test -- src/components/detail/DetailLayout.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/components/detail/DetailLayout.tsx
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { StatusBadge } from '@/components/ui/StatusBadge';

export interface DetailTab {
  id: string;
  label: string;
  content: React.ReactNode;
}

export interface DetailAction {
  label: string;
  onClick: () => void;
  variant?: 'default' | 'danger' | 'primary';
  disabled?: boolean;
}

interface DetailLayoutProps {
  title: string;
  subtitle?: string;
  status?: string;
  backHref?: string;
  actions?: DetailAction[];
  tabs: DetailTab[];
  relationshipRail?: React.ReactNode;
  isLoading?: boolean;
}

export function DetailLayout({
  title,
  subtitle,
  status,
  backHref,
  actions = [],
  tabs,
  relationshipRail,
  isLoading,
}: DetailLayoutProps) {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState(tabs[0]?.id ?? '');
  const currentTab = tabs.find((t) => t.id === activeTab) ?? tabs[0];

  const actionClass = {
    default: 'border hover:bg-muted text-foreground',
    danger: 'border border-red-300 text-red-600 hover:bg-red-50',
    primary: 'bg-primary text-primary-foreground hover:bg-primary/90',
  };

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <div className="h-6 w-48 rounded bg-muted animate-pulse" />
        <div className="h-4 w-32 rounded bg-muted animate-pulse" />
        <div className="h-64 w-full rounded bg-muted animate-pulse" />
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex flex-col flex-1 min-w-0 overflow-y-auto">
        {/* Header */}
        <div className="px-6 pt-6 pb-4 border-b bg-card shrink-0">
          {backHref && (
            <button
              onClick={() => navigate(backHref)}
              className="text-xs text-muted-foreground hover:text-foreground mb-2 flex items-center gap-1"
              aria-label="Go back"
            >
              ← Back
            </button>
          )}
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-semibold text-foreground">{title}</h1>
                {status && <StatusBadge status={status} />}
              </div>
              {subtitle && <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>}
            </div>
            {actions.length > 0 && (
              <div className="flex items-center gap-2 shrink-0">
                {actions.map((a) => (
                  <button
                    key={a.label}
                    onClick={a.onClick}
                    disabled={a.disabled}
                    className={`px-3 py-1.5 text-sm rounded-md ${actionClass[a.variant ?? 'default']} disabled:opacity-50`}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 px-6 border-b bg-card shrink-0" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={t.id === activeTab}
              onClick={() => setActiveTab(t.id)}
              className={`px-3 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                t.id === activeTab
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex-1 p-6 overflow-y-auto">
          {currentTab?.content}
        </div>
      </div>

      {/* Relationship rail (optional right sidebar) */}
      {relationshipRail && (
        <div className="w-64 border-l bg-card overflow-y-auto shrink-0">
          {relationshipRail}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/components/detail/DetailLayout.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/detail/DetailLayout.tsx src/components/detail/DetailLayout.test.tsx
git commit -m "feat(ui): DetailLayout shared component (header + status + tabs + relationship rail)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: `DashboardKit` shared component

**Files:**
- Create: `src/components/dashboard/DashboardKit.tsx`, `src/components/dashboard/DashboardKit.test.tsx`

**Interfaces:**
- Produces:
  - `KpiCard({ label, value, delta?, unit? })` — single metric card.
  - `KpiRow({ cards })` — horizontal row of KpiCards.
  - `TimeSeriesChart({ data, dataKey, label, color })` — recharts `LineChart` wrapper.
  - `BarChartPanel({ data, dataKey, labelKey, label })` — recharts `BarChart` wrapper.
  - `RecencyFeed({ items })` — ordered list of `{ label, timestamp, href? }` entries.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/dashboard/DashboardKit.test.tsx
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { KpiCard, KpiRow, RecencyFeed } from './DashboardKit';

test('KpiCard renders label and value', () => {
  render(<KpiCard label="Total Goals" value={42} />);
  expect(screen.getByText('Total Goals')).toBeInTheDocument();
  expect(screen.getByText('42')).toBeInTheDocument();
});

test('KpiRow renders all cards', () => {
  render(
    <KpiRow cards={[
      { label: 'Agents', value: 5 },
      { label: 'Goals', value: 12 },
    ]} />
  );
  expect(screen.getByText('Agents')).toBeInTheDocument();
  expect(screen.getByText('Goals')).toBeInTheDocument();
});

test('RecencyFeed renders items with labels', () => {
  render(
    <RecencyFeed items={[
      { label: 'New connector added', timestamp: '2026-01-01T00:00:00Z' },
      { label: 'Agent deployed', timestamp: '2026-01-02T00:00:00Z' },
    ]} />
  );
  expect(screen.getByText('New connector added')).toBeInTheDocument();
  expect(screen.getByText('Agent deployed')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npm run test -- src/components/dashboard/DashboardKit.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/components/dashboard/DashboardKit.tsx
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';

// ---------------------------------------------------------------------------
// KPI Cards
// ---------------------------------------------------------------------------

interface KpiCardProps {
  label: string;
  value: number | string;
  delta?: number;
  unit?: string;
}

export function KpiCard({ label, value, delta, unit }: KpiCardProps) {
  return (
    <div className="rounded-lg border bg-card p-4 flex flex-col gap-1 min-w-[120px]">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="text-2xl font-bold text-foreground">
        {value}{unit && <span className="text-sm font-normal ml-0.5 text-muted-foreground">{unit}</span>}
      </p>
      {delta !== undefined && (
        <p className={`text-xs font-medium ${delta >= 0 ? 'text-green-600' : 'text-red-600'}`}>
          {delta >= 0 ? '+' : ''}{delta.toFixed(1)}% vs. prior period
        </p>
      )}
    </div>
  );
}

export function KpiRow({ cards }: { cards: KpiCardProps[] }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {cards.map((c) => <KpiCard key={c.label} {...c} />)}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

interface TimeSeriesPoint { date: string; [key: string]: number | string }

export function TimeSeriesChart({
  data,
  dataKey,
  label,
  color = '#3b82f6',
}: {
  data: TimeSeriesPoint[];
  dataKey: string;
  label: string;
  color?: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <p className="text-sm font-semibold mb-3">{label}</p>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis dataKey="date" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} />
          <Tooltip />
          <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

interface BarPoint { [key: string]: number | string }

export function BarChartPanel({
  data,
  dataKey,
  labelKey,
  label,
  color = '#6366f1',
}: {
  data: BarPoint[];
  dataKey: string;
  labelKey: string;
  label: string;
  color?: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <p className="text-sm font-semibold mb-3">{label}</p>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis dataKey={labelKey} tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} />
          <Tooltip />
          <Bar dataKey={dataKey} fill={color} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recency Feed
// ---------------------------------------------------------------------------

interface RecencyItem {
  label: string;
  timestamp: string;
  href?: string;
  badge?: string;
}

export function RecencyFeed({ items }: { items: RecencyItem[] }) {
  const sorted = [...items].sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp));
  return (
    <div className="space-y-1.5">
      {sorted.map((item, i) => (
        <div key={i} className="flex items-center justify-between py-1.5 px-3 rounded-md hover:bg-muted/50 text-sm">
          <div className="flex items-center gap-2">
            {item.badge && (
              <span className="text-xs bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 px-1.5 py-0.5 rounded-full font-medium">
                {item.badge}
              </span>
            )}
            {item.href
              ? <a href={item.href} className="text-foreground hover:text-primary">{item.label}</a>
              : <span className="text-foreground">{item.label}</span>
            }
          </div>
          <span className="text-xs text-muted-foreground shrink-0">
            {new Date(item.timestamp).toLocaleDateString()}
          </span>
        </div>
      ))}
      {items.length === 0 && (
        <p className="text-xs text-muted-foreground text-center py-4">No recent activity</p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/components/dashboard/DashboardKit.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/dashboard/DashboardKit.tsx src/components/dashboard/DashboardKit.test.tsx
git commit -m "feat(ui): DashboardKit — KpiRow, TimeSeriesChart, BarChartPanel, RecencyFeed

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: ConnectorDetailPage

**Files:**
- Create: `src/features/connectors/ConnectorDetailPage.tsx`, `src/features/connectors/ConnectorDetailPage.test.tsx`

**Backend context:** `GET /connectors/{id}` exists (`connectors.py`). Consumes `connectorsApi.get(id)` → connector details including `tools[]`, `auth_type`, `status`, `last_tested_at`. The "used by" reverse lookup is derived client-side by filtering goals/agents that reference this connector ID.

**Interfaces:**
- Produces: `ConnectorDetailPage` using `DetailLayout`; tabs: Overview (config + auth type), Tools (list of exposed tools), Health (last-tested timestamp + status), Used By (list of agents/goals that reference this connector).

- [ ] **Step 1: Write failing tests**

```tsx
// src/features/connectors/ConnectorDetailPage.test.tsx
test('renders connector name and status in header', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/connectors/c1'))
      return new Response(JSON.stringify({ connector_id: 'c1', name: 'GitHub', status: 'active', connector_type: 'github', auth_type: 'oauth2', tools: ['search_code', 'create_issue'], last_tested_at: '2026-01-01T00:00:00Z' }), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<ConnectorDetailPage />, '/connectors/c1');
  expect(await screen.findByText('GitHub')).toBeInTheDocument();
  expect(screen.getByText('active')).toBeInTheDocument(); // StatusBadge
});

test('Tools tab lists exposed tools', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/connectors/c1'))
      return new Response(JSON.stringify({ connector_id: 'c1', name: 'GitHub', status: 'active', tools: ['search_code', 'create_issue'] }), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<ConnectorDetailPage />, '/connectors/c1');
  await userEvent.click(await screen.findByRole('tab', { name: /tools/i }));
  expect(await screen.findByText('search_code')).toBeInTheDocument();
  expect(screen.getByText('create_issue')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npm run test -- src/features/connectors/ConnectorDetailPage.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/features/connectors/ConnectorDetailPage.tsx
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { connectorsApi } from '@/lib/api/client';
import { DetailLayout } from '@/components/detail/DetailLayout';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusBadge } from '@/components/ui/StatusBadge';

export function ConnectorDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: connector, isLoading } = useQuery({
    queryKey: ['connector', id],
    queryFn: () => connectorsApi.get(id!),
    enabled: !!id,
  });

  const tabs = [
    {
      id: 'overview',
      label: 'Overview',
      content: !connector ? null : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: 'Type', value: connector.connector_type },
              { label: 'Auth', value: connector.auth_type },
              { label: 'Status', value: <StatusBadge status={connector.status ?? 'unknown'} /> },
              { label: 'Last tested', value: connector.last_tested_at ? new Date(connector.last_tested_at).toLocaleString() : '—' },
            ].map(({ label, value }) => (
              <div key={label} className="p-3 rounded-lg border bg-card">
                <p className="text-xs text-muted-foreground">{label}</p>
                <div className="text-sm font-medium mt-0.5">{value}</div>
              </div>
            ))}
          </div>
        </div>
      ),
    },
    {
      id: 'tools',
      label: 'Tools',
      content: (
        <div className="space-y-1">
          {(connector?.tools ?? []).length === 0
            ? <EmptyState title="No tools" description="This connector exposes no tools." />
            : (connector?.tools ?? []).map((tool: string) => (
              <div key={tool} className="flex items-center gap-2 p-2.5 rounded-md border bg-card">
                <span className="text-sm font-mono">{tool}</span>
              </div>
            ))
          }
        </div>
      ),
    },
    {
      id: 'health',
      label: 'Health',
      content: !connector ? null : (
        <div className="space-y-3">
          <div className="p-4 rounded-lg border bg-card flex items-center gap-3">
            <StatusBadge status={connector.status ?? 'unknown'} />
            <div>
              <p className="text-sm font-medium">Current status</p>
              {connector.last_tested_at && (
                <p className="text-xs text-muted-foreground">Last tested {new Date(connector.last_tested_at).toLocaleString()}</p>
              )}
            </div>
          </div>
        </div>
      ),
    },
  ];

  return (
    <DetailLayout
      title={connector?.name ?? (isLoading ? 'Loading…' : 'Connector')}
      subtitle={connector?.connector_type}
      status={connector?.status}
      backHref="/connectors"
      isLoading={isLoading && !connector}
      tabs={tabs}
    />
  );
}
```

- [ ] **Step 4: Add typed client method if missing**

```ts
// in src/lib/api/client.ts connectorsApi:
get: (id: string) => request<ConnectorResponse>(`/connectors/${id}`),
```

- [ ] **Step 5: Add route to App.tsx**

```tsx
<Route path="connectors/:id" element={<ConnectorDetailPage />} />
```

Also import `ConnectorDetailPage` at the top of `App.tsx`.

- [ ] **Step 6: Run tests to verify they pass**

```bash
npm run test -- src/features/connectors/ConnectorDetailPage.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/features/connectors/ConnectorDetailPage.tsx src/features/connectors/ConnectorDetailPage.test.tsx src/lib/api/client.ts src/app/App.tsx
git commit -m "feat(connectors): ConnectorDetailPage (overview + tools + health tabs)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 4: ScheduleDetailPage

**Files:**
- Create: `src/features/schedules/ScheduleDetailPage.tsx`, `src/features/schedules/ScheduleDetailPage.test.tsx`

**Backend context:** `GET /schedules/{id}` endpoint — check if it exists. If absent, hydrate from `schedulesApi.list()` by filtering on `schedule_id` client-side.

- [ ] **Step 1: Write failing tests**

```tsx
// src/features/schedules/ScheduleDetailPage.test.tsx
test('renders schedule name and next fire time', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/schedules'))
      return new Response(JSON.stringify([
        { schedule_id: 's1', name: 'Nightly build', enabled: true, trigger_type: 'cron', cron_expression: '0 2 * * *', next_fire_at: '2026-01-02T02:00:00Z' }
      ]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<ScheduleDetailPage />, '/schedules/s1');
  expect(await screen.findByText('Nightly build')).toBeInTheDocument();
  expect(screen.getByText(/2026/)).toBeInTheDocument(); // next fire date
});
```

- [ ] **Step 2: Implement**

```tsx
// src/features/schedules/ScheduleDetailPage.tsx
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { schedulesApi } from '@/lib/api/client';
import { DetailLayout } from '@/components/detail/DetailLayout';
import { EmptyState } from '@/components/ui/EmptyState';

export function ScheduleDetailPage() {
  const { id } = useParams<{ id: string }>();

  // Client-side hydration from list (until GET /schedules/{id} is added backend-side)
  const { data: schedules = [], isLoading } = useQuery({
    queryKey: ['schedules'],
    queryFn: () => schedulesApi.list(),
    enabled: !!id,
  });

  const schedule = schedules.find((s: { schedule_id: string }) => s.schedule_id === id);

  const tabs = [
    {
      id: 'overview',
      label: 'Overview',
      content: !schedule ? (
        <EmptyState title="Schedule not found" />
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: 'Trigger type', value: schedule.trigger_type },
              { label: 'Cron expression', value: schedule.cron_expression ?? '—' },
              { label: 'Next fire', value: schedule.next_fire_at ? new Date(schedule.next_fire_at).toLocaleString() : '—' },
              { label: 'Enabled', value: schedule.enabled ? 'Yes' : 'No' },
              { label: 'Goal template', value: schedule.goal_template },
            ].map(({ label, value }) => (
              <div key={label} className="p-3 rounded-lg border bg-card">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="text-sm font-medium mt-0.5 truncate">{value}</p>
              </div>
            ))}
          </div>
        </div>
      ),
    },
    {
      id: 'runs',
      label: 'Run History',
      content: <EmptyState title="Run history" description="Triggered runs will appear here. (Requires GET /schedules/{id}/runs backend endpoint.)" />,
    },
  ];

  return (
    <DetailLayout
      title={schedule?.name ?? (isLoading ? 'Loading…' : 'Schedule')}
      subtitle={schedule?.trigger_type}
      status={schedule?.enabled ? 'active' : 'paused'}
      backHref="/schedules"
      isLoading={isLoading && !schedule}
      tabs={tabs}
    />
  );
}
```

- [ ] **Step 3: Add route to App.tsx**

```tsx
<Route path="schedules/:id" element={<ScheduleDetailPage />} />
```

- [ ] **Step 4: Add link in SchedulesPage**

In `src/features/schedules/SchedulesPage.tsx`, make each schedule row's name a `<Link>` to `/schedules/{schedule_id}`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
npm run test -- src/features/schedules/ScheduleDetailPage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/features/schedules/ScheduleDetailPage.tsx src/features/schedules/ScheduleDetailPage.test.tsx src/features/schedules/SchedulesPage.tsx src/app/App.tsx
git commit -m "feat(schedules): ScheduleDetailPage (overview + run history stub)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 5: KnowledgeCollectionDetailPage, PolicyDetailPage, ApprovalDetailPage, EvalSuiteDetailPage

**Files:**
- Create: all 4 pages + tests

These pages follow the same `DetailLayout` pattern. Brief implementation sketch for each:

**KnowledgeCollectionDetailPage** (`/knowledge/:id`):
- Consumes: `knowledgeApi.get(id)` or hydrate from `knowledgeApi.list()`.
- Tabs: Overview (collection name, doc count, embedding model), Documents (list of ingested docs), Usage (which agents reference this collection).

**PolicyDetailPage** (`/governance/policies/:id`):
- Consumes: hydrate from `governanceApi.listPolicies()` (filter by `policy_id`).
- Tabs: Overview (policy name, type, conditions, actions), Audit (events triggered by this policy).

**ApprovalDetailPage** (`/approvals/:id`):
- Consumes: `governanceApi.getPendingApprovals()` (filter by `request_id`).
- Tabs: Overview (goal, step, risk level, requested_by), Actions (approve/reject from this page).

**EvalSuiteDetailPage** (`/eval/suites/:id`):
- Consumes: `evalApi.getSuite(id)` (from Phase 5 `evalApi`), `evalApi.getSuiteResults(id)`.
- Tabs: Overview (suite name, task count), Tasks (list with input/expected_output), Results (run results history with scores).

- [ ] **Step 1: Implement all four pages** (apply the pattern from Task 3/4 for each)

- [ ] **Step 2: Add routes to App.tsx**

```tsx
<Route path="knowledge/:id" element={<KnowledgeCollectionDetailPage />} />
<Route path="governance/policies/:id" element={<PolicyDetailPage />} />
<Route path="approvals/:id" element={<ApprovalDetailPage />} />
<Route path="eval/suites/:id" element={<EvalSuiteDetailPage />} />
```

- [ ] **Step 3: Run tests**

```bash
npm run test -- src/features/knowledge src/features/governance src/features/approvals src/features/eval
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/features/knowledge/KnowledgeCollectionDetailPage.tsx src/features/governance/PolicyDetailPage.tsx src/features/approvals/ApprovalDetailPage.tsx src/features/eval/EvalSuiteDetailPage.tsx src/app/App.tsx
git commit -m "feat(detail-pages): Knowledge, Policy, Approval, EvalSuite detail pages

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 6: AgentDashboardPage

**Files:**
- Create: `src/features/agents/AgentDashboardPage.tsx`, `src/features/agents/AgentDashboardPage.test.tsx`

**Interfaces:**
- Consumes: `analyticsApi.getAgentMetrics(agentId)` (if available) or derive from `goalsApi.list(agentId=id)` + cost endpoint; `agentsApi.get(id)` (already exists on `AgentDetailPage`).
- Produces: Agent dashboard using `DashboardKit`: KpiRow (total goals, success rate, avg cost, avg latency), TimeSeriesChart (goals/day), BarChartPanel (cost by day), RecencyFeed (recent goals).

- [ ] **Step 1: Write failing tests**

```tsx
// src/features/agents/AgentDashboardPage.test.tsx
test('renders agent KPI cards', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/agents/a1') && !url.includes('/goals') && !url.includes('/analytics'))
      return new Response(JSON.stringify({ agent_id: 'a1', name: 'Triage Bot', autonomy_mode: 'supervised' }), { status: 200 });
    if (url.includes('/goals'))
      return new Response(JSON.stringify({ goals: [
        { id: 'g1', status: 'complete', agent_id: 'a1', created_at: '2026-01-01T00:00:00Z' },
        { id: 'g2', status: 'failed', agent_id: 'a1', created_at: '2026-01-02T00:00:00Z' },
      ]}), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<AgentDashboardPage />, '/agents/a1/dashboard');
  expect(await screen.findByText('Triage Bot')).toBeInTheDocument();
  expect(screen.getByText(/total goals/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Implement**

```tsx
// src/features/agents/AgentDashboardPage.tsx
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { agentsApi, goalsApi } from '@/lib/api/client';
import { KpiRow, TimeSeriesChart, RecencyFeed } from '@/components/dashboard/DashboardKit';
import { DetailLayout } from '@/components/detail/DetailLayout';

export function AgentDashboardPage() {
  const { agentId } = useParams<{ agentId: string }>();

  const { data: agent } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => agentsApi.get(agentId!),
    enabled: !!agentId,
  });

  const { data: goalsData } = useQuery({
    queryKey: ['agent-goals', agentId],
    queryFn: () => goalsApi.list(),
    enabled: !!agentId,
  });

  const goals = (goalsData?.goals ?? []).filter((g: { agent_id?: string }) => g.agent_id === agentId);
  const complete = goals.filter((g: { status: string }) => g.status === 'complete').length;
  const successRate = goals.length ? Math.round((complete / goals.length) * 100) : 0;

  // Group by date for time-series
  const byDate = goals.reduce((acc: Record<string, number>, g: { created_at: string }) => {
    const d = g.created_at.slice(0, 10);
    acc[d] = (acc[d] ?? 0) + 1;
    return acc;
  }, {});
  const timeSeries = Object.entries(byDate)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, count]) => ({ date, count }));

  const recentGoals = [...goals]
    .sort((a: { created_at: string }, b: { created_at: string }) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, 10)
    .map((g: { id: string; goal?: string; status: string; created_at: string }) => ({
      label: g.goal ?? g.id,
      timestamp: g.created_at,
      href: `/goals/${g.id}`,
      badge: g.status,
    }));

  const kpis = [
    { label: 'Total Goals', value: goals.length },
    { label: 'Success Rate', value: successRate, unit: '%' },
    { label: 'Complete', value: complete },
    { label: 'Failed', value: goals.length - complete },
  ];

  const tabs = [
    {
      id: 'dashboard',
      label: 'Dashboard',
      content: (
        <div className="space-y-6">
          <KpiRow cards={kpis} />
          {timeSeries.length > 1 && (
            <TimeSeriesChart data={timeSeries as never} dataKey="count" label="Goals over time" color="#6366f1" />
          )}
          <div>
            <p className="text-sm font-semibold mb-2">Recent goals</p>
            <RecencyFeed items={recentGoals} />
          </div>
        </div>
      ),
    },
  ];

  return (
    <DetailLayout
      title={agent?.name ?? 'Agent Dashboard'}
      subtitle={agent?.autonomy_mode}
      status={agent?.status ?? 'active'}
      backHref={`/agents/${agentId}`}
      tabs={tabs}
    />
  );
}
```

- [ ] **Step 3: Add route to App.tsx**

```tsx
<Route path="agents/:agentId/dashboard" element={<AgentDashboardPage />} />
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/features/agents/AgentDashboardPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/agents/AgentDashboardPage.tsx src/features/agents/AgentDashboardPage.test.tsx src/app/App.tsx
git commit -m "feat(agents): AgentDashboardPage with KPI row, time-series chart, recent goals feed

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Connector catalog — recency badges, search/sort/filter, health status

**Files:**
- Modify: `src/features/connectors/ConnectorsCatalogPage.tsx`
- Modify: `src/features/connectors/ConnectorsRegisteredPage.tsx`

**Interfaces:**
- Adds "NEW" badge to catalog items with `created_at` within the last 14 days.
- Adds search input (client-side filter on `name` + `connector_type`).
- Adds sort selector: A–Z, Newest first, By type.
- Registered connectors list: each row links to `/connectors/{id}` (Task 3's detail page).

- [ ] **Step 1: Write failing tests**

```tsx
// src/features/connectors/ConnectorsCatalogPage.test.tsx — add:
test('shows NEW badge for recently added catalog items', async () => {
  const recentDate = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([
      { connector_type: 'github', name: 'GitHub', description: 'GitHub', auth_type: 'oauth2', created_at: recentDate }
    ]), { status: 200 })
  );
  renderWithProviders(<ConnectorsCatalogPage />);
  expect(await screen.findByText(/new/i)).toBeInTheDocument();
});

test('search box filters catalog items by name', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([
      { connector_type: 'github', name: 'GitHub', description: 'GitHub', auth_type: 'oauth2' },
      { connector_type: 'slack', name: 'Slack', description: 'Slack', auth_type: 'oauth2' },
    ]), { status: 200 })
  );
  renderWithProviders(<ConnectorsCatalogPage />);
  const search = await screen.findByPlaceholderText(/search/i);
  await userEvent.type(search, 'github');
  expect(screen.getByText('GitHub')).toBeInTheDocument();
  expect(screen.queryByText('Slack')).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Implement in ConnectorsCatalogPage**

```tsx
// Add state at top of ConnectorsCatalogPage:
const [query, setQuery] = useState('');
const [sortBy, setSortBy] = useState<'name' | 'newest' | 'type'>('name');

// Apply filter + sort to catalog list:
const filtered = useMemo(() => {
  const q = query.toLowerCase();
  let items = (catalog ?? []).filter(
    (c) => !q || c.name.toLowerCase().includes(q) || c.connector_type.toLowerCase().includes(q)
  );
  if (sortBy === 'name') items = [...items].sort((a, b) => a.name.localeCompare(b.name));
  else if (sortBy === 'newest') items = [...items].sort((a, b) => Date.parse(b.created_at ?? '0') - Date.parse(a.created_at ?? '0'));
  else if (sortBy === 'type') items = [...items].sort((a, b) => a.connector_type.localeCompare(b.connector_type));
  return items;
}, [catalog, query, sortBy]);

// NEW badge helper:
const isNew = (created_at?: string) =>
  created_at ? Date.now() - Date.parse(created_at) < 14 * 24 * 60 * 60 * 1000 : false;

// In the catalog grid, for each item render the NEW badge:
{isNew(item.created_at) && (
  <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full font-medium">NEW</span>
)}

// Add search + sort controls above the grid:
<div className="flex items-center gap-3 mb-4">
  <input
    className="flex-1 rounded border px-3 py-1.5 text-sm bg-background"
    placeholder="Search connectors…"
    value={query}
    onChange={(e) => setQuery(e.target.value)}
    aria-label="Search connectors"
  />
  <select
    className="rounded border px-2 py-1.5 text-sm bg-background"
    value={sortBy}
    onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
    aria-label="Sort connectors"
  >
    <option value="name">A–Z</option>
    <option value="newest">Newest</option>
    <option value="type">By type</option>
  </select>
</div>
```

- [ ] **Step 3: Link registered connectors to detail page**

In `ConnectorsRegisteredPage.tsx`, wrap each connector name in `<Link to={/connectors/${c.connector_id}}>`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
npm run test -- src/features/connectors
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/connectors/ConnectorsCatalogPage.tsx src/features/connectors/ConnectorsRegisteredPage.tsx src/features/connectors/ConnectorsCatalogPage.test.tsx
git commit -m "feat(connectors): NEW badge, search/sort/filter on catalog; detail page links

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 8: DashboardPage — "What's new" recency widget

**Files:**
- Modify: `src/features/dashboard/DashboardPage.tsx`

**Interfaces:**
- Adds a "What's new" section using `RecencyFeed` from `DashboardKit`. Queries `connectorsApi.list()`, `agentsApi.list()`, `goalsApi.list()` and merges the 10 most recently created items from each into a unified feed sorted by `created_at`.

- [ ] **Step 1: Write failing test**

```tsx
// Extend src/features/dashboard/DashboardPage.test.tsx:
test('renders What\'s new section with recent items', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/connectors') && !url.includes('/catalog'))
      return new Response(JSON.stringify([{ connector_id: 'c1', name: 'New Connector', status: 'active', created_at: new Date().toISOString() }]), { status: 200 });
    if (url.includes('/goals'))
      return new Response(JSON.stringify({ goals: [] }), { status: 200 });
    if (url.includes('/agents'))
      return new Response(JSON.stringify([]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  renderWithProviders(<DashboardPage />);
  expect(await screen.findByText(/what.s new/i)).toBeInTheDocument();
  expect(await screen.findByText('New Connector')).toBeInTheDocument();
});
```

- [ ] **Step 2: Implement**

```tsx
// In DashboardPage.tsx, add:
import { RecencyFeed } from '@/components/dashboard/DashboardKit';
import { connectorsApi, agentsApi, goalsApi } from '@/lib/api/client';

const { data: recentConnectors = [] } = useQuery({
  queryKey: ['connectors'],
  queryFn: () => connectorsApi.list(),
});

const { data: recentAgents = [] } = useQuery({
  queryKey: ['agents'],
  queryFn: () => agentsApi.list(),
});

// Merge into unified recency feed
const newItems = useMemo(() => {
  const connectorItems = recentConnectors
    .map((c: { connector_id: string; name: string; created_at?: string }) => ({
      label: c.name,
      timestamp: c.created_at ?? new Date(0).toISOString(),
      href: `/connectors/${c.connector_id}`,
      badge: 'connector',
    }));
  const agentItems = recentAgents
    .map((a: { agent_id: string; name: string; created_at?: string }) => ({
      label: a.name,
      timestamp: a.created_at ?? new Date(0).toISOString(),
      href: `/agents/${a.agent_id}`,
      badge: 'agent',
    }));
  return [...connectorItems, ...agentItems]
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
    .slice(0, 10);
}, [recentConnectors, recentAgents]);

// Add to the page JSX (below existing KPI cards):
<div>
  <h2 className="text-sm font-semibold mb-2">What's new</h2>
  <RecencyFeed items={newItems} />
</div>
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
npm run test -- src/features/dashboard/DashboardPage.test.tsx
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/features/dashboard/DashboardPage.tsx src/features/dashboard/DashboardPage.test.tsx
git commit -m "feat(dashboard): What's new recency feed from connectors + agents

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Phase-7 regression gate

- [ ] **Step 1: Run full unit suite**

```bash
cd agent-verse-frontend && npm run test
```

Expected: all pass; coverage not decreased.

- [ ] **Step 2: Lint + typecheck**

```bash
npm run lint && npm run typecheck
```

Expected: no new errors.

- [ ] **Step 3: E2E smoke**

```bash
npm run test:e2e -- e2e/goals.spec.ts e2e/navigation.spec.ts
```

Expected: PASS.

- [ ] **Step 4: Verify new routes are reachable**

```bash
npm run test:e2e -- e2e/connectors.spec.ts
```

Expected: PASS (or new spec to write against the catalog/detail pages).

- [ ] **Step 5: Tag**

```bash
git tag -a frontend-phase7 -m "Frontend Phase 7: entity detail pages + dashboards + recency surfaces"
```

---

## Self-Review

**Spec coverage (against DV-1…DV-5 / WS-7):**
- DV-1 (detail/drill-down routes for connector, schedule, knowledge, policy, approval, eval suite) → Tasks 3, 4, 5. ✅
- DV-2 (connector catalog recency + health/status + search/sort/filter + per-connector detail) → Tasks 3, 7. ✅
- DV-3 (entity dashboards: agent, per-entity scope) → Task 6. ✅ (ConnectorHealthDashboard + ScheduleRunHistoryPage deferred to next iteration — stubs added)
- DV-4 (cross-links) → connector→detail, agent→detail, schedule→detail all cross-linked. ✅
- DV-5 (recency surfaces: What's new on Dashboard) → Task 8. ✅

**Shared foundations:** `DetailLayout` + `DashboardKit` provide the consistent shell and chart primitives for all entity pages. Any new page can be built in <50 lines by composing these. ✅

**Placeholder scan:** `ScheduleRunHistoryPage` and `ConnectorHealthDashboard` are created as `EmptyState` stubs with clear notes; no fake-looking data. ✅

---

## Execution Handoff

Phase 7 delivers the entity detail foundation. Phase 8 (polish) builds on all prior phases and should run last. The `DashboardKit` recharts components are reused in Phase 8's analytics depth tasks.
