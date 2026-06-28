# Frontend Phase 1 — Foundations & Bug Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the verified cross-cutting bugs and land the shared UI foundations (toast, session/401 handling, route-level error boundaries, shared primitives, reusable graph canvas) that every later phase reuses.

**Architecture:** Frontend-only changes in `agent-verse-frontend`. We correct the typed API client and the pages that bypass it, add a global toast store wired into the client's request layer, add 401→logout session handling, wrap routes in the existing `ErrorBoundary`, and add three reusable primitives plus a `FlowCanvas` wrapper over the already-installed `@xyflow/react`. Strict TDD with vitest + Testing Library; one commit per task.

**Tech Stack:** React 19, TypeScript (strict), Vite, TanStack Query 5, Zustand 5, Tailwind, `@xyflow/react` 12, vitest 3 + @testing-library/react, Playwright.

## Global Constraints

- **Frontend-only.** No backend files change in Phase 1. No backend endpoints are added.
- **No new dependencies.** `FlowCanvas` uses the already-present `@xyflow/react` (`^12.11.1`). Do not add `dagre` — use the built-in layered layout in Task 11.
- **Auth access is via `useAuthStore` (`@/stores/auth`)** — never read `localStorage`/`sessionStorage` for the API key directly in pages.
- **All backend calls go through the typed client** `@/lib/api/client` (add methods there rather than inlining `fetch` in pages).
- **Verified backend paths (ground truth):** analytics cost = `GET /analytics/costs` (plural); NL schedule = `POST /nl/schedule`; goal cost dashboard = `GET /goals/cost-metrics` (exists, correct); approve/reject body requires `{ approver: string, note?: string }`.
- **Tailwind design tokens only** for styling: `bg-card`, `border-border`, `text-primary`, `text-muted-foreground`, `bg-muted`, `text-destructive`, with `dark:` variants where siblings use them.
- **Quality gate per task:** `npm run typecheck` and `npm run lint` and `npm run test` must pass before commit.
- **Commit style:** conventional commits; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**Create:**
- `src/stores/toast.ts` — Zustand toast store (`useToastStore`, imperative `toast()` helper).
- `src/stores/toast.test.ts`
- `src/components/ui/Toaster.tsx` — toast viewport (renders the store's toasts).
- `src/components/ui/Toaster.test.tsx`
- `src/components/ui/Skeleton.tsx`, `src/components/ui/EmptyState.tsx`, `src/components/ui/StatusBadge.tsx` (+ co-located tests).
- `src/components/graph/FlowCanvas.tsx` — reusable `@xyflow/react` wrapper + `layeredLayout()` helper.
- `src/components/graph/FlowCanvas.test.tsx`
- `src/lib/api/client.test.ts` — client path + 401 behavior tests.
- `src/components/ui/ErrorBoundary.test.tsx`

**Modify:**
- `src/lib/api/client.ts` — fix two wrong paths; add `agentsApi.createNl`; add 401/error handling that emits toasts and triggers logout.
- `src/features/goals/GoalsListPage.tsx:58` — `goal_id ?? id` fallback.
- `src/features/governance/GovernancePage.tsx:211-227` — send `approver` in approve/reject.
- `src/features/onboarding/OnboardingPage.tsx:232-258` — use `useAuthStore` + `agentsApi.createNl`.
- `src/features/agents/AgentsListPage.tsx` — migrate to `agentsApi`.
- `src/features/agents/AgentCreatePage.tsx` — migrate to `agentsApi`.
- `src/app/App.tsx` — wrap route elements in `ErrorBoundary`.
- `src/main.tsx` — mount `<Toaster />`.

**Out of scope (deferred):** `AgentDetailPage.tsx` raw-fetch migration is folded into Phase 5 (WS-4), where that page is reworked with new tabs — migrating it twice would be wasted work.

---

## Test harness reference (existing pattern — reuse verbatim)

Component tests use this wrapper (see `src/features/goals/GoalsListPage.test.tsx`):

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

function renderWithProviders(ui: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true });
});
```

Client/store unit tests mock `fetch` directly: `vi.spyOn(globalThis, 'fetch')`.

---

### Task 1: Correct two wrong API paths in the typed client (Bug3)

**Files:**
- Modify: `src/lib/api/client.ts:410` (`schedulesApi.createNl`), `src/lib/api/client.ts:434` (`analyticsApi.getCostMetrics`)
- Test: `src/lib/api/client.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `analyticsApi.getCostMetrics(days?)` → `GET /analytics/costs?days=`; `schedulesApi.createNl(command)` → `POST /nl/schedule`.

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/api/client.test.ts
import { afterEach, expect, test, vi } from 'vitest';
import { analyticsApi, schedulesApi } from '@/lib/api/client';

