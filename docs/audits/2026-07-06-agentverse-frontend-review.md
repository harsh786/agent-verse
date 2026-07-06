# AgentVerse Frontend UX Review
**Date:** 2026-07-06  
**Reviewer:** Frontend Design Review (automated code analysis)  
**Codebase:** `agent-verse-frontend/` — React 19, TypeScript, Vite, TanStack Query, Zustand, Tailwind  
**Scope:** Phase 6 — Full UX audit of all primary feature pages  

---

## Methodology

Every page below was reviewed by reading its actual TSX source code. No guessing.  
Source files cited with line references where relevant.

---

## 1. Goals

### GoalsListPage (`src/features/goals/GoalsListPage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Skeleton table with 5 rows during `isLoading` (lines 258–281) |
| Empty state | ✅ | Three distinct rich empty states: no goals at all (Zap icon), filter empty (CheckCircle2), search no-match (Search icon) with clear actions (lines 282–321) |
| Error state | ❌ | `useQuery` result binds only `isLoading` — no `isError` check. A 401, 500, or network timeout renders a blank table body silently |
| Mobile responsive | ⚠️ | Full-width table with `max-w-lg` goal text. Table will overflow horizontally on viewports < 640px. No card/list fallback layout |
| Accessibility (ARIA) | ✅ | `aria-label="Search goals"`, `aria-label="Select all goals on page"`, `aria-label="Cancel goal"`, `aria-hidden` on decorative icons (lines 201–414) |
| Mission Control theme | ⚠️ | Uses generic `bg-card / border-border` tokens, not the dark command-center palette used by AgentsListPage and MemoryExplorerPage. Inconsistent visual language |

**What's good:**
- URL-backed filter/search/sort/page state (bookmarkable, back-navigable)
- Bulk cancel with optimistic per-row spinner state (`cancellingIds` set at line 57)
- Status count badges on filter pills
- 5-second polling refetch interval for live status updates
- Relative timestamp + event count displayed per row

### GoalDetailPage (`src/features/goals/GoalDetailPage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Full-page Loader2 spinner during initial fetch (line 507); per-section skeletons on Events/Eval tabs |
| Empty state | ✅ | "Waiting for events…" vs "Goal finished. No live events." depending on status (lines 776–781); EmptyState component for event log (line 853) |
| Error state | ⚠️ | `if (!goal)` shows "Goal not found" (line 515) but does not distinguish 404 from network errors. Mutation errors (approve/reject/pause/cancel) correctly show inline messages |
| Mobile responsive | ⚠️ | `max-w-4xl` container is fine, but the tab bar wraps poorly on narrow viewports. Analysis buttons (DNA/Diff/Ghost Run) in a flex row may overflow |
| Accessibility (ARIA) | ✅ | Full ARIA tablist with `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls`, `aria-labelledby`, keyboard navigation (ArrowLeft/Right/Home/End) at lines 383–406 |
| Mission Control theme | ⚠️ | Uses light-mode `bg-card/border-border` tokens. Execution timeline is on the dark side but page chrome is generic |

**What's good:**
- SSE live event stream via `useGoalStream` with `● Live / ○ Disconnected` indicator
- Live LLM token streaming display with blinking cursor (lines 800–824)
- `ExecutionTimeline` + `ToolCallInspector` components for visual execution trace
- HITL approval panel with action/risk level display and notes textarea
- Elapsed timer for in-progress goals (`ElapsedTimer` at line 303)
- `LiveCostTicker` showing running cost
- 7-dimension eval scorecard with progress bars (lines 969–1035)
- "Retry from step" mutation on failed events (lines 267–280)
- Debug raw state panel in DEV (lines 1052–1061)

---

## 2. Agents

