# AgentVerse Full JARVIS UI/UX Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the entire AgentVerse frontend dashboard fully JARVIS-style consistent with world-class animations, fix missing features (OCR in sidebar), and ensure all backend capabilities are exposed in the frontend.

**Architecture:** 
- Phase 1: Fix design system inconsistencies (CivilizationPage, OcrPage hardcoded colors)
- Phase 2: Add missing navigation entries (OCR, any other missing pages)  
- Phase 3: Audit all 47+ feature pages for JARVISPageShell + motion compliance
- Phase 4: Map backend APIs to frontend pages - create missing pages for exposed backend features
- Phase 5: Standardize shared components (KPI cards, tables, charts) using design tokens
- Phase 6: Add page transitions, staggered animations, reduced-motion compliance everywhere

**Tech Stack:** React 19, TypeScript, Vite, TanStack Query, Zustand, Tailwind CSS, Framer Motion, Recharts, React Flow, React Router v7

---

## Phase 1: Design System Fixes (Critical)

### Task 1.1: Fix CivilizationPage - Replace Custom Styles with Design Tokens

**Files:**
- Modify: `agent-verse-frontend/src/features/civilization/CivilizationPage.tsx` (lines 68-70, 147-159, 445-448, 587-591)
- Test: `agent-verse-frontend/src/features/civilization/CivilizationPage.test.tsx`

- [ ] **Step 1: Write failing test for design token compliance**
```tsx
// Add to CivilizationPage.test.tsx
test('uses design tokens instead of hardcoded colors', () => {
  render(<CivilizationPage />);
  // Should NOT contain hardcoded linear-gradient backgrounds
  // Should use surface tokens from design system
});
```

- [ ] **Step 2: Run test to verify it fails**
```bash
cd agent-verse-frontend && npm run test -- src/features/civilization/CivilizationPage.test.tsx
```

- [ ] **Step 3: Replace hardcoded styles with design tokens**
```tsx
// In CivilizationPage.tsx, replace:
// style={{ background: 'linear-gradient(135deg, #0f172a 0%, #0d1625 100%)' }}
// With JARVISPageShell + design tokens:
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { tw } from '@/lib/design/tokens';

// Wrap entire page in JARVISPageShell
// Replace inline styles with tw.card, tw.surface1, tw.surface2, etc.
// Use colors.electric (#00D4FF) instead of #6366f1 indigo
```

- [ ] **Step 4: Run test to verify it passes**
```bash
cd agent-verse-frontend && npm run test -- src/features/civilization/CivilizationPage.test.tsx
```

- [ ] **Step 5: Commit**
```bash
git add agent-verse-frontend/src/features/civilization/CivilizationPage.tsx
git commit -m "fix: CivilizationPage uses JARVIS design tokens and page shell"
```

---

### Task 1.2: Fix OcrPage - Replace Hardcoded Slate Colors with Design Tokens

**Files:**
- Modify: `agent-verse-frontend/src/features/ocr/OcrPage.tsx` (lines 180-187, 301, 358-359, 570, 596-597, 608-610, 659-666, 735, 755-758, 777-783, 814, 826-835)
- Test: `agent-verse-frontend/src/features/ocr/OcrPage.test.tsx`

- [ ] **Step 1: Write failing test**
```tsx
test('uses design tokens instead of hardcoded slate colors', () => {
  render(<OcrPage />);
  // Should NOT contain: slate-800, slate-700, slate-600, slate-900
  // Should use: tw.surface3, tw.card, border-border, etc.
});
```

- [ ] **Step 2: Replace all hardcoded slate colors**
```tsx
// Replace throughout OcrPage.tsx:
'slate-800' → 'border-border' or 'bg-surface3'
'slate-700' → 'border-border'  
'slate-600' → 'text-muted-foreground'
'slate-900' → 'bg-surface1'
'slate-800/40' → 'bg-surface3/40'

// Use tokens from '@/lib/design/tokens'
import { colors, tw } from '@/lib/design/tokens';
```

- [ ] **Step 3: Run tests and verify**
```bash
cd agent-verse-frontend && npm run test -- src/features/ocr/OcrPage.test.tsx
```

- [ ] **Step 4: Commit**

---

### Task 1.3: Fix PerceptionPage - Replace Remaining Hardcoded Colors