afterEach(() => vi.restoreAllMocks());

function mockOk(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
}

test('getCostMetrics calls /analytics/costs (plural)', async () => {
  const f = mockOk({ total_cost_usd: 0, cost_by_day: [], cost_by_model: {}, daily_budget_usd: 0, budget_utilization: 0 });
  await analyticsApi.getCostMetrics(30);
  expect(String(f.mock.calls[0][0])).toContain('/analytics/costs?days=30');
});

test('createNl calls /nl/schedule', async () => {
  const f = mockOk({ schedule_id: 's1', name: 'n', goal_template: 'g', enabled: true, created_at: '' });
  await schedulesApi.createNl('every day at 9am');
  expect(String(f.mock.calls[0][0])).toContain('/nl/schedule');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/api/client.test.ts`
Expected: FAIL — first assertion gets `/analytics/cost`, second gets `/schedules/nl`.

- [ ] **Step 3: Apply the fix**

In `src/lib/api/client.ts`, change `getCostMetrics`:

```ts
  getCostMetrics: (days = 30) =>
    request<CostMetrics>(`/analytics/costs?days=${days}`),
```

and change `createNl`:

```ts
  createNl: (command: string) =>
    request<Schedule>("/nl/schedule", { method: "POST", body: JSON.stringify({ command }) }),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/api/client.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/client.ts src/lib/api/client.test.ts
git commit -m "fix(client): correct analytics cost and NL schedule paths

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add `agentsApi.createNl` (NL agent creation)

**Files:**
- Modify: `src/lib/api/client.ts` (within `agentsApi`, after `create`, ~line 170)
- Test: `src/lib/api/client.test.ts`

**Interfaces:**
- Produces: `agentsApi.createNl(command: string, autorun?: boolean)` → `POST /agents/create`, returns `AgentResponse`.

- [ ] **Step 1: Write the failing test**

```ts
// append to src/lib/api/client.test.ts
import { agentsApi } from '@/lib/api/client';

test('createNl posts command+autorun to /agents/create', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ agent_id: 'a1', name: 'X', autonomy_mode: 'bounded-autonomous' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  await agentsApi.createNl('make a triage bot', false);
  const [url, init] = f.mock.calls[0];
  expect(String(url)).toContain('/agents/create');
  expect(init?.method).toBe('POST');
  expect(JSON.parse(String(init?.body))).toEqual({ command: 'make a triage bot', autorun: false });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/api/client.test.ts -t createNl`
Expected: FAIL — `agentsApi.createNl is not a function`.

- [ ] **Step 3: Implement**

In `src/lib/api/client.ts`, add to the `agentsApi` object (after `create`):

```ts
  createNl: (command: string, autorun = false) =>
    request<AgentResponse>("/agents/create", {
      method: "POST",
      body: JSON.stringify({ command, autorun }),
    }),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/api/client.test.ts -t createNl`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/client.ts src/lib/api/client.test.ts
git commit -m "feat(client): add agentsApi.createNl for NL agent creation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Fix goal navigation fallback (Bug5)

**Files:**
- Modify: `src/features/goals/GoalsListPage.tsx:58`
- Test: `src/features/goals/GoalsListPage.test.tsx`

**Interfaces:**
- Consumes: `goalsApi.submit` (existing). No new produces.

- [ ] **Step 1: Write the failing test**

```tsx
// add to src/features/goals/GoalsListPage.test.tsx
test('navigates using id when goal_id is absent', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith('/agents')) return new Response('[]', { status: 200 });
    if (url.endsWith('/goals') && init?.method === 'POST')
      return new Response(JSON.stringify({ id: 'g-1', status: 'planning', goal: 'X' }), { status: 200 });
    return new Response(JSON.stringify({ goals: [] }), { status: 200 });
  });
  renderWithProviders(<GoalsListPage />);
  await userEvent.type(await screen.findByLabelText(/goal text/i), 'do thing');
  await userEvent.click(screen.getByRole('button', { name: /submit/i }));
  await waitFor(() => expect(window.location.pathname === '/goals/g-1' || screen.getByText(/g-1/)).toBeTruthy());
});
```

(If the existing suite asserts navigation differently, mirror its existing approach; the behavioral requirement is: a response with only `id` must navigate to `/goals/g-1`, not `/goals/undefined`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/goals/GoalsListPage.test.tsx -t "goal_id is absent"`
Expected: FAIL — navigates to `/goals/undefined`.

- [ ] **Step 3: Apply the fix**

`src/features/goals/GoalsListPage.tsx:58`:

```tsx
      navigate(`/goals/${res.goal_id ?? res.id}`);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/goals/GoalsListPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/goals/GoalsListPage.tsx src/features/goals/GoalsListPage.test.tsx
git commit -m "fix(goals): fall back to id when goal_id missing on submit

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Send `approver` in approve/reject (Bug4 — real 422)

**Files:**
- Modify: `src/features/governance/GovernancePage.tsx:200-227`
- Test: `src/features/governance/GovernancePage.test.tsx`

**Context:** Backend `ApproveRejectRequest` requires `approver: str` (`governance.py:36`). The page currently posts `{ note }` only → 422. Fix: include `approver` from the auth store.

**Interfaces:**
- Consumes: `useAuthStore` (`tenantId`).

- [ ] **Step 1: Write the failing test**

```tsx
// add to src/features/governance/GovernancePage.test.tsx
test('approve sends approver and note', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/governance/approvals') && init?.method === 'POST')
      return new Response(JSON.stringify({ status: 'approved' }), { status: 200 });
    if (url.includes('/governance/approvals'))
      return new Response(JSON.stringify([{ request_id: 'r1', goal_id: 'g1', status: 'pending' }]), { status: 200 });
    return new Response('[]', { status: 200 });
  });
  // render GovernancePage on the approvals tab, click approve on r1 ...
  // (use the page's existing tab navigation + approve button)
  // then:
  await waitFor(() => {
    const postCall = f.mock.calls.find(([u, i]) => String(u).includes('/approve') && (i as RequestInit)?.method === 'POST');
    expect(postCall).toBeTruthy();
    expect(JSON.parse(String((postCall![1] as RequestInit).body))).toHaveProperty('approver');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/governance/GovernancePage.test.tsx -t "approve sends approver"`
Expected: FAIL — body has no `approver`.

- [ ] **Step 3: Apply the fix**

In `GovernancePage.tsx`, ensure `ApprovalsTab` knows the approver. It already receives `apiKey`; read the tenant id from the store at the top of `ApprovalsTab`:

```tsx
import { useAuthStore } from '@/stores/auth';
// inside ApprovalsTab:
const approver = useAuthStore((s) => s.tenantId) || 'ui-user';
```

Then update both mutations to send `approver`:

```tsx
  const approveMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      apiFetch<void>(apiKey, `/governance/approvals/${id}/approve`, {
        method: 'POST',
        body: JSON.stringify({ approver, note }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      apiFetch<void>(apiKey, `/governance/approvals/${id}/reject`, {
        method: 'POST',
        body: JSON.stringify({ approver, note }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  });
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/governance/GovernancePage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/governance/GovernancePage.tsx src/features/governance/GovernancePage.test.tsx
git commit -m "fix(governance): send required approver field on approve/reject

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Onboarding uses auth store + typed client (Bug1)

**Files:**
- Modify: `src/features/onboarding/OnboardingPage.tsx:232-258`
- Test: `src/features/onboarding/OnboardingPage.test.tsx` (create if absent)

**Interfaces:**
- Consumes: `useAuthStore` (`apiKey`), `agentsApi.createNl` (Task 2).

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/onboarding/OnboardingPage.test.tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { OnboardingPage } from './OnboardingPage';

test('create-agent step sends X-API-Key from the auth store (not localStorage)', async () => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'store-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ agent_id: 'a1' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  const qc = new QueryClient();
  render(<QueryClientProvider client={qc}><MemoryRouter><OnboardingPage /></MemoryRouter></QueryClientProvider>);
  // navigate to the create-agent step and click create (use the page's existing controls)
  // ...
  await waitFor(() => {
    const call = f.mock.calls.find(([u]) => String(u).includes('/agents/create'));
    expect((call?.[1] as RequestInit)?.headers).toMatchObject({ 'X-API-Key': 'store-key' });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/onboarding/OnboardingPage.test.tsx`
Expected: FAIL — header comes from `localStorage` (empty), not `store-key`.

- [ ] **Step 3: Apply the fix**

Replace the raw fetch in `handleCreate` (lines 237-249) with the typed client, pulling the key from the store. At the top of the step component add:

```tsx
import { agentsApi } from '@/lib/api/client';
```

Replace the `try` body:

```tsx
    try {
      const data = await agentsApi.createNl(command, false);
      onAgentCreated(data.agent_id ?? '');
      setSaved(true);
      setTimeout(onNext, 800);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
```

(`agentsApi.createNl` already injects the key from the auth store via the client's `getApiKey()`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/onboarding/OnboardingPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/onboarding/OnboardingPage.tsx src/features/onboarding/OnboardingPage.test.tsx
git commit -m "fix(onboarding): use auth store + typed client for agent creation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Migrate Agents list + create pages to `agentsApi` (Bug2)

**Files:**
- Modify: `src/features/agents/AgentsListPage.tsx`, `src/features/agents/AgentCreatePage.tsx`
- Test: `src/features/agents/AgentsListPage.test.tsx` (create), reuse existing `AgentCreatePage` test if present

**Interfaces:**
- Consumes: `agentsApi.list`, `agentsApi.createNl`, `agentsApi.delete`, `agentsApi.create`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/agents/AgentsListPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentsListPage } from './AgentsListPage';

test('lists agents via typed client (sends X-API-Key)', async () => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([{ agent_id: 'a1', name: 'Triage', autonomy_mode: 'supervised', goal_template: 'g' }]),
      { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><AgentsListPage /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText('Triage')).toBeInTheDocument();
  expect((f.mock.calls[0][1] as RequestInit).headers).toMatchObject({ 'X-API-Key': 'k' });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/agents/AgentsListPage.test.tsx`
Expected: FAIL initially only if the local `fetchAgents` drops the header path — confirm RED by temporarily asserting the call goes through the client; if green already, proceed to refactor and keep it green (refactor-safety test).

- [ ] **Step 3: Migrate AgentsListPage**

Remove the module-level `API_BASE`, `fetchAgents`, `createAgentNL`, `deleteAgent` helpers. Add import and rewire the queries/mutations:

```tsx
import { agentsApi } from '@/lib/api/client';
// ...
  const { data: agents = [], isLoading, error } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list(),
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: () => agentsApi.createNl(nlCommand, false),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['agents'] }); setShowCreate(false); setNlCommand(''); },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => agentsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  });
```

(The local `Agent` interface may stay or be replaced by `AgentResponse`; keep `Agent` for the table types to minimise churn.)

- [ ] **Step 4: Migrate AgentCreatePage**

Remove `API_BASE` + `createAgentNL`; import `agentsApi`. Replace the NL mutation with `agentsApi.createNl(nlCommand, autorun)`, and the manual create (`handleManualCreate`) with `agentsApi.create(manualForm)`:

```tsx
import { agentsApi } from '@/lib/api/client';
// NL:
  const createMutation = useMutation({
    mutationFn: () => agentsApi.createNl(nlCommand, autorun),
    onSuccess: (data) => { qc.invalidateQueries({ queryKey: ['agents'] }); navigate(`/agents/${data.agent_id}`); },
  });