### AgentsListPage (`src/features/agents/AgentsListPage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Skeleton rows with 5 columns during fetch (lines 265–288) |
| Empty state | ✅ | Bot icon + translated `t('agents.noAgents')` + deploy CTA (lines 293–307) |
| Error state | ✅ | `error` check renders "Failed to load agents. Check your connection." (lines 289–291) |
| Mobile responsive | ⚠️ | 6-column table. `sm:flex-row` on filter controls is good but the table itself has no card fallback. Small viewports will overflow |
| Accessibility (ARIA) | ✅ | `role="button"` + `aria-label="View agent {name}"` on each row; search input unlabelled — no `aria-label` or associated `<label>` on the search input (line 183) |
| Mission Control theme | ✅ | Uses `MissionControlLayout`, `bg-panel-graphite`, `border-neural-violet`, dark palette consistently |

**What's good:**
- URL-backed filter/sort/page state
- Autonomy mode badges with human-readable labels
- Confirm-before-delete modal via `ConfirmModal` component
- NL agent creation inline modal with good UX copy
- Pagination only rendered when needed (`filteredAgents.length > PAGE_SIZE`)

### AgentCreatePage (`src/features/agents/AgentCreatePage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | `createMutation.isPending` disables submit, button label changes to "Creating…" |
| Empty state | N/A | Form page — not applicable |
| Error state | ✅ | `role="alert"` on error paragraphs for both NL and manual modes |
| Mobile responsive | ✅ | `max-w-2xl` form with stacked fields, no table layout |
| Accessibility (ARIA) | ⚠️ | Labels are present for most fields but some inputs (connector IDs, max iterations) lack associated `htmlFor`/`id` pairs. Tab switcher buttons lack `role="tab"` |
| Mission Control theme | ❌ | Uses generic `bg-card/border-input` tokens, no MissionControlLayout wrapper. Inconsistent with AgentsListPage |

**Autonomy mode UX:**
- `supervised`: option text "Supervised (every action needs approval)" — adequate
- `bounded-autonomous`: "Bounded Autonomous (approve high-risk only)" — adequate
- `fully-autonomous`: "Fully Autonomous (requires eval suite)" — mentions eval requirement but gives no link to set up eval. Users may get stuck

---

## 3. Memory

### MemoryExplorerPage (`src/features/memory/MemoryExplorerPage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Skeleton rows per section during data fetching |
| Empty state | ✅ | `EmptyState` component with context-aware description (lines 501–503) |
| Error state | ⚠️ | Errors surfaced via `toast()` only — no in-page error banner for list query failures |
| Mobile responsive | ✅ | `max-w-4xl` container with flex-wrap on controls |
| Accessibility (ARIA) | ✅ | Labels with `htmlFor`/`id` on all modal fields; `aria-label` on action buttons; `aria-label` on semantic recall input (line 389) |
| Mission Control theme | ✅ | `MissionControlLayout`, dark palette, neural-violet accents |

**What's good:**
- Semantic recall with confidence bars (vector similarity UX is excellent)
- Add + Edit + Delete memory all functional
- Type filter pills + pagination
- Tool reliability table with color-coded rows/progress bars
- Execution memory collapsible section with `aria-expanded`
- Memory provenance: source shown in recall results (line 429) — but truncated to 12 chars with `…` (no tooltip for full URL)

---

## 4. Knowledge / RAG

### KnowledgePage (`src/features/knowledge/KnowledgePage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Spinner per tab, per-section loading states |
| Empty state | ✅ | Database icon + "No collections yet" (line 125); "No results found" for search (line 747) |
| Error state | ⚠️ | Errors go to `toast()` in most mutations; Collections loading renders `Loader2` but no error UI for collection list failures |
| Mobile responsive | ⚠️ | Collections grid is `sm:grid-cols-2 lg:grid-cols-3` — good. Ingest form has `sm:grid-cols-5` which may squeeze on tablet |
| Accessibility (ARIA) | ⚠️ | Tab navigation is implemented with standard buttons but lacks `role="tablist"` / `role="tab"` / `aria-controls`. Tab bar variable `Tab` type at line 36 is functional but not ARIA-annotated |
| Mission Control theme | ⚠️ | Mixes dark and light palette — collections/search/analytics tabs use light tokens (`bg-card`), Ask AI uses violet accents, Ingest uses `bg-gradient-to-br from-violet-50` |