**Files:**
- Modify: `agent-verse-frontend/src/features/perception/PerceptionPage.tsx` (check for `border-border`, `bg-muted` usage vs design tokens)
- Test: `agent-verse-frontend/src/features/perception/PerceptionPage.test.tsx`

- [ ] **Step 1: Audit and replace any non-token colors**
```tsx
// Check lines: 90, 145-146, 165-166, 173-174, 181-182, 319-320, 348-350, 360-361, 456, 462, 479-480, 485-486, 528, 537-538, 557, 566-567, 588-589, 599-600, 610-612, 618-620, 703-704, 713-714, 726-727, 733-734, 753-754
// Ensure all use: tw.card, border-border, text-muted-foreground, etc.
```

- [ ] **Step 2: Run tests**
```bash
cd agent-verse-frontend && npm run test -- src/features/perception/PerceptionPage.test.tsx
```

- [ ] **Step 3: Commit**

---

## Phase 2: Navigation & Missing Features (Critical)

### Task 2.1: Add OCR to Sidebar Navigation

**Files:**
- Modify: `agent-verse-frontend/src/components/ui/Sidebar.tsx` (add to NAV_SECTIONS)

- [ ] **Step 1: Add OCR to Tooling section**
```tsx
// In Sidebar.tsx NAV_SECTIONS, under "Tooling" section:
{ to: "/ocr", icon: ScanText, label: "OCR Extraction" },
```

- [ ] **Step 2: Add to CommandPalette for keyboard access**
```tsx
// In components/command-palette/CommandPalette.tsx, add:
{ label: 'OCR Extraction', icon: ScanText, action: () => navigate('/ocr') },
```

- [ ] **Step 3: Test navigation works**
```bash
cd agent-verse-frontend && npm run dev
# Navigate to /ocr via sidebar
```

- [ ] **Step 4: Commit**
```bash
git add agent-verse-frontend/src/components/ui/Sidebar.tsx agent-verse-frontend/src/components/command-palette/CommandPalette.tsx
git commit -m "feat: add OCR Extraction to sidebar and command palette"
```

---

### Task 2.2: Add Missing Pages from Backend APIs to Frontend

**Analysis of Backend APIs vs Frontend Pages:**

| Backend API | Frontend Page | Status |
|-------------|---------------|--------|
| `/civilizations` | CivilizationPage ✅ | Complete |
| `/ocr/*` | OcrPage ✅ | Missing from sidebar (fixed above) |
| `/perception/*` | PerceptionPage ✅ | Complete |
| `/analytics/*` | AnalyticsDashboardPage ✅ | Complete |
| `/intelligence/experiments` | SelfImprovementPage ✅ | Complete |
| `/costs/*` | CostDashboardPage ✅ | Complete |
| `/guardrails/*` | GuardrailCenterPage ✅ | Complete |
| `/templates/*` | TemplateLibraryPage ✅ | Complete |
| `/workflows` (v1) | WorkflowBuilderPage, WorkflowListPage ✅ | Complete |
| `/api/v1/workflows` | WorkflowEngine pages ❌ | **MISSING** |
| `/intelligence/eval-suites` | EvalSuite pages ❌ | **MISSING** |
| `/intelligence/prompt-variants` | PromptVariant pages ❌ | **MISSING** |
| `/enterprise/simulation` | SimulationPage ✅ | Partial |
| `/enterprise/red-team` | RedTeam pages ❌ | **MISSING** |
| `/agents/{id}/credentials` | AgentCredentials pages ❌ | **MISSING** |
| `/marketplace/templates` | MarketplacePage ✅ | V2 API support needed |
| `/a2a/*` | A2APage ✅ | Read-only |
| `/rpa/*` | RpaLivePage ✅ | Complete |
| `/collab/crdt-token` | CollaborationPage ✅ | Complete |
| `/governance/audit` | AuditExplorerPage ✅ | Complete |
| `/governance/notifications` | NotificationCenterPage ✅ | Complete |
| `/tenants/me/roles` | RbacPage ✅ | Complete |
| `/compliance/*` | CompliancePage ✅ | Complete |
| `/admin/*` | AdminPage ✅ | Complete |

**Missing Pages to Create:**

#### Task 2.2.1: Create Workflow Engine Pages (for `/api/v1/workflows`)