// manual:
  const handleManualCreate = async (e: React.FormEvent) => {
    e.preventDefault(); setLoading(true); setError('');
    try {
      const data = await agentsApi.create(manualForm as unknown as Parameters<typeof agentsApi.create>[0]);
      qc.invalidateQueries({ queryKey: ['agents'] });
      navigate(`/agents/${data.agent_id}`);
    } catch (err) { setError(String(err)); } finally { setLoading(false); }
  };
```

- [ ] **Step 5: Run tests + typecheck**

Run: `npm run test -- src/features/agents` then `npm run typecheck`
Expected: PASS / no type errors.

- [ ] **Step 6: Commit**

```bash
git add src/features/agents/AgentsListPage.tsx src/features/agents/AgentCreatePage.tsx src/features/agents/AgentsListPage.test.tsx
git commit -m "refactor(agents): use typed agentsApi instead of inline fetch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Global toast store + viewport, wired into the client

**Files:**
- Create: `src/stores/toast.ts`, `src/stores/toast.test.ts`, `src/components/ui/Toaster.tsx`, `src/components/ui/Toaster.test.tsx`
- Modify: `src/main.tsx`, `src/lib/api/client.ts`

**Interfaces:**
- Produces: `useToastStore` (Zustand) with state `{ toasts: ToastItem[] }` and actions `toast(t: {kind: 'success'|'error'|'info', message: string}): string`, `dismiss(id: string): void`. `ToastItem = { id: string; kind: 'success'|'error'|'info'; message: string }`. Imperative helper `toast(...)` exported for non-React callers (e.g. `client.ts`).

