# World-Class Agent OS UI — Complete Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Transform AgentVerse into the world's most compelling agent OS platform UI with zero regressions, full dark-mode fidelity, mobile responsiveness, and 15 "wow" features that don't exist on any competing platform.

**Architecture:** Six parallel execution waves. Each wave's agents own non-overlapping file sets. Backend APIs use FastAPI + SQLAlchemy + existing infra. Frontend uses React 19, Recharts (themed), @xyflow/react (already installed), d3-force, Web Speech API. All open source, all locally runnable.

**Tech Stack:** React 19 · TypeScript 5 · Recharts 2 · @xyflow/react · d3-force · diff · Zustand 5 · TanStack Query 5 · Tailwind 3 · FastAPI · SQLAlchemy · pgvector

---

## Wave 1 — Foundation & Critical Bug Fixes (4 parallel agents, no file overlap)

### Agent 1A owns: ui.ts, Toaster.tsx, new ConfirmModal, new Pagination, MarketplacePage, GoalDetailPage (debug dump only), ObservabilityPage, OnboardingPage, useCollabSocket.ts

### Agent 1B owns: Sidebar.tsx, AppLayout.tsx, TopBar.tsx

### Agent 1C owns: StatusBadge.tsx, new ThemedChart components, hardcoded-color fixes in 10 feature pages

### Agent 1D owns: Backend new API files (insights.py, templates.py, 0048 migration, tests)

---

## Wave 2 — Wow Features Frontend (3 parallel agents)

### Agent 2A owns: DashboardPage.tsx complete redesign (Mission Control)

### Agent 2B owns: Goal DNA page, Execution Diff page, Ghost Run page (all new files)

### Agent 2C owns: LiveCostTicker, AgentRadar, VoiceGoalInput, TemplateLibrary, AgentPersonality, App.tsx routes, client.ts additions

---

## Wave 3 — E2E Tests + Integration

### Agent 3A: All e2e tests for new features
### Agent 3B: Backend unit tests for new APIs, frontend unit tests for new components

---

## New npm dependencies required
```bash
npm install d3-force d3-selection diff @types/d3-force @types/d3-selection @types/diff --save
```

## New backend dependencies (all open source, already in venv)
- No new Python packages needed — uses existing LangGraph, pgvector, SQLAlchemy, httpx

---

## Complete File Map

### New Frontend Files
- `src/stores/ui.ts` — add localStorage persistence for theme + sidebar
- `src/components/ui/Toaster.tsx` — auto-dismiss, theming
- `src/components/ui/ConfirmModal.tsx` — reusable confirm dialog
- `src/components/ui/Pagination.tsx` — full pagination with page size
- `src/components/ui/Sidebar.tsx` — CSS vars, mobile, tooltips, primary rail
- `src/components/ui/AppLayout.tsx` — mobile hamburger + overlay
- `src/components/ui/TopBar.tsx` — fix theme toggle, breadcrumbs
- `src/components/ui/StatusBadge.tsx` — all statuses, dark mode
- `src/components/charts/ThemedLineChart.tsx` — Recharts + CSS vars
- `src/components/charts/ThemedBarChart.tsx` — Recharts + CSS vars
- `src/components/charts/ThemedAreaChart.tsx` — Recharts + CSS vars
- `src/components/charts/ThemedRadarChart.tsx` — Recharts RadarChart + CSS vars
- `src/components/charts/index.ts` — barrel export
- `src/components/live/LiveCostTicker.tsx` — real-time $ counter
- `src/components/voice/VoiceGoalInput.tsx` — Web Speech API
- `src/features/dashboard/DashboardPage.tsx` — Mission Control redesign
- `src/features/dashboard/components/MissionControlOrbit.tsx` — live agent orbit
- `src/features/dashboard/components/LiveActivityStream.tsx` — streaming feed
- `src/features/goals/GoalDNAPage.tsx` — force-graph execution visualization
- `src/features/goals/GoalDiffPage.tsx` — side-by-side execution diff
- `src/features/goals/GhostRunPage.tsx` — A/B goal execution comparison
- `src/features/goals/components/CostEstimateWidget.tsx` — pre-run estimator
- `src/features/goals/GoalsListPage.tsx` — add pagination + skeleton
- `src/features/agents/AgentRadarPage.tsx` — 6-axis health radar
- `src/features/agents/AgentPersonalityPage.tsx` — personality sliders
- `src/features/templates/TemplateLibraryPage.tsx` — parameterized templates
- `src/features/templates/components/TemplateCard.tsx`
- `src/features/templates/components/TemplateInstantiator.tsx`
- `src/app/App.tsx` — new routes

### Modified Frontend Files (color fixes + dark mode)
- `src/features/agents/AgentDetailPage.tsx`
- `src/features/agents/AgentCreatePage.tsx`
- `src/features/agents/AgentsListPage.tsx`
- `src/features/settings/SettingsPage.tsx`
- `src/features/knowledge/KnowledgePage.tsx`
- `src/features/governance/GovernancePage.tsx`
- `src/features/approvals/ApprovalsPage.tsx`
- `src/features/observability/CostDashboardPage.tsx`
- `src/features/analytics/AnalyticsDashboardPage.tsx`
- `src/features/collaboration/CollaborationPage.tsx`
- `src/features/marketplace/MarketplacePage.tsx`
- `src/features/goals/GoalDetailPage.tsx`
- `src/features/observability/ObservabilityPage.tsx`
- `src/features/onboarding/OnboardingPage.tsx`

### New Backend Files
- `app/api/insights.py` — cost estimator, execution graph, NL query, failure analysis
- `app/api/templates.py` — goal template CRUD + instantiate
- `app/db/models/template.py` — GoalTemplate SQLAlchemy model
- `app/db/migrations/versions/0048_goal_templates.py`
- `tests/api/test_insights.py`
- `tests/api/test_templates.py`

### Modified Backend Files
- `app/main.py` — wire new routers
- `app/db/models/__init__.py` — export GoalTemplate
- `agent-verse-frontend/src/lib/api/client.ts` — insightsApi, templatesApi