**Document management:**
- Upload: ✅ Drag-and-drop zone + file picker (`<input type="file">`) for PDF/DOCX/text
- Search: ✅ `Enter` key submission, query-word highlight via `**` splitting
- Citations: ✅ `data-testid="citations-panel"` with indexed citations, confidence scores, source URLs
- Streaming RAG: ✅ Single-collection streaming via SSE with blinking cursor
- RPA Scraper: ✅ Playwright-backed URL ingestion with per-URL result breakdown

---

## 5. Governance

### GovernancePage (`src/features/governance/GovernancePage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Loader2 spinners for each tab's data; `data-testid="loading"` on approvals (line 972) |
| Empty state | ✅ | `data-testid="approvals-empty"` (line 978) and `data-testid="policies-empty"` (line 699); rich icons with explanatory copy |
| Error state | ⚠️ | Audit chain verification error goes to toast (line 1103); policy mutations go to toast. No in-page error banners |
| Mobile responsive | ⚠️ | Policies table is wide; time-window hour grid (24 buttons) will overflow at 320px |
| Accessibility (ARIA) | ⚠️ | `GovTab` switcher at lines 61–65 uses plain `<button>` elements without `role="tablist"` / `role="tab"`. Contrast with GoalDetailPage's fully ARIA-compliant tabs |
| Mission Control theme | ⚠️ | Light theme (`bg-card / border-border`) throughout. Emergency Stop banner is correctly alarming. Inconsistent with the dark-themed Memory/Agent pages |

**What's good:**
- Emergency Stop kill-switch with two-step confirmation (always visible)
- Live SSE updates for both policies and approvals streams
- SLA stats dashboard (pending/approved/denied/within-SLA)
- Batch approve/reject with count feedback
- Policy simulator modal (test tool patterns against current rules)
- Policy version history + rollback
- Audit export in JSON and CSV formats
- Tamper-evident chain verification button
- Time-window restrictions (allowed hours, allowed weekdays)

---

## 6. Observability

### ObservabilityPage (`src/features/observability/ObservabilityPage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Skeleton pulse animation for health cards (line 291); metric loading shown via disabled refresh button |
| Empty state | ✅ | Per-chart empty messages with actionable hints; "No spans recorded yet" with explanation |
| Error state | ✅ | `data-testid="health-error"` div (line 297); health check failures clearly identified |
| Mobile responsive | ✅ | `grid-cols-2 md:grid-cols-4` KPI grid; Recharts `ResponsiveContainer` throughout |
| Accessibility (ARIA) | ⚠️ | Tab switcher ('overview'/'metrics'/'traces'/'logs') lacks ARIA tablist semantics. Trace rows are interactive divs without keyboard focus |
| Mission Control theme | ✅ | Uses `hsl(var(--popover))` for Recharts tooltips matching app theme; `bg-card / border-border` tokens |

**What's good:**
- Recharts integration: area chart (goal throughput), line chart (cost over time, latency trend), bar charts (duration percentiles, tool calls, LLM tokens)
- Auto-refresh with time-range-aware intervals (10s for 1h, 10m for 30d)
- Raw Prometheus metrics in `<details>` expander for debugging
- Real-time SSE log stream with pause-on-hover, level filter, log search, export
- Custom time-range picker
- Platform architecture diagram (static but informative)
- Grafana integration link (env-var gated)
- `TraceExplorer` component for span-level waterfall

---

## 7. Workflow Builder