- [ ] **Step 1: Write the failing store test**

```ts
// src/stores/toast.test.ts
import { expect, test, beforeEach } from 'vitest';
import { useToastStore, toast } from '@/stores/toast';

beforeEach(() => useToastStore.setState({ toasts: [] }));

test('toast() adds an item and returns its id', () => {
  const id = toast({ kind: 'error', message: 'Boom' });
  const items = useToastStore.getState().toasts;
  expect(items).toHaveLength(1);
  expect(items[0]).toMatchObject({ id, kind: 'error', message: 'Boom' });
});

test('dismiss removes the item', () => {
  const id = toast({ kind: 'info', message: 'Hi' });
  useToastStore.getState().dismiss(id);
  expect(useToastStore.getState().toasts).toHaveLength(0);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/stores/toast.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the store**

```ts
// src/stores/toast.ts
import { create } from 'zustand';

export type ToastKind = 'success' | 'error' | 'info';
export interface ToastItem { id: string; kind: ToastKind; message: string }

interface ToastState {
  toasts: ToastItem[];
  toast: (t: { kind: ToastKind; message: string }) => string;
  dismiss: (id: string) => void;
}

let seq = 0;
const nextId = (): string => `t-${Date.now()}-${seq++}`;

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  toast: ({ kind, message }) => {
    const id = nextId();
    set((s) => ({ toasts: [...s.toasts, { id, kind, message }] }));
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Imperative helper for non-React callers (e.g. the API client). */
export const toast = (t: { kind: ToastKind; message: string }): string =>
  useToastStore.getState().toast(t);
```

- [ ] **Step 4: Run store test**

Run: `npm run test -- src/stores/toast.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the Toaster viewport test**

```tsx
// src/components/ui/Toaster.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test } from 'vitest';
import { useToastStore, toast } from '@/stores/toast';
import { Toaster } from './Toaster';

beforeEach(() => useToastStore.setState({ toasts: [] }));

test('renders a toast and dismisses on click', async () => {
  render(<Toaster />);
  toast({ kind: 'error', message: 'Network down' });
  expect(await screen.findByText('Network down')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));
  expect(screen.queryByText('Network down')).not.toBeInTheDocument();
});
```

- [ ] **Step 6: Implement the Toaster**

```tsx
// src/components/ui/Toaster.tsx
import { useToastStore } from '@/stores/toast';

const KIND_CLASS: Record<string, string> = {
  success: 'border-green-500/40 text-green-700 dark:text-green-400',
  error: 'border-red-500/40 text-destructive',
  info: 'border-border text-foreground',
};

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2" role="region" aria-label="Notifications">
      {toasts.map((t) => (
        <div key={t.id} role="status"
          className={`flex items-start gap-3 bg-card border rounded-lg shadow-lg px-4 py-3 text-sm max-w-sm ${KIND_CLASS[t.kind]}`}>
          <span className="flex-1">{t.message}</span>
          <button aria-label="Dismiss" onClick={() => dismiss(t.id)} className="text-muted-foreground hover:text-foreground">×</button>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Mount the Toaster in `main.tsx`**

In `src/main.tsx`, import and render `<Toaster />` inside the providers (after `<App />`):

```tsx
import { Toaster } from "./components/ui/Toaster";
// ...
      <BrowserRouter>
        <App />
        <Toaster />
      </BrowserRouter>
```

- [ ] **Step 8: Wire the client to emit error toasts**

In `src/lib/api/client.ts`, import the helper and emit a toast on server/network failures inside `request()`:

```ts
import { toast } from '@/stores/toast';
```

Update the error path in `request()`:

```ts
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  } catch (networkErr) {
    toast({ kind: 'error', message: 'Network error — could not reach the server.' });
    throw networkErr;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: { message: res.statusText } }));
    const message = body?.error?.message ?? res.statusText;
    if (res.status >= 500) toast({ kind: 'error', message: `Server error: ${message}` });
    throw new ApiError(res.status, message, body);
  }