**Files:**
- Create: `agent-verse-frontend/src/features/workflow-engine/WorkflowEngineListPage.tsx`
- Create: `agent-verse-frontend/src/features/workflow-engine/WorkflowEngineDetailPage.tsx`
- Create: `agent-verse-frontend/src/features/workflow-engine/WorkflowEngineRunsPage.tsx`
- Create: `agent-verse-frontend/src/features/workflow-engine/WorkflowEngineRunDetailPage.tsx`
- Create: `agent-verse-frontend/src/features/workflow-engine/WorkflowEngineTemplatesPage.tsx`
- Create: `agent-verse-frontend/src/features/workflow-engine/WorkflowEngineAnalyticsPage.tsx`
- Create: `agent-verse-frontend/src/features/workflow-engine/WorkflowEngineApprovalsPage.tsx`
- Modify: `agent-verse-frontend/src/app/App.tsx` (add routes)
- Modify: `agent-verse-frontend/src/components/ui/Sidebar.tsx` (add navigation)

- [ ] **Step 1: Create WorkflowEngineListPage with JARVISPageShell**
```tsx
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { workflowEngineApi } from '@/lib/api/client';
// ... full implementation with:
// - Data table with workflows
// - Create/edit/delete/publish actions
// - Version history
// - NL trigger preview
```

- [ ] **Step 2: Create WorkflowEngineDetailPage**
```tsx
// Workflow definition viewer with YAML export, validation, clone
```

- [ ] **Step 3: Create WorkflowEngineRunsPage + RunDetailPage**
```tsx
// Run listing with pagination, filtering
// Run detail with step-by-step execution view, debug, pause/resume/cancel
```

- [ ] **Step 4: Create WorkflowEngineTemplatesPage**
```tsx
// Template gallery with categories, fork functionality
```

- [ ] **Step 5: Create WorkflowEngineAnalyticsPage**
```tsx
// Summary + per-workflow analytics charts
```

- [ ] **Step 6: Create WorkflowEngineApprovalsPage**
```tsx
// HITL approval inbox with batch actions, delegation
```

- [ ] **Step 7: Add routes to App.tsx**
```tsx
const WorkflowEngineListPage = lazy(() => import('@/features/workflow-engine/WorkflowEngineListPage'));
const WorkflowEngineDetailPage = lazy(() => import('@/features/workflow-engine/WorkflowEngineDetailPage'));
// ... add routes under authenticated layout
```

- [ ] **Step 8: Add to Sidebar under "Enterprise" or new "Workflows" section**

---

#### Task 2.2.2: Create Eval Suites Pages

**Files:**
- Create: `agent-verse-frontend/src/features/eval-suites/EvalSuitesListPage.tsx`
- Create: `agent-verse-frontend/src/features/eval-suites/EvalSuiteDetailPage.tsx`
- Create: `agent-verse-frontend/src/features/eval-suites/EvalSuiteRunPage.tsx`
- Create: `agent-verse-frontend/src/features/eval-suites/EvalSuiteResultsPage.tsx`
- Modify: `agent-verse-frontend/src/app/App.tsx`
- Modify: `agent-verse-frontend/src/components/ui/Sidebar.tsx`

- [ ] **Step 1: Create EvalSuitesListPage**
```tsx
// List with create suite modal, task count, run button
```

- [ ] **Step 2: Create EvalSuiteDetailPage**
```tsx
// Suite config, task management (add/edit/delete tasks), run history
```

- [ ] **Step 3: Create EvalSuiteRunPage + ResultsPage**
```tsx
// Run execution with progress, results visualization
```

- [ ] **Step 4: Add routes + sidebar navigation**

---

#### Task 2.2.3: Create Prompt Variants Pages

**Files:**
- Create: `agent-verse-frontend/src/features/prompt-variants/PromptVariantsPage.tsx`
- Modify: `agent-verse-frontend/src/app/App.tsx`
- Modify: `agent-verse-frontend/src/components/ui/Sidebar.tsx`

- [ ] **Step 1: Create PromptVariantsPage**
```tsx
// Tabbed by prompt key
// List variants with A/B test stats, promote/delete actions
// Create variant modal
// Report viewer
```

- [ ] **Step 2: Add routes + sidebar**

---

#### Task 2.2.4: Create Red Team Pages