### WorkflowBuilderPage (`src/features/workflow-builder/WorkflowBuilderPage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Toolbar feedback; React.lazy + Suspense fallback |
| Empty state | ✅ | Empty canvas shows node palette ready to drag from |
| Error state | ✅ | `toast()` for generate/save/run failures; per-node `status: 'failed'` animation |
| Mobile responsive | ❌ | Canvas-based tool; unusable on mobile. No responsive fallback or message |
| Accessibility (ARIA) | ⚠️ | `aria-label` on toolbar buttons and workflow name input; however, the ReactFlow canvas itself has no keyboard-accessible node manipulation path |
| Mission Control theme | ✅ | `command-black` background, `neural-violet` accents, `bg-panel-graphite` sidebar |

**What's good:**
- ReactFlow with drag-drop palette, MiniMap, Controls
- 11 node types with type-specific inspector panels
- Undo/redo with history stack (Ctrl+Z/Y)
- Ctrl+C/V node copy-paste
- 6 pre-built workflow templates (Incident Response, PR Review, Daily Report, Bug Triage, Employee Onboarding, Cost Alert)
- NL workflow generation from natural language description
- Real-time validation (missing trigger, isolated nodes)
- Dry Run mode with per-node result data
- Auto-save with 2s debounce + `● Saved / ● Unsaved / ● Save failed` indicator
- `ToolSelector` with `<datalist>` autocomplete from live tool registry

---

## 8. Settings

### SettingsPage (`src/features/settings/SettingsPage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Per-section loading text; `Saving…` inline during mutations |
| Empty state | ✅ | "No API keys" message; empty sessions list gracefully handled |
| Error state | ✅ | Per-section error paragraphs; `saveMutation.isError` shown inline in LLM form |
| Mobile responsive | ⚠️ | Sidebar + content layout (`flex gap-6`) collapses poorly below `md`. The `w-48` left nav will crowd content |
| Accessibility (ARIA) | ✅ | `aria-label="Settings sections"` on nav; settings tab buttons have icons with visible labels |
| Mission Control theme | ⚠️ | Light theme (`bg-card`) throughout; no dark palette. Consistent within settings but not with Mission Control pages |

**What's good:**
- 7 tabs: General, LLM Providers, API Keys, Security, Notifications, Appearance, Danger Zone
- LLM Provider: provider dropdown with auto-suggest models, masked API key display, base URL for custom endpoints
- API Keys: one-time key reveal banner with copy button, rotate, delete with confirm
- MFA: delegated to `MFASettings` component
- Sessions: active session list with revoke
- Notifications: toggle preferences with localStorage fallback
- Appearance: light/dark/system theme + density selector
- Danger Zone: export-all-data (JSON download) + type-"DELETE"-to-confirm account deletion
- Missing: No `/billing` route in `App.tsx` — `BillingPage.tsx` exists but is not routed

---

## 9. Onboarding

### OnboardingPage (`src/features/onboarding/OnboardingPage.tsx`)

| Signal | Rating | Notes |
|--------|--------|-------|
| Loading state | ✅ | Per-step `Loader2` spinners during async operations |
| Empty state | N/A | Linear flow — not applicable |
| Error state | ✅ | Per-step error state with inline error paragraphs |
| Mobile responsive | ✅ | `max-w-lg` centered layout with stacked form fields |
| Accessibility (ARIA) | ⚠️ | Step indicators use div/span without `aria-current="step"` or progress element semantics |
| Mission Control theme | ❌ | Plain `bg-background` with blue Zap logo. Not styled in any distinctive way |

**What's good:**
- 4-step wizard: LLM → Connector → Agent → First Goal
- Skip connector step option
- Skip-all button to go straight to dashboard
- Step indicators with green checkmarks for completed steps
- Agent ID passed from step 3 to step 4 for goal submission

**What's missing:**
- No "back" navigation between steps
- No persistence of partial progress (refresh loses state)
- Not triggered automatically for new users — must navigate manually to `/onboarding`
- No check for "already completed" (could re-run onboarding after setup)
- No video/demo embed for value communication

---

## 10. Navigation