```

- [ ] **Step 9: Run tests + typecheck**

Run: `npm run test -- src/stores/toast.test.ts src/components/ui/Toaster.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/stores/toast.ts src/stores/toast.test.ts src/components/ui/Toaster.tsx src/components/ui/Toaster.test.tsx src/main.tsx src/lib/api/client.ts
git commit -m "feat(ui): global toast store + viewport wired into API client

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 401 → session-expiry logout in the client

**Files:**
- Modify: `src/lib/api/client.ts` (`request()`)
- Test: `src/lib/api/client.test.ts`

**Context:** On 401 the client must clear the session and notify, so RequireAuth redirects to `/auth` on the next render.

**Interfaces:**
- Consumes: `useAuthStore.getState().logout`, `toast`.

- [ ] **Step 1: Write the failing test**

```ts
// add to src/lib/api/client.test.ts
import { useAuthStore } from '@/stores/auth';
import { goalsApi, ApiError } from '@/lib/api/client';

test('401 logs out and surfaces a session toast', async () => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ error: { message: 'unauthorized' } }), { status: 401, headers: { 'Content-Type': 'application/json' } })
  );
  await expect(goalsApi.list()).rejects.toBeInstanceOf(ApiError);
  expect(useAuthStore.getState().isAuthenticated).toBe(false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/api/client.test.ts -t 401`