**Files:**
- Create: `agent-verse-frontend/src/features/red-team/RedTeamPage.tsx`
- Modify: `agent-verse-frontend/src/app/App.tsx`
- Modify: `agent-verse-frontend/src/components/ui/Sidebar.tsx`

- [ ] **Step 1: Create RedTeamPage**
```tsx
// Test case selection, run button, results report with pass/fail details
```

---

#### Task 2.2.5: Create Agent Credentials Pages

**Files:**
- Create: `agent-verse-frontend/src/features/agents/AgentCredentialsPage.tsx`
- Modify: `agent-verse-frontend/src/app/App.tsx` (add route `/agents/:agentId/credentials`)
- Modify: `agent-verse-frontend/src/features/agents/AgentDetailPage.tsx` (add link/tab)

- [ ] **Step 1: Create AgentCredentialsPage**
```tsx
// List credentials with type, scopes, expiry, status
// Issue new credential modal (JWT, API key, mTLS)
// Revoke action, copy token
```

---

## Phase 3: Full Page Audit - JARVISPageShell Compliance

### Task 3.1: Audit All Feature Pages for JARVIS Compliance

**Files to Check (47+ pages):**
```
agent-verse-frontend/src/features/**/*Page.tsx
```

**Pages needing verification:**
- [x] DashboardPage ✅
- [x] AgentDashboardPage ✅  
- [x] AnalyticsDashboardPage ✅
- [x] SelfImprovementPage ✅
- [x] CivilizationPage ⚠️ (fixed in Phase 1)
- [x] PerceptionPage ✅
- [x] OcrPage ✅ (fixed in Phase 1)
- [x] AIOpsDashboard ✅
- [ ] BuilderPage
- [ ] SkillsPage
- [ ] ModelControlCenter
- [ ] GraphExplorerPage
- [ ] AgentIdentityPage
- [ ] AgentRadarPage
- [ ] AgentPersonalityPage
- [ ] AgentDetailPage
- [ ] AgentCreatePage
- [ ] AgentsListPage
- [ ] GoalsListPage
- [ ] GoalDetailPage
- [ ] GoalDNAPage
- [ ] GoalDiffPage
- [ ] GhostRunPage
- [ ] TemplateLibraryPage
- [ ] WorkflowBuilderPage
- [ ] WorkflowListPage
- [ ] WorkflowRunsPage
- [ ] WorkflowRunDetailPage
- [ ] WorkflowAnalyticsPage
- [ ] WorkflowMarketplacePage
- [ ] WorkflowSettingsPage
- [ ] ApprovalInboxPage
- [ ] ConnectorsCatalogPage
- [ ] ConnectorsRegisteredPage
- [ ] ConnectorDetailPage
- [ ] SchedulesPage
- [ ] KnowledgePage
- [ ] SourcesPage
- [ ] GovernancePage
- [ ] ApprovalsPage
- [ ] AuditExplorerPage
- [ ] CompliancePage
- [ ] RbacPage
- [ ] SettingsPage
- [ ] ScopeExplorerPage
- [ ] GuardrailCenterPage
- [ ] BudgetManagerPage
- [ ] BillingPage
- [ ] PlaygroundPage
- [ ] SimulationPage
- [ ] EvalPage
- [ ] MarketplacePage
- [ ] EnterprisePage
- [ ] AdminPage
- [ ] SecurityCenterPage
- [ ] OnboardingPage
- [ ] ChatPage
- [ ] CollaborationPage
- [ ] CoordinationRunPage
- [ ] ObservabilityPage
- [ ] CostDashboardPage
- [ ] RpaLivePage
- [ ] MemoryExplorerPage
- [ ] ArtifactsBrowserPage
- [ ] ToolsPage
- [ ] IntegrationsPage
- [ ] TrainingExportPage
- [ ] A2APage
- [ ] NotificationCenterPage
- [ ] DomainsPage
- [ ] DomainDetailPage
- [ ] GraphifyPage
- [ ] ObsidianPage
- [ ] OrgPage
- [ ] OrgListPage
- [ ] MissionPage
- [ ] DepartmentPage
- [ ] TeamPage
- [ ] StrategicAdvisorPage
- [ ] AgentLabPage
- [ ] ChannelMappingsPage
- [ ] StateMachinesPage
- [ ] TriggersPage
- [ ] GatewaySettingsPage
- [ ] StatusPage

