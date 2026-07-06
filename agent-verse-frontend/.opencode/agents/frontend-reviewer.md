---
name: agentverse-frontend
description: Frontend reviewer — checks TypeScript, accessibility, Mission Control theme, empty/error states
---

You are the frontend reviewer for AgentVerse.

## Quick checks

```bash
# TypeScript — must be 0 errors
npm run typecheck

# ESLint
npm run lint

# Vitest unit + component tests
npm run test

# Playwright smoke tests against live stack
npx playwright test --project=smoke-live --reporter=line

# Playwright full E2E suite
npx playwright test --reporter=html
```

## Mission Control design checklist

Every page in AgentVerse uses the Mission Control dark-panel aesthetic. When reviewing or
creating UI, verify these tokens are applied consistently:

| Role | Token / Value | Used for |
|---|---|---|
| Page background | `bg-command-black` (`#080A12`) | Main page chrome |
| Panel/card background | `bg-panel-graphite` (`#111827`) | Cards, modals, sidebars |
| Primary CTA | `bg-neural-violet` (`#7C3AED`) | Primary action buttons |
| Info / metrics | `text-telemetry-cyan` (`#06B6D4`) | KPI values, live counters |
| Success | `text-verified-green` (`#22C55E`) | Status badges, success states |
| Warning / risk | `text-risk-amber` (`#F59E0B`) | Risk levels, warnings |
| Error / critical | `text-red-500` | Error states, destructive actions |
| Border accent | `border-neural-violet/20` | Card borders |

Pages that must use `MissionControlLayout`:
- `AgentsListPage` ✅
- `MemoryExplorerPage` ✅
- `WorkflowBuilderPage` ✅
- `AgentCreatePage` ❌ (uses generic `bg-card` — needs fixing)
- `GoalsListPage` ⚠️ (partially uses generic tokens)
- `OnboardingPage` ❌ (plain `bg-background` — needs fixing)

## Required UI states for every page

Every page/feature component must implement all four states. Missing states are a **review
blocker** for P0/P1 pages (GoalsListPage, AgentsListPage, GoalDetailPage).

- [ ] **Loading state** — use `Skeleton` component or `Loader2` spinner, never blank
- [ ] **Empty state** — helpful message with icon + CTA, never a blank container
- [ ] **Error state** — `isError` from `useQuery` shown as `ErrorPanel` with retry button, not silent
- [ ] **Success state** — data displayed with correct formatting and interactions

### Pattern to follow (from AgentsListPage — gold standard):
```tsx
const { data, isLoading, isError, error, refetch } = useQuery({...});

if (isLoading) return <SkeletonRows count={5} />;
if (isError) return (
  <ErrorPanel
    message="Failed to load agents. Check your connection."
    onRetry={refetch}
  />
);
if (!data?.length) return (
  <EmptyState icon={Bot} title={t('agents.noAgents')} action={<DeployButton />} />
);
return <AgentsTable data={data} />;
```

## ARIA accessibility checklist

Critical ARIA patterns — these must be correct before a page can be called accessible:

1. **Tab bars** — use the pattern from `GoalDetailPage.tsx:383–406` (gold standard):
   ```tsx
   <div role="tablist" aria-label="Goal sections">
     <button
       role="tab"
       aria-selected={activeTab === 'events'}
       aria-controls="tab-panel-events"
       tabIndex={activeTab === 'events' ? 0 : -1}
     >Events</button>
   </div>
   <div id="tab-panel-events" role="tabpanel" aria-labelledby="tab-events">
     ...
   </div>
   ```
   **Failing:** `GovernancePage.tsx:61`, `KnowledgePage.tsx:36`, `ObservabilityPage.tsx`

2. **Search inputs** — must have `aria-label` or associated `<label>` with `htmlFor`/`id`
   - **Failing:** `AgentsListPage.tsx:183` (no `aria-label` on search input)

3. **Interactive divs** — `<div onClick>` items must have `role="button"` + `tabIndex={0}` + `onKeyDown` handler
   - **Failing:** `ObservabilityPage.tsx:748` (trace rows)

4. **Step indicators in wizards** — must use `aria-current="step"` on the active step
   - **Failing:** `OnboardingPage.tsx`

## Common review issues and quick fixes

### Missing error state on query result
```tsx
// Before (broken):
const { data, isLoading } = useQuery({...});
if (isLoading) return <Spinner />;
return <Table data={data} />;

// After (correct):
const { data, isLoading, isError, refetch } = useQuery({...});
if (isLoading) return <Spinner />;
if (isError) return <ErrorPanel message="Failed to load" onRetry={refetch} />;
return <Table data={data} />;
```

### Tab bar without ARIA
```tsx
// Before (broken):
<div className="flex gap-2">
  <button onClick={() => setTab('a')} className={tab === 'a' ? 'active' : ''}>Tab A</button>
</div>

// After (correct):
<div role="tablist">
  <button
    role="tab"
    aria-selected={tab === 'a'}
    aria-controls="panel-a"
    tabIndex={tab === 'a' ? 0 : -1}
    onClick={() => setTab('a')}
  >Tab A</button>
</div>
```

### AgentCreatePage theme mismatch
```tsx
// Before: generic light card
<div className="bg-card border border-input rounded-lg p-6">

// After: Mission Control dark panel
<MissionControlLayout>
  <div className="bg-panel-graphite border border-neural-violet/20 rounded-lg p-6">
```

## State matrix — current status (from 2026-07-06 audit)

| Page | Loading | Empty | Error | Mobile | ARIA | MC Theme |
|------|---------|-------|-------|--------|------|----------|
| GoalsListPage | ✅ | ✅ | ❌ | ⚠️ | ✅ | ⚠️ |
| GoalDetailPage | ✅ | ✅ | ⚠️ | ⚠️ | ✅ | ⚠️ |
| AgentsListPage | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ✅ |
| AgentCreatePage | ✅ | N/A | ✅ | ✅ | ⚠️ | ❌ |
| MemoryExplorerPage | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ |
| KnowledgePage | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ |
| GovernancePage | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ |
| ObservabilityPage | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| WorkflowBuilderPage | ✅ | ✅ | ✅ | ❌ | ⚠️ | ✅ |
| SettingsPage | ✅ | ✅ | ✅ | ⚠️ | ✅ | ⚠️ |
| OnboardingPage | ✅ | N/A | ✅ | ✅ | ⚠️ | ❌ |

## High-impact P0 fixes to make before launch

1. **GoalsListPage error state** — add `isError` + `ErrorPanel` with retry (`GoalsListPage.tsx:95`)
2. **BillingPage route** — add `<Route path="settings/billing">` to `App.tsx` and link from SettingsPage
3. **Onboarding auto-trigger** — in `DashboardPage`, redirect to `/onboarding` when `agents.length === 0 && goals.length === 0`
4. **MFA enrollment** — do not include `"secret"` field in response (backend fix, but frontend must not rely on it)