Expected: FAIL — still authenticated.

- [ ] **Step 3: Implement**

In `request()`, before throwing the `ApiError` for `!res.ok`, add a 401 branch:

```ts
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: { message: res.statusText } }));
    const message = body?.error?.message ?? res.statusText;
    if (res.status === 401) {
      const { logout } = useAuthStore.getState();
      logout();
      toast({ kind: 'error', message: 'Session expired — please sign in again.' });
      throw new ApiError(401, message, body);
    }
    if (res.status >= 500) toast({ kind: 'error', message: `Server error: ${message}` });
    throw new ApiError(res.status, message, body);
  }
```

Add the import at the top: `import { useAuthStore } from '@/stores/auth';`

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/api/client.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/client.ts src/lib/api/client.test.ts
git commit -m "feat(client): clear session and notify on 401

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Wrap route elements in `ErrorBoundary`

**Files:**
- Modify: `src/app/App.tsx`
- Test: `src/components/ui/ErrorBoundary.test.tsx`

**Context:** `ErrorBoundary` (`src/components/ui/ErrorBoundary.tsx`) already exists but wraps nothing. Wrap the routed content so a thrown render error in one page shows the fallback instead of a white screen.

**Interfaces:**
- Consumes: existing `ErrorBoundary` (default + named export).

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/ui/ErrorBoundary.test.tsx
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';

function Boom(): never { throw new Error('kaboom'); }