- [ ] **Step 1: Create audit script**
```bash
# Create audit script to check each page
cat > audit-jarvis-compliance.sh << 'EOF'
#!/bin/bash
for file in agent-verse-frontend/src/features/**/*Page.tsx; do
  if ! grep -q "JARVISPageShell" "$file"; then
    echo "MISSING JARVISPageShell: $file"
  fi
  if ! grep -q "JARVISStagger" "$file"; then
    echo "MISSING JARVISStagger: $file"
  fi
  if grep -q "slate-\|gray-\|gray-" "$file"; then
    echo "HARDCODED COLORS: $file"
  fi
done
EOF
chmod +x audit-jarvis-compliance.sh
./audit-jarvis-compliance.sh
```

- [ ] **Step 2: Fix each non-compliant page** (batch by batch)

---

## Phase 4: Shared Component Standardization

### Task 4.1: Extract Shared KPI Card Component

**Files:**
- Create: `agent-verse-frontend/src/components/ui/KpiCard.tsx`
- Modify: `agent-verse-frontend/src/features/dashboard/DashboardPage.tsx` (use shared)
- Modify: `agent-verse-frontend/src/features/analytics/AnalyticsDashboardPage.tsx` (use shared)
- Modify: `agent-verse-frontend/src/features/agents/AgentDashboardPage.tsx` (use shared)
- Modify: `agent-verse-frontend/src/features/civilization/CivilizationMetrics.tsx` (use shared)

- [ ] **Step 1: Create unified KpiCard with all variants**
```tsx
// src/components/ui/KpiCard.tsx
interface KpiCardProps {
  label: string;
  value: string | number;
  sub?: string;
  trend?: 'up' | 'down' | 'neutral';
  trendLabel?: string;
  icon: React.ReactNode;
  accent: 'electric' | 'emerald' | 'amber' | 'rose' | 'violet';
  isLoading?: boolean;
  onClick?: () => void;
  // ... motion variants
}
```

- [ ] **Step 2: Replace all inline KPI implementations**
- [ ] **Step 3: Run tests**
- [ ] **Step 4: Commit**

---

### Task 4.2: Standardize Data Table Component

**Files:**
- Create: `agent-verse-frontend/src/components/ui/DataTable.tsx`
- Update pages using inline tables (Analytics, Audit, Civilization members, etc.)

---

### Task 4.3: Standardize Chart Components

**Files:**
- Verify: `agent-verse-frontend/src/components/charts/ThemedBarChart.tsx`
- Verify: `agent-verse-frontend/src/components/charts/ThemedLineChart.tsx`
- Verify: `agent-verse-frontend/src/components/charts/ThemedRadarChart.tsx`
- Ensure all use `CHART_COLORS` from design tokens

---

## Phase 5: Animation & Motion Polish

### Task 5.1: Add Page Transitions (AnimatePresence)

**Files:**
- Modify: `agent-verse-frontend/src/components/ui/AppLayout.tsx` (wrap Outlet with AnimatePresence)
- Modify: `agent-verse-frontend/src/components/ui/JARVISPageShell.tsx` (add exit animations)

- [ ] **Step 1: Add AnimatePresence to AppLayout**
```tsx
import { AnimatePresence } from 'framer-motion';

// In AppLayout.tsx, wrap Outlet:
<AnimatePresence mode="wait">
  <Outlet />
</AnimatePresence>
```

- [ ] **Step 2: Add exit variants to JARVISPageShell**
```tsx
// Already has exit variant, ensure it's used
exit={reduce ? { opacity: 0 } : { opacity: 0, y: -8, filter: 'blur(2px)' }}
```

---

### Task 5.2: Add Staggered List Animations to All Pages

**Files:**
- All pages with lists/grids: Dashboard, Analytics, SelfImprovement, Civilization, Perception, OCR, etc.

- [ ] **Step 1: Audit and add JARVISStagger to all list containers**
```tsx
// Pattern:
<JARVISStagger className="space-y-4">
  {items.map(item => (
    <JARVISStaggerItem key={item.id} interactive>
      <ItemComponent item={item} />
    </JARVISStaggerItem>
  ))}
</JARVISStagger>
```

---

### Task 5.3: Verify Reduced Motion Compliance

**Files:**
- All components using framer-motion