### App.tsx + Sidebar

| Signal | Rating | Notes |
|--------|--------|-------|
| All features accessible | ✅ | 50+ routes defined in `App.tsx` (lines 185–244) with lazy loading for heavier pages |
| Sidebar/navbar | ✅ | Collapsible sidebar with 9 groups, search, pending approvals badge |
| Error boundaries | ✅ | `RouteErrorBoundary` on every route; `ErrorBoundary` around `AppLayout` |
| Auth guard | ✅ | `RequireAuth` with session validation against `/tenants/me` |
| Lazy loading | ✅ | Heavy pages (WorkflowBuilder, CivilizationPage, etc.) use `React.lazy` + `Suspense` |
| Mobile responsive | ⚠️ | Sidebar collapse works (sidebarOpen state) but no bottom-nav pattern for mobile |
| Keyboard shortcuts | ⚠️ | `CommandPalette` component exists at `src/components/command-palette/CommandPalette.tsx` but no visible trigger/shortcut hint in the UI |
| Breadcrumbs | ❌ | No breadcrumb navigation on any page. Deep routes (agents/:id/radar, goals/:id/dna) have back-buttons only |

---

## State Matrix Summary

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

---

## Top UX Issues

Ranked by user impact × implementation cost (high impact / low cost first).

### 1. GoalsListPage: Missing Error State (CRITICAL)

**File:** `src/features/goals/GoalsListPage.tsx:95`  
**Impact:** Silent blank page on any network/auth failure. Users see nothing and don't know to retry.  
**Fix:** Add `isError` destructuring from `useQuery` result. Show an error panel with a "Retry" button that calls `qc.invalidateQueries`.

```tsx
const { data, isLoading, isError, refetch } = useQuery({ ... });
// Add after isLoading check:
if (isError) return <ErrorPanel message="Failed to load goals" onRetry={refetch} />;
```

---

### 2. Onboarding Not Auto-Triggered for New Users (HIGH)

**File:** `src/features/dashboard/DashboardPage.tsx` (caller)  
**Impact:** New tenants land on the dashboard with no agents or goals. No guidance on what to do first. Activation rate will be low.  
**Fix:** In `DashboardPage`, check `agents.length === 0` and `goals.length === 0` on mount; redirect to `/onboarding`. Set a `localStorage` flag after completion to prevent repeat.

---

### 3. Navigation Cognitive Overload — 50+ Sidebar Items (HIGH)

**File:** `src/components/ui/Sidebar.tsx`  
**Impact:** New users cannot locate features. 9 groups × 5–7 items each collapses when sidebar is narrow, but even expanded it's overwhelming.  
**Fix:** Default-collapse all groups except "Mission Control" (Dashboard/Goals/Agents). Promote the search input. Add a `?` keyboard shortcut hint to open `CommandPalette`.

---

### 4. Table-Only Layouts Break on Mobile (HIGH)

**Files:** `GoalsListPage.tsx:257`, `AgentsListPage.tsx:264`, `GovernancePage.tsx:707`  
**Impact:** The three most-used pages are broken on mobile viewports (< 640px). Tables horizontally scroll or columns collapse.  
**Fix:** Add a responsive card layout for `sm:hidden` viewports. A simple `flex flex-col` card with name, status badge, and action button is sufficient.

---

### 5. GovernancePage and KnowledgePage Tab Bars Lack ARIA Semantics (HIGH)

**Files:** `GovernancePage.tsx:61`, `KnowledgePage.tsx:36`  
**Impact:** Screen reader users cannot identify or navigate the tab bars. `Tab` and `GovTab` switchers are plain buttons without `role="tab"` or `aria-controls`.  
**Fix:** Pattern from `GoalDetailPage.tsx:709` — add `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls`, `tabIndex` managed focus, and keyboard navigation.

---

### 6. AgentCreatePage Not in Mission Control Theme (MEDIUM)