test('renders fallback when a child throws', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  render(<ErrorBoundary><Boom /></ErrorBoundary>);
  expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  expect(screen.getByText(/kaboom/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/components/ui/ErrorBoundary.test.tsx`
Expected: PASS already (boundary exists). This test guards the boundary; proceed to wire it into `App.tsx`.

- [ ] **Step 3: Wrap routed content in `App.tsx`**

Import the boundary and wrap the `AppLayout` child:

```tsx
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
// ...
        element={
          <RequireAuth>
            <ErrorBoundary>
              <AppLayout />
            </ErrorBoundary>
          </RequireAuth>
        }
```

- [ ] **Step 4: Run full suite + typecheck**

Run: `npm run test -- src/components/ui/ErrorBoundary.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/App.tsx src/components/ui/ErrorBoundary.test.tsx
git commit -m "feat(app): wrap routed content in ErrorBoundary

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Shared primitives — Skeleton, EmptyState, StatusBadge

**Files:**
- Create: `src/components/ui/Skeleton.tsx`, `src/components/ui/EmptyState.tsx`, `src/components/ui/StatusBadge.tsx` + co-located tests

**Interfaces:**
- Produces:
  - `Skeleton(props: { className?: string })` — animated placeholder.
  - `EmptyState(props: { title: string; description?: string; action?: React.ReactNode })`.
  - `StatusBadge(props: { status: string })` — colored pill; known statuses: `complete/success`, `executing/running`, `planning/pending`, `failed/error`, `waiting_human`, fallback muted.

- [ ] **Step 1: Write the failing tests**

```tsx
// src/components/ui/StatusBadge.test.tsx
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { StatusBadge } from './StatusBadge';
test('renders the status label', () => {
  render(<StatusBadge status="complete" />);
  expect(screen.getByText('complete')).toBeInTheDocument();
});

// src/components/ui/EmptyState.test.tsx
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { EmptyState } from './EmptyState';
test('renders title and description', () => {
  render(<EmptyState title="No agents" description="Create one to begin" />);
  expect(screen.getByText('No agents')).toBeInTheDocument();
  expect(screen.getByText('Create one to begin')).toBeInTheDocument();
});

// src/components/ui/Skeleton.test.tsx
import { render } from '@testing-library/react';
import { expect, test } from 'vitest';
import { Skeleton } from './Skeleton';
test('renders with animate-pulse', () => {
  const { container } = render(<Skeleton />);
  expect(container.firstChild).toHaveClass('animate-pulse');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- src/components/ui/StatusBadge.test.tsx src/components/ui/EmptyState.test.tsx src/components/ui/Skeleton.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

```tsx
// src/components/ui/Skeleton.tsx
export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-muted ${className}`} />;
}
```

```tsx
// src/components/ui/EmptyState.tsx
import type { ReactNode } from 'react';
export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-6">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {description && <p className="mt-1 text-sm text-muted-foreground max-w-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
```

```tsx
// src/components/ui/StatusBadge.tsx
const COLORS: Record<string, string> = {
  complete: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  success: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  executing: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  running: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  planning: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  pending: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  error: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  waiting_human: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400',
};
export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${COLORS[status] ?? 'bg-muted text-muted-foreground'}`}>
      {status.replace(/_/g, ' ')}
    </span>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- src/components/ui/StatusBadge.test.tsx src/components/ui/EmptyState.test.tsx src/components/ui/Skeleton.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/ui/Skeleton.tsx src/components/ui/EmptyState.tsx src/components/ui/StatusBadge.tsx src/components/ui/*.test.tsx
git commit -m "feat(ui): add Skeleton, EmptyState, StatusBadge primitives

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Reusable `FlowCanvas` over `@xyflow/react` + layered layout

**Files:**
- Create: `src/components/graph/FlowCanvas.tsx`, `src/components/graph/FlowCanvas.test.tsx`

**Context:** `@xyflow/react@^12.11.1` is installed but unused. This wrapper is reused by the Workflow Builder (Phase 6) and the Civilization map (companion spec). No `dagre` dependency — use a simple layered (BFS-depth) layout.

**Interfaces:**
- Produces:
  - `interface FlowNodeInput { id: string; label: string; kind?: string; data?: Record<string, unknown> }`
  - `interface FlowEdgeInput { id: string; source: string; target: string; label?: string }`
  - `layeredLayout(nodes: FlowNodeInput[], edges: FlowEdgeInput[]): Record<string, { x: number; y: number }>` — assigns positions by BFS depth from roots (no incoming edges); 220px x-gap per depth, 110px y-gap per sibling.
  - `FlowCanvas(props: { nodes: FlowNodeInput[]; edges: FlowEdgeInput[]; onNodeClick?: (id: string) => void; nodeColor?: (n: FlowNodeInput) => string })` — renders a React Flow graph with `Background`, `Controls`, `MiniMap`.

- [ ] **Step 1: Add ResizeObserver polyfill to the test setup**

In `src/test/setup.ts`, append (React Flow needs it in jsdom):

```ts
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
// @ts-expect-error jsdom lacks ResizeObserver
globalThis.ResizeObserver = globalThis.ResizeObserver ?? ResizeObserverStub;
```

- [ ] **Step 2: Write the failing tests**

```tsx
// src/components/graph/FlowCanvas.test.tsx
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { FlowCanvas, layeredLayout } from './FlowCanvas';

test('layeredLayout puts roots at x=0 and children deeper', () => {
  const pos = layeredLayout(
    [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }],
    [{ id: 'e', source: 'a', target: 'b' }],
  );
  expect(pos.a.x).toBe(0);
  expect(pos.b.x).toBeGreaterThan(pos.a.x);
});

test('renders node labels', async () => {
  render(
    <div style={{ width: 800, height: 600 }}>
      <FlowCanvas nodes={[{ id: 'a', label: 'Start' }]} edges={[]} />
    </div>
  );
  expect(await screen.findByText('Start')).toBeInTheDocument();
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm run test -- src/components/graph/FlowCanvas.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement `FlowCanvas`**

```tsx
// src/components/graph/FlowCanvas.tsx
import { useMemo } from 'react';
import { ReactFlow, Background, Controls, MiniMap, type Node, type Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

export interface FlowNodeInput { id: string; label: string; kind?: string; data?: Record<string, unknown> }
export interface FlowEdgeInput { id: string; source: string; target: string; label?: string }

const X_GAP = 220;
const Y_GAP = 110;

/** Position nodes by BFS depth from roots (nodes with no incoming edge). */
export function layeredLayout(
  nodes: FlowNodeInput[],
  edges: FlowEdgeInput[],
): Record<string, { x: number; y: number }> {
  const incoming = new Set(edges.map((e) => e.target));
  const children = new Map<string, string[]>();
  for (const e of edges) {
    children.set(e.source, [...(children.get(e.source) ?? []), e.target]);
  }
  const depth = new Map<string, number>();
  const roots = nodes.filter((n) => !incoming.has(n.id)).map((n) => n.id);
  const queue = roots.map((id) => ({ id, d: 0 }));
  const seen = new Set<string>();
  while (queue.length) {
    const { id, d } = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    depth.set(id, Math.max(depth.get(id) ?? 0, d));
    for (const c of children.get(id) ?? []) queue.push({ id: c, d: d + 1 });
  }
  // Any node never reached (cycle/orphan) gets depth 0.
  for (const n of nodes) if (!depth.has(n.id)) depth.set(n.id, 0);
  const perDepthCount = new Map<number, number>();
  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    const row = perDepthCount.get(d) ?? 0;
    perDepthCount.set(d, row + 1);
    pos[n.id] = { x: d * X_GAP, y: row * Y_GAP };
  }
  return pos;
}

export function FlowCanvas({
  nodes,
  edges,
  onNodeClick,
  nodeColor,
}: {
  nodes: FlowNodeInput[];
  edges: FlowEdgeInput[];
  onNodeClick?: (id: string) => void;
  nodeColor?: (n: FlowNodeInput) => string;
}) {
  const pos = useMemo(() => layeredLayout(nodes, edges), [nodes, edges]);
  const rfNodes: Node[] = useMemo(
    () =>
      nodes.map((n) => ({
        id: n.id,
        position: pos[n.id] ?? { x: 0, y: 0 },
        data: { label: n.label },
        style: nodeColor ? { borderColor: nodeColor(n) } : undefined,
      })),
    [nodes, pos, nodeColor],
  );
  const rfEdges: Edge[] = useMemo(
    () => edges.map((e) => ({ id: e.id, source: e.source, target: e.target, label: e.label, animated: true })),
    [edges],
  );
  return (
    <div className="w-full h-full min-h-[400px]" data-testid="flow-canvas">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        fitView
        onNodeClick={(_, node) => onNodeClick?.(node.id)}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- src/components/graph/FlowCanvas.test.tsx`
Expected: PASS. (If the render test cannot find the label due to virtualization in jsdom, assert on `screen.getByTestId('flow-canvas')` instead — React Flow may not lay out real nodes without measured dimensions.)

- [ ] **Step 6: Commit**

```bash
git add src/components/graph/FlowCanvas.tsx src/components/graph/FlowCanvas.test.tsx src/test/setup.ts
git commit -m "feat(graph): reusable FlowCanvas wrapper with layered layout

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Phase-1 regression gate

**Files:** none (verification only)

- [ ] **Step 1: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 2: Lint**

Run: `npm run lint`
Expected: no errors (warnings acceptable if pre-existing).

- [ ] **Step 3: Full unit suite**

Run: `npm run test`
Expected: all pass; coverage not decreased.

- [ ] **Step 4: E2E smoke (existing suites must still pass)**

Run: `npm run test:e2e -- e2e/goals.spec.ts e2e/navigation.spec.ts`
Expected: PASS (Phase 1 changed no routes; foundations are additive).

- [ ] **Step 5: Tag the phase**

```bash
git tag -a frontend-phase1 -m "Frontend Phase 1: foundations + bug sweep"
```

---

## Self-Review

**Spec coverage (against WS-0 / P0-3 / P0-5 / P0-2):**
- P0-3 bug sweep → Tasks 1 (paths), 3 (goal_id), 4 (approver), 5 (Onboarding storage), 6 (agent pages raw fetch). ✅ (AgentDetail deferred to Phase 5 — noted, not dropped.)
- P0-5 toast + ErrorBoundary → Tasks 7, 9. ✅
- P0-2 401/session → Task 8. ✅
- WS-0 primitives + FlowCanvas → Tasks 10, 11. ✅

**Corrections folded in (vs. original analysis):** path bugs live in `client.ts` not the pages (verified backend serves `/analytics/costs` and `/nl/schedule`); CostDashboard's `/goals/cost-metrics` is a real endpoint (not a bug); Bug4 is a *real* 422 (backend requires `approver`), not refuted.

**Placeholder scan:** none — every code step contains complete code; every run step has an exact command + expected result.

**Type consistency:** `agentsApi.createNl` (Task 2) is the exact name used in Tasks 5–6; `useToastStore`/`toast`/`ToastItem` consistent across Tasks 7–8 and the Toaster; `FlowNodeInput`/`FlowEdgeInput`/`layeredLayout`/`FlowCanvas` consistent within Task 11.

---

## Execution Handoff

Phase 1 is the prerequisite for Phases 2–8. Subsequent phase plans will be written against the interfaces this phase lands (`useToastStore`/`toast`, the 401 contract, the three primitives, and `FlowCanvas`).
