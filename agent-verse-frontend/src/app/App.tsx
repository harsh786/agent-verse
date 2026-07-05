import { Navigate, Route, Routes } from "react-router-dom";
import { useEffect, useState, lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { AppLayout } from "@/components/ui/AppLayout";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { RouteErrorBoundary } from "@/components/ui/RouteErrorBoundary";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import NotFoundPage from '@/features/errors/NotFoundPage';
import OAuthCallbackPage from '@/features/connectors/OAuthCallbackPage';

// ── Lazy-loaded existing pages ───────────────────────────────────────────────
const DomainsPage = lazy(() => import('@/features/domains/DomainsPage'));
const DomainDetailPage = lazy(() => import('@/features/domains/DomainDetailPage'));
const CivilizationPage = lazy(() => import('../features/civilization/CivilizationPage'));
const GoalDNAPage = lazy(() => import("@/features/goals/GoalDNAPage").then(m => ({ default: m.GoalDNAPage })));
const AgentRadarPage = lazy(() => import("@/features/agents/AgentRadarPage").then(m => ({ default: m.AgentRadarPage })));
const TemplateLibraryPage = lazy(() => import("@/features/templates/TemplateLibraryPage").then(m => ({ default: m.TemplateLibraryPage })));
const WorkflowBuilderPage = lazy(() => import("@/features/workflow-builder/WorkflowBuilderPage").then(m => ({ default: m.WorkflowBuilderPage })));
const GoalDiffPage = lazy(() => import("@/features/goals/GoalDiffPage").then(m => ({ default: m.GoalDiffPage })));
const GhostRunPage = lazy(() => import("@/features/goals/GhostRunPage").then(m => ({ default: m.GhostRunPage })));
const AgentPersonalityPage = lazy(() => import("@/features/agents/AgentPersonalityPage").then(m => ({ default: m.AgentPersonalityPage })));

// ── New pages (Spec implementations) ────────────────────────────────────────
const AgentIdentityPage = lazy(() => import("@/features/agents/AgentIdentityPage").then(m => ({ default: m.AgentIdentityPage })));
const ScopeExplorerPage = lazy(() => import("@/features/settings/ScopeExplorerPage").then(m => ({ default: m.ScopeExplorerPage })));
const GuardrailCenterPage = lazy(() => import("@/features/settings/GuardrailCenterPage").then(m => ({ default: m.GuardrailCenterPage })));
const BudgetManagerPage = lazy(() => import("@/features/settings/BudgetManagerPage").then(m => ({ default: m.BudgetManagerPage })));
const SelfImprovementPage = lazy(() => import("@/features/analytics/SelfImprovementPage").then(m => ({ default: m.SelfImprovementPage })));
const AgentLabPage = lazy(() => import("@/features/lab/AgentLabPage").then(m => ({ default: m.AgentLabPage })));
const BuilderPage = lazy(() => import("@/features/builder/BuilderPage"));
const SkillsPage = lazy(() => import('@/features/skills/SkillsPage'));

import { LandingPage } from "@/features/landing/LandingPage";
import { AuthPage } from "@/features/auth/AuthPage";
import { SSOCallbackPage } from "@/features/auth/SSOCallbackPage";
import MFAVerifyPage from "@/features/auth/MFAVerifyPage";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { GoalsListPage } from "@/features/goals/GoalsListPage";
import { GoalDetailPage } from "@/features/goals/GoalDetailPage";
import { AgentsListPage } from "@/features/agents/AgentsListPage";
import { AgentCreatePage } from "@/features/agents/AgentCreatePage";
import { AgentDetailPage } from "@/features/agents/AgentDetailPage";
import { ApprovalsPage } from "@/features/approvals/ApprovalsPage";
import { OnboardingPage } from "@/features/onboarding/OnboardingPage";
import { ConnectorsCatalogPage } from "@/features/connectors/ConnectorsCatalogPage";
import { ConnectorsRegisteredPage } from "@/features/connectors/ConnectorsRegisteredPage";
import { SchedulesPage } from "@/features/schedules/SchedulesPage";
import { KnowledgePage } from "@/features/knowledge/KnowledgePage";
import { GovernancePage } from "@/features/governance/GovernancePage";
import { CollaborationPage } from "@/features/collaboration/CollaborationPage";
import { ObservabilityPage } from "@/features/observability/ObservabilityPage";
import { CostDashboardPage } from "@/features/observability/CostDashboardPage";
import { EvalPage } from "@/features/eval/EvalPage";
import { MarketplacePage } from "@/features/marketplace/MarketplacePage";
import { EnterprisePage } from "@/features/enterprise/EnterprisePage";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { PlaygroundPage } from "@/features/playground/PlaygroundPage";
import { AnalyticsDashboardPage } from "@/features/analytics/AnalyticsDashboardPage";
import SimulationPage from "@/features/simulation/SimulationPage";
import AuditExplorerPage from "@/features/audit/AuditExplorerPage";
import RpaLivePage from "@/features/rpa/RpaLivePage";
import { MemoryExplorerPage } from "@/features/memory/MemoryExplorerPage";
import { ArtifactsBrowserPage } from "@/features/artifacts/ArtifactsBrowserPage";
import { ToolsPage } from "@/features/tools/ToolsPage";
import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import { TrainingExportPage } from "@/features/training/TrainingExportPage";
import { PerceptionPage } from "@/features/perception/PerceptionPage";
import { A2APage } from "@/features/a2a/A2APage";
import { NotificationCenterPage } from "@/features/notifications/NotificationCenterPage";
import { RbacPage } from "@/features/rbac/RbacPage";
import { CompliancePage } from "@/features/compliance/CompliancePage";
import { ConnectorDetailPage } from "@/features/connectors/ConnectorDetailPage";
import { AgentDashboardPage } from "@/features/agents/AgentDashboardPage";
import { StatusPage } from "@/features/status/StatusPage";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

/** Wrap an element in a per-route error boundary. Keeps route definitions concise. */
function rb(name: string, element: React.ReactNode): React.ReactNode {
  return <RouteErrorBoundary routeName={name}>{element}</RouteErrorBoundary>;
}

/** Lazy element wrapped in Suspense + per-route error boundary. */
function lazy_rb(name: string, element: React.ReactNode): React.ReactNode {
  return (
    <RouteErrorBoundary routeName={name}>
      <Suspense fallback={<LoadingSpinner />}>{element}</Suspense>
    </RouteErrorBoundary>
  );
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const apiKey = useAuthStore((s) => s.apiKey);
  const tenantId = useAuthStore((s) => s.tenantId);
  const logout = useAuthStore((s) => s.logout);
  const sessionValidated = useAuthStore((s) => s.sessionValidated);
  const setSessionValidated = useAuthStore((s) => s.setSessionValidated);
  const [isChecking, setIsChecking] = useState(false);

  useEffect(() => {
    // Only validate once per app load — skip if already validated this session
    if (!isAuthenticated || !apiKey || sessionValidated) return;

    let cancelled = false;
    setIsChecking(true);

    async function validateSession() {
      try {
        const res = await fetch(`${API_BASE}/tenants/me`, {
          headers: { 'X-API-Key': apiKey },
        });
        if (!res.ok) {
          logout();
          return;
        }
        const tenant = await res.json();
        if (tenant.tenant_id !== tenantId) {
          logout();
          return;
        }
      } catch {
        // Keep the session during transient backend/network failures
      } finally {
        if (!cancelled) {
          setSessionValidated(true);
          setIsChecking(false);
        }
      }
    }

    void validateSession();

    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps — intentionally runs once on mount

  if (!isAuthenticated) return <Navigate to="/auth" replace />;
  if (isChecking) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-sm text-muted-foreground">Loading…</p>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      {/* ── Public routes (no auth required) ──────────────────────────── */}
      <Route path="/" element={<LandingPage />} />
      <Route path="/auth" element={<AuthPage />} />
      <Route path="/login" element={<AuthPage />} />
      <Route path="/auth/callback" element={<SSOCallbackPage />} />
      <Route path="/auth/mfa" element={<MFAVerifyPage />} />
      <Route path="/status" element={<StatusPage />} />
      {/* OAuth popup callback — must be public so the provider redirect works without auth */}
      <Route path="/connectors/oauth/callback" element={<OAuthCallbackPage />} />

      {/* ── Authenticated app routes — pathless layout route ────────────
          A pathless <Route> has no path prop; it acts as a layout wrapper.
          Children still match their own full paths from the root.
          The outer ErrorBoundary catches layout-level failures (AppLayout crash).
          RouteErrorBoundary on each child catches individual page crashes so
          the sidebar/nav remain functional.                                 */}
      <Route
        element={
          <RequireAuth>
            <ErrorBoundary>
              <AppLayout />
            </ErrorBoundary>
          </RequireAuth>
        }
      >
        <Route path="dashboard"             element={rb("Dashboard",          <DashboardPage />)} />
        <Route path="goals"                 element={rb("Goals",              <GoalsListPage />)} />
        <Route path="goals/:goalId"         element={rb("Goal Detail",        <GoalDetailPage />)} />
        <Route path="agents"                element={rb("Agents",             <AgentsListPage />)} />
        <Route path="agents/create"         element={rb("Create Agent",       <AgentCreatePage />)} />
        <Route path="agents/:agentId"       element={rb("Agent Detail",       <AgentDetailPage />)} />
        <Route path="agents/:agentId/identity"    element={lazy_rb("Agent Identity",    <AgentIdentityPage />)} />
        <Route path="agents/:agentId/dashboard"   element={rb("Agent Dashboard",        <AgentDashboardPage />)} />
        <Route path="agents/:agentId/radar"       element={lazy_rb("Agent Radar",       <AgentRadarPage />)} />
        <Route path="agents/:agentId/personality" element={lazy_rb("Agent Personality", <AgentPersonalityPage />)} />
        <Route path="approvals"             element={rb("Approvals",          <ApprovalsPage />)} />
        <Route path="onboarding"            element={rb("Onboarding",         <OnboardingPage />)} />
        <Route path="connectors/catalog"    element={rb("Connectors Catalog", <ConnectorsCatalogPage />)} />
        <Route path="connectors"            element={rb("Connectors",         <ConnectorsRegisteredPage />)} />
        <Route path="connectors/:connectorId" element={rb("Connector Detail", <ConnectorDetailPage />)} />
        <Route path="schedules"             element={rb("Schedules",          <SchedulesPage />)} />
        <Route path="knowledge"             element={rb("Knowledge",          <KnowledgePage />)} />
        <Route path="governance"            element={rb("Governance",         <GovernancePage />)} />
        <Route path="collaboration"         element={rb("Collaboration",      <CollaborationPage />)} />
        <Route path="observability"         element={rb("Observability",      <ObservabilityPage />)} />
        <Route path="observability/cost"    element={rb("Cost Dashboard",     <CostDashboardPage />)} />
        <Route path="eval"                  element={rb("Evaluations",        <EvalPage />)} />
        <Route path="marketplace"           element={rb("Marketplace",        <MarketplacePage />)} />
        <Route path="domains"               element={lazy_rb("Domains",       <DomainsPage />)} />
        <Route path="domains/:domain"       element={lazy_rb("Domain Detail", <DomainDetailPage />)} />
        <Route path="enterprise"            element={rb("Enterprise",         <EnterprisePage />)} />
        <Route path="settings"              element={rb("Settings",           <SettingsPage />)} />
        <Route path="settings/scopes"       element={lazy_rb("Scope Explorer",   <ScopeExplorerPage />)} />
        <Route path="settings/guardrails"   element={lazy_rb("Guardrail Center", <GuardrailCenterPage />)} />
        <Route path="settings/budgets"      element={lazy_rb("Budget Manager",   <BudgetManagerPage />)} />
        <Route path="self-improvement"      element={lazy_rb("Self Improvement", <SelfImprovementPage />)} />
        <Route path="lab"                   element={lazy_rb("Agent Lab",        <AgentLabPage />)} />
        <Route path="skills"                element={lazy_rb("Skills",           <SkillsPage />)} />
        <Route path="workflow-builder"      element={lazy_rb("Workflow Builder", <WorkflowBuilderPage />)} />
        <Route path="playground"            element={rb("Playground",     <PlaygroundPage />)} />
        <Route path="analytics"             element={rb("Analytics",      <AnalyticsDashboardPage />)} />
        <Route path="simulation"            element={rb("Simulation",     <SimulationPage />)} />
        <Route path="audit"                 element={rb("Audit Explorer", <AuditExplorerPage />)} />
        <Route path="rpa/live"              element={rb("RPA Live",       <RpaLivePage />)} />
        <Route path="memory"                element={rb("Memory",         <MemoryExplorerPage />)} />
        <Route path="artifacts"             element={rb("Artifacts",      <ArtifactsBrowserPage />)} />
        <Route path="tools"                 element={rb("Tools",          <ToolsPage />)} />
        <Route path="integrations"          element={rb("Integrations",   <IntegrationsPage />)} />
        <Route path="training-export"       element={rb("Training Export",<TrainingExportPage />)} />
        <Route path="perception"            element={rb("Perception",     <PerceptionPage />)} />
        <Route path="a2a"                   element={rb("A2A",            <A2APage />)} />
        <Route path="notifications"         element={rb("Notifications",  <NotificationCenterPage />)} />
        <Route path="rbac"                  element={rb("RBAC",           <RbacPage />)} />
        <Route path="compliance"            element={rb("Compliance",     <CompliancePage />)} />
        <Route path="goals/:goalId/dna"     element={lazy_rb("Goal DNA",      <GoalDNAPage />)} />
        <Route path="goals/:goalId/diff"    element={lazy_rb("Goal Diff",     <GoalDiffPage />)} />
        <Route path="goals/ghost-run"       element={lazy_rb("Ghost Run",     <GhostRunPage />)} />
        <Route path="templates"             element={lazy_rb("Templates",     <TemplateLibraryPage />)} />
        <Route path="civilization"          element={lazy_rb("Civilization",  <CivilizationPage />)} />
        <Route path="civilization/:id"      element={lazy_rb("Civilization",  <CivilizationPage />)} />
        <Route path="builder"               element={lazy_rb("Builder",       <BuilderPage />)} />
        <Route path="*"                     element={rb("Not Found",          <NotFoundPage />)} />
      </Route>
    </Routes>
  );
}