**File:** `src/features/agents/AgentCreatePage.tsx:53`  
**Impact:** Users navigate from the dark-themed AgentsListPage to a white/light AgentCreatePage. Jarring visual discontinuity.  
**Fix:** Wrap in `<MissionControlLayout>`. Replace `bg-card/border-input` tokens with `bg-panel-graphite/border-neural-violet/20`.

---

### 7. Memory Provenance Truncated Without Tooltip (MEDIUM)

**File:** `src/features/memory/MemoryExplorerPage.tsx:429`  
**Impact:** `r.source.slice(0, 12)…` in recall results makes source tracing impossible. Users cannot identify which document or goal produced a memory.  
**Fix:** Wrap the source in a `<abbr title={r.source}>` or a hover `Tooltip` component showing the full source URL.

---

### 8. SettingsPage: BillingPage Exists but Is Unreachable (MEDIUM)

**Files:** `src/features/settings/BillingPage.tsx`, `src/app/App.tsx`  
**Impact:** `BillingPage.tsx` (194 lines) exists and has billing plan/subscription UI but no route is registered in `App.tsx` and no settings tab links to it.  
**Fix:** Add `<Route path="settings/billing" element={<BillingPage />} />` to `App.tsx` and add a "Billing" tab to `SettingsPage.tsx`'s `SETTINGS_TABS` array.

---

### 9. Onboarding: No Back Navigation or Progress Persistence (MEDIUM)

**File:** `src/features/onboarding/OnboardingPage.tsx:394`  
**Impact:** Users who fill out Step 1 (LLM config) then make an error in Step 2 cannot go back. A page refresh resets to Step 1, losing all entered data.  
**Fix:** Add a "Back" button (disabled on step 1). Persist `currentStep` and form data to `sessionStorage`.

---

### 10. WorkflowBuilder Completely Unusable on Mobile (MEDIUM)

**File:** `src/features/workflow-builder/WorkflowBuilderPage.tsx:1001`  
**Impact:** The canvas-based editor renders a full-height `ReactFlow` canvas that is non-functional on touch/mobile. No feedback is given.  
**Fix:** Detect `window.innerWidth < 768` and render a `<MobileBlocker>` component explaining the feature requires desktop. Alternatively, expose a read-only preview mode on mobile.

---

## Additional Minor Issues

| Issue | File | Priority |
|-------|------|----------|
| Search input in AgentsListPage has no `aria-label` | `AgentsListPage.tsx:183` | LOW |
| AgentCreatePage autonomy modes have no help link for "requires eval suite" | `AgentCreatePage.tsx:162` | LOW |
| GoalDetailPage: `if (!goal)` doesn't distinguish 404 vs network error | `GoalDetailPage.tsx:515` | MEDIUM |
| KnowledgePage: Ingest tab labels (`for` inputs missing `id` pairs) | `KnowledgePage.tsx:575–580` | LOW |
| CommandPalette component exists but no visible keyboard shortcut hint | `Sidebar.tsx` | LOW |
| SettingsPage sidebar nav collapses below md breakpoint (no bottom-nav pattern) | `SettingsPage.tsx:950` | MEDIUM |
| ObservabilityPage trace rows are `div onClick` without keyboard focus | `ObservabilityPage.tsx:748` | LOW |

---

## Strengths Worth Preserving

1. **GoalDetailPage ARIA tablist** — the keyboard navigation model (lines 383–406) is production-grade. Other pages should copy this exact pattern.
2. **GovernancePage Emergency Stop** — the two-step confirmation UX is correct and the persistent banner is excellent.
3. **MemoryExplorerPage semantic recall** — confidence bars + type badges + source links = strong discoverability UX.
4. **WorkflowBuilderPage auto-save** — the `● Saved / ● Unsaved` indicator pattern is excellent for canvas editors.
5. **GoalsListPage URL-backed state** — filter/search/sort/page all in URL params ensures shareability and back-button correctness.