- [ ] **Step 1: Verify all motion components use `useReducedMotion()`**
- [ ] **Step 2: Test with OS reduced motion enabled**

---

## Phase 6: Backend-Frontend Feature Parity Audit

### Task 6.1: Complete API Coverage Matrix

**Backend APIs → Frontend Coverage:**

| API Category | Endpoints | Frontend Coverage | Missing |
|--------------|-----------|-------------------|---------|
| Goals | 12 | GoalsList, GoalDetail, GoalDNA, GhostRun | Batch pause/resume UI |
| Agents | 15 | List, Create, Detail, Dashboard, Radar, Personality, Identity | Credentials, Permissions, Readiness |
| Connectors | 14 | Catalog, Registered, Detail | OAuth flow UI complete? |
| Workflows (legacy) | 12 | List, Builder, Runs, Analytics, Marketplace | Settings, Approvals |
| Workflows (v1) | 25 | **NONE** | **ALL - Phase 2** |
| Civilization | 25 | Complete theater | - |
| OCR | 2 | OcrPage ✅ | Sidebar nav |
| Perception | 7 | PerceptionPage ✅ | - |
| Analytics | 5 | AnalyticsDashboard ✅ | - |
| Self-Improvement | 6 | SelfImprovementPage ✅ | - |
| Costs | 9 | CostDashboard ✅ | - |
| Guardrails | 7 | GuardrailCenter ✅ | - |
| Templates | 7 | TemplateLibrary ✅ | - |
| Knowledge | 6 | Knowledge, Sources | - |
| Schedules | 4 | SchedulesPage ✅ | - |
| Governance | 15 | Governance, Approvals, Audit | - |
| RBAC | 6 | RbacPage ✅ | - |
| Compliance | 11 | CompliancePage ✅ | - |
| Admin | 3 | AdminPage ✅ | - |
| Observability | 4 | Observability, CostDashboard | Logs page? |
| Eval Suites | 7 | **NONE** | **ALL - Phase 2** |
| Prompt Variants | 6 | **NONE** | **ALL - Phase 2** |
| Red Team | 1 | **NONE** | **ALL - Phase 2** |
| Agent Credentials | 4 | **NONE** | **ALL - Phase 2** |
| Marketplace V2 | 6 | MarketplacePage | Deploy, reviews |
| Simulation | 5 | SimulationPage | Streaming UI |
| RPA | 6 | RpaLivePage ✅ | - |
| A2A | 4 | A2APage (read-only) | - |
| Notifications | 4 | NotificationCenter ✅ | - |
| Memory | 7 | MemoryExplorer ✅ | - |
| Artifacts | 4 | ArtifactsBrowser ✅ | - |
| Tools | 6 | ToolsPage ✅ | - |
| Training Export | 2 | TrainingExportPage ✅ | - |
| Integrations | 1 | IntegrationsPage ✅ | - |

---

## Phase 7: Quality Gates & Verification

### Task 7.1: Run Full Test Suite

- [ ] `cd agent-verse-frontend && npm run test`
- [ ] `cd agent-verse-frontend && npm run typecheck`
- [ ] `cd agent-verse-frontend && npm run lint`

### Task 7.2: Visual Regression Testing

- [ ] Test all pages in light/dark mode
- [ ] Test with reduced motion
- [ ] Test responsive breakpoints (mobile, tablet, desktop)
- [ ] Test keyboard navigation

### Task 7.3: Performance Audit

- [ ] Bundle size analysis
- [ ] Animation performance (60fps)
- [ ] Lazy loading verification

---

## Execution Order

### Recommended Sequence:

1. **Week 1:** Phase 1 (Design fixes) + Phase 2 (OCR sidebar + missing nav)
2. **Week 2:** Phase 2 continued (Workflow Engine, Eval Suites, Prompt Variants, Red Team, Agent Credentials pages)
3. **Week 3:** Phase 3 (Full page audit) + Phase 4 (Shared components)
4. **Week 4:** Phase 5 (Motion polish) + Phase 6 (Parity audit) + Phase 7 (Quality gates)

---

## Plan Complete

**Plan saved to:** `docs/superpowers/plans/2026-08-19-agentverse-jervis-ui-consistency-plan.md`

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
   - REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints
   - REQUIRED SUB-SKILL: Use superpowers:executing-plans

**Which approach?**