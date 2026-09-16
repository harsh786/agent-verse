import { Navigate, Route, Routes } from "react-router-dom";
import { useEffect, useState, lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";
import { useAuthStore, getAuthHeader } from "@/stores/auth";
import { API_BASE } from "@/lib/api/client";
import { AppLayout } from "@/components/ui/AppLayout";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { RouteErrorBoundary } from "@/components/ui/RouteErrorBoundary";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import NotFoundPage from '@/features/errors/NotFoundPage';
import OAuthCallbackPage from '@/features/connectors/OAuthCallbackPage';

// ── Lazy-loaded existing pages ───────────────────────────────────────────────
const DomainsPage = lazy(() => import('@/features/domains/DomainsPage'));
const DomainDetailPage = lazy(() => import('@/features/domains/DomainDetailPage'));
// AI Organization OS
const OrgPage = lazy(() => import('@/features/org/OrgPage').then(m => ({ default: m.OrgPage })));
const OrgListPage = lazy(() => import('@/features/org/OrgListPage').then(m => ({ default: m.OrgListPage })));
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
const BillingPage = lazy(() => import("@/features/settings/BillingPage"));
const RoleEditorPage = lazy(() => import("@/features/settings/RoleEditorPage").then(m => ({ default: m.RoleEditorPage })));
const PrivacySettings = lazy(() => import("@/features/settings/PrivacySettings").then(m => ({ default: m.PrivacySettings })));
const ScheduledMissions = lazy(() => import("@/features/org/ScheduledMissions").then(m => ({ default: m.ScheduledMissions })));
const GatewaySettingsPage = lazy(() => import("@/features/gateway/GatewaySettingsPage").then(m => ({ default: m.GatewaySettingsPage })));
const SelfImprovementPage = lazy(() => import("@/features/analytics/SelfImprovementPage").then(m => ({ default: m.SelfImprovementPage })));
const AgentLabPage = lazy(() => import("@/features/lab/AgentLabPage").then(m => ({ default: m.AgentLabPage })));
const BuilderPage = lazy(() => import("@/features/builder/BuilderPage"));
const SkillsPage = lazy(() => import('@/features/skills/SkillsPage'));
const ModelControlCenter = lazy(() => import('@/features/models/ModelControlCenter').then(m => ({ default: m.ModelControlCenter })));
const GraphExplorerPage = lazy(() => import('@/features/knowledge-graph/GraphExplorerPage').then(m => ({ default: m.GraphExplorerPage })));
const AdminPage = lazy(() => import('@/features/admin/AdminPage'));
const SecurityCenterPage = lazy(() => import('@/features/security/SecurityCenterPage'));
const ChatPage = lazy(() => import('@/features/chat/ChatPage'));
const AgentMemoryPage = lazy(() => import('@/features/chat/AgentMemoryPage'));

// ── Unrouted pages — now wired ───────────────────────────────────────────────
const ChannelMappingsPage = lazy(() => import('@/features/channels/ChannelMappingsPage').then(m => ({ default: m.ChannelMappingsPage })));
const StateMachinesPage   = lazy(() => import('@/features/state-machines/StateMachinesPage').then(m => ({ default: m.StateMachinesPage })));
const TriggersPage            = lazy(() => import('@/features/triggers/TriggersPage').then(m => ({ default: m.TriggersPage })));
const WorkflowListPage        = lazy(() => import('@/features/workflow/WorkflowListPage'));
const WorkflowEditorPage      = lazy(() => import('@/features/workflow/WorkflowBuilderPage'));
const WorkflowRunsPage        = lazy(() => import('@/features/workflow/WorkflowRunsPage'));
const WorkflowRunDetailPage   = lazy(() => import('@/features/workflow/WorkflowRunDetailPage'));
const WorkflowAnalyticsPage   = lazy(() => import('@/features/workflow/WorkflowAnalyticsPage'));
const WorkflowMarketplacePage = lazy(() => import('@/features/workflow/WorkflowMarketplacePage'));
const WorkflowSettingsPage    = lazy(() => import('@/features/workflow/WorkflowSettingsPage'));
const ApprovalInboxPage       = lazy(() => import('@/features/workflow/ApprovalInboxPage'));
const MissionPage      = lazy(() => import('@/features/org/MissionPage').then(m => ({ default: m.MissionPage })));
const DepartmentPage   = lazy(() => import('@/features/org/DepartmentPage').then(m => ({ default: m.DepartmentPage })));
const TeamPage         = lazy(() => import('@/features/org/TeamPage').then(m => ({ default: m.TeamPage })));
const StrategicAdvisorPage = lazy(() => import('@/features/org/StrategicAdvisorPage').then(m => ({ default: m.StrategicAdvisorPage })));
// ── Graphify + Obsidian standalone pages ─────────────────────────────────────
const GraphifyPage  = lazy(() => import('@/features/graphify/GraphifyPage').then(m => ({ default: m.GraphifyPage })));
const ObsidianPage  = lazy(() => import('@/features/obsidian/ObsidianPage').then(m => ({ default: m.ObsidianPage })));
// ── New JARVIS pages ──────────────────────────────────────────────────────────
const EvalSuitesPage       = lazy(() => import('@/features/eval-suites/EvalSuitesPage').then(m => ({ default: m.EvalSuitesPage })));
const PromptVariantsPage   = lazy(() => import('@/features/prompt-variants/PromptVariantsPage').then(m => ({ default: m.PromptVariantsPage })));
const RedTeamPage          = lazy(() => import('@/features/red-team/RedTeamPage').then(m => ({ default: m.RedTeamPage })));
const AgentCredentialsPage = lazy(() => import('@/features/agents/AgentCredentialsPage').then(m => ({ default: m.AgentCredentialsPage })));
const WorkflowEnginePage   = lazy(() => import('@/features/workflow-engine/WorkflowEnginePage').then(m => ({ default: m.WorkflowEnginePage })));

// ── Authenticated app pages — lazy (FE2: moved out of the eager entry chunk) ──
const DashboardPage = lazy(() => import('@/features/dashboard/DashboardPage').then(m => ({ default: m.DashboardPage })));
const AIOpsDashboard = lazy(() => import('@/features/dashboard/AIOpsDashboard').then(m => ({ default: m.AIOpsDashboard })));
const GoalsListPage = lazy(() => import('@/features/goals/GoalsListPage').then(m => ({ default: m.GoalsListPage })));
const GoalDetailPage = lazy(() => import('@/features/goals/GoalDetailPage').then(m => ({ default: m.GoalDetailPage })));
const AgentsListPage = lazy(() => import('@/features/agents/AgentsListPage').then(m => ({ default: m.AgentsListPage })));
const AgentCreatePage = lazy(() => import('@/features/agents/AgentCreatePage').then(m => ({ default: m.AgentCreatePage })));
const AgentDetailPage = lazy(() => import('@/features/agents/AgentDetailPage').then(m => ({ default: m.AgentDetailPage })));
const AgentDashboardPage = lazy(() => import('@/features/agents/AgentDashboardPage').then(m => ({ default: m.AgentDashboardPage })));
const ApprovalsPage = lazy(() => import('@/features/approvals/ApprovalsPage').then(m => ({ default: m.ApprovalsPage })));
const OnboardingPage = lazy(() => import('@/features/onboarding/OnboardingPage').then(m => ({ default: m.OnboardingPage })));
const ConnectorsCatalogPage = lazy(() => import('@/features/connectors/ConnectorsCatalogPage').then(m => ({ default: m.ConnectorsCatalogPage })));
const ConnectorsRegisteredPage = lazy(() => import('@/features/connectors/ConnectorsRegisteredPage').then(m => ({ default: m.ConnectorsRegisteredPage })));
const ModelRegistryPage = lazy(() => import('@/features/models/ModelRegistryPage').then(m => ({ default: m.ModelRegistryPage })));
const ConnectorDetailPage = lazy(() => import('@/features/connectors/ConnectorDetailPage').then(m => ({ default: m.ConnectorDetailPage })));
const SchedulesPage = lazy(() => import('@/features/schedules/SchedulesPage'));
const KnowledgePage = lazy(() => import('@/features/knowledge/KnowledgePage'));
const SourcesPage = lazy(() => import('@/features/ingestion/SourcesPage').then(m => ({ default: m.SourcesPage })));
const GovernancePage = lazy(() => import('@/features/governance/GovernancePage').then(m => ({ default: m.GovernancePage })));
const CollaborationPage = lazy(() => import('@/features/collaboration/CollaborationPage').then(m => ({ default: m.CollaborationPage })));
const CoordinationRunPage = lazy(() => import('@/features/coordination/CoordinationRunPage'));
const ObservabilityPage = lazy(() => import('@/features/observability/ObservabilityPage').then(m => ({ default: m.ObservabilityPage })));
const CostDashboardPage = lazy(() => import('@/features/observability/CostDashboardPage').then(m => ({ default: m.CostDashboardPage })));
const EvalPage = lazy(() => import('@/features/eval/EvalPage').then(m => ({ default: m.EvalPage })));
const MarketplacePage = lazy(() => import('@/features/marketplace/MarketplacePage').then(m => ({ default: m.MarketplacePage })));
const EnterprisePage = lazy(() => import('@/features/enterprise/EnterprisePage').then(m => ({ default: m.EnterprisePage })));
const SettingsPage = lazy(() => import('@/features/settings/SettingsPage').then(m => ({ default: m.SettingsPage })));
const PlaygroundPage = lazy(() => import('@/features/playground/PlaygroundPage').then(m => ({ default: m.PlaygroundPage })));
const AnalyticsDashboardPage = lazy(() => import('@/features/analytics/AnalyticsDashboardPage').then(m => ({ default: m.AnalyticsDashboardPage })));
const SimulationPage = lazy(() => import('@/features/simulation/SimulationPage'));
const AuditExplorerPage = lazy(() => import('@/features/audit/AuditExplorerPage'));
const RpaLivePage = lazy(() => import('@/features/rpa/RpaLivePage'));
const OcrPage = lazy(() => import('@/features/ocr/OcrPage'));
const MemoryExplorerPage = lazy(() => import('@/features/memory/MemoryExplorerPage').then(m => ({ default: m.MemoryExplorerPage })));
const ArtifactsBrowserPage = lazy(() => import('@/features/artifacts/ArtifactsBrowserPage').then(m => ({ default: m.ArtifactsBrowserPage })));
const ToolsPage = lazy(() => import('@/features/tools/ToolsPage').then(m => ({ default: m.ToolsPage })));
const IntegrationsPage = lazy(() => import('@/features/integrations/IntegrationsPage').then(m => ({ default: m.IntegrationsPage })));
const TrainingExportPage = lazy(() => import('@/features/training/TrainingExportPage').then(m => ({ default: m.TrainingExportPage })));
const PerceptionPage = lazy(() => import('@/features/perception/PerceptionPage').then(m => ({ default: m.PerceptionPage })));
const A2APage = lazy(() => import('@/features/a2a/A2APage').then(m => ({ default: m.A2APage })));
const NotificationCenterPage = lazy(() => import('@/features/notifications/NotificationCenterPage'));
const RbacPage = lazy(() => import('@/features/rbac/RbacPage'));
const CompliancePage = lazy(() => import('@/features/compliance/CompliancePage'));

// ── Public / first-paint pages — kept eager (small, needed before auth) ──────
import { LandingPage } from "@/features/landing/LandingPage";
import { AuthPage } from "@/features/auth/AuthPage";
import { SSOCallbackPage } from "@/features/auth/SSOCallbackPage";
import MFAVerifyPage from "@/features/auth/MFAVerifyPage";
import { StatusPage } from "@/features/status/StatusPage";

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
          headers: getAuthHeader(),
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
  }, [apiKey, isAuthenticated, logout, sessionValidated, setSessionValidated, tenantId]);

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
        <Route path="dashboard"             element={lazy_rb("Dashboard",          <DashboardPage />)} />
        <Route path="ai-ops"                element={lazy_rb("AI Ops",             <AIOpsDashboard />)} />
        <Route path="goals"                 element={lazy_rb("Goals",              <GoalsListPage />)} />
        <Route path="goals/:goalId"         element={lazy_rb("Goal Detail",        <GoalDetailPage />)} />
        <Route path="agents"                element={lazy_rb("Agents",             <AgentsListPage />)} />
        <Route path="agents/create"         element={lazy_rb("Create Agent",       <AgentCreatePage />)} />
        <Route path="agents/:agentId"       element={lazy_rb("Agent Detail",       <AgentDetailPage />)} />
        <Route path="agents/:agentId/identity"    element={lazy_rb("Agent Identity",    <AgentIdentityPage />)} />
        <Route path="agents/:agentId/dashboard"   element={lazy_rb("Agent Dashboard",        <AgentDashboardPage />)} />
        <Route path="agents/:agentId/radar"       element={lazy_rb("Agent Radar",       <AgentRadarPage />)} />
        <Route path="agents/:agentId/personality" element={lazy_rb("Agent Personality", <AgentPersonalityPage />)} />
        <Route path="approvals"             element={lazy_rb("Approvals",          <ApprovalsPage />)} />
        <Route path="onboarding"            element={lazy_rb("Onboarding",         <OnboardingPage />)} />
        <Route path="connectors/catalog"    element={lazy_rb("Connectors Catalog", <ConnectorsCatalogPage />)} />
        <Route path="connectors"            element={lazy_rb("Connectors",         <ConnectorsRegisteredPage />)} />
        <Route path="connectors/:connectorId" element={lazy_rb("Connector Detail", <ConnectorDetailPage />)} />
        <Route path="models"                element={lazy_rb("Model Registry",     <ModelRegistryPage />)} />
        <Route path="schedules"             element={lazy_rb("Schedules",          <SchedulesPage />)} />
        <Route path="knowledge"             element={lazy_rb("Knowledge",          <KnowledgePage />)} />
        <Route path="sources"              element={lazy_rb("Sources",            <SourcesPage />)} />
        <Route path="governance"            element={lazy_rb("Governance",         <GovernancePage />)} />
        <Route path="collaboration"         element={lazy_rb("Collaboration",      <CollaborationPage />)} />
        <Route path="coordination/:sessionId?" element={lazy_rb("Coordination",    <CoordinationRunPage />)} />
        <Route path="observability"         element={lazy_rb("Observability",      <ObservabilityPage />)} />
        <Route path="observability/cost"    element={lazy_rb("Cost Dashboard",     <CostDashboardPage />)} />
        <Route path="eval"                  element={lazy_rb("Evaluations",        <EvalPage />)} />
        <Route path="marketplace"           element={lazy_rb("Marketplace",        <MarketplacePage />)} />
        <Route path="domains"               element={lazy_rb("Domains",       <DomainsPage />)} />
        <Route path="domains/:domain"       element={lazy_rb("Domain Detail", <DomainDetailPage />)} />
        <Route path="enterprise"            element={lazy_rb("Enterprise",         <EnterprisePage />)} />
        <Route path="settings"              element={lazy_rb("Settings",           <SettingsPage />)} />
        <Route path="settings/scopes"       element={lazy_rb("Scope Explorer",   <ScopeExplorerPage />)} />
        <Route path="settings/guardrails"   element={lazy_rb("Guardrail Center", <GuardrailCenterPage />)} />
        <Route path="settings/budgets"      element={lazy_rb("Budget Manager",   <BudgetManagerPage />)} />
        <Route path="settings/billing"      element={lazy_rb("Billing",          <BillingPage />)} />
        <Route path="self-improvement"      element={lazy_rb("Self Improvement", <SelfImprovementPage />)} />
        <Route path="lab"                   element={lazy_rb("Agent Lab",        <AgentLabPage />)} />
        <Route path="skills"                element={lazy_rb("Skills",           <SkillsPage />)} />
        <Route path="workflow-builder"      element={lazy_rb("Workflow Builder", <WorkflowBuilderPage />)} />
        <Route path="playground"            element={lazy_rb("Playground",     <PlaygroundPage />)} />
        <Route path="analytics"             element={lazy_rb("Analytics",      <AnalyticsDashboardPage />)} />
        <Route path="simulation"            element={lazy_rb("Simulation",     <SimulationPage />)} />
        <Route path="audit"                 element={lazy_rb("Audit Explorer", <AuditExplorerPage />)} />
        <Route path="rpa/live"              element={lazy_rb("RPA Live",       <RpaLivePage />)} />
        <Route path="ocr"                   element={lazy_rb("OCR Extraction", <OcrPage />)} />
        <Route path="memory"                element={lazy_rb("Memory",         <MemoryExplorerPage />)} />
        <Route path="artifacts"             element={lazy_rb("Artifacts",      <ArtifactsBrowserPage />)} />
        <Route path="tools"                 element={lazy_rb("Tools",          <ToolsPage />)} />
        <Route path="integrations"          element={lazy_rb("Integrations",   <IntegrationsPage />)} />
        <Route path="training-export"       element={lazy_rb("Training Export",<TrainingExportPage />)} />
        <Route path="perception"            element={lazy_rb("Perception",     <PerceptionPage />)} />
        <Route path="a2a"                   element={lazy_rb("A2A",            <A2APage />)} />
        <Route path="notifications"         element={lazy_rb("Notifications",  <NotificationCenterPage />)} />
        <Route path="rbac"                  element={lazy_rb("RBAC",           <RbacPage />)} />
        <Route path="compliance"            element={lazy_rb("Compliance",     <CompliancePage />)} />
        <Route path="goals/:goalId/dna"     element={lazy_rb("Goal DNA",      <GoalDNAPage />)} />
        <Route path="goals/:goalId/diff"    element={lazy_rb("Goal Diff",     <GoalDiffPage />)} />
        <Route path="goals/ghost-run"       element={lazy_rb("Ghost Run",     <GhostRunPage />)} />
        <Route path="templates"             element={lazy_rb("Templates",     <TemplateLibraryPage />)} />
        <Route path="civilization"          element={lazy_rb("Civilization",  <CivilizationPage />)} />
        <Route path="civilization/:id"      element={lazy_rb("Civilization",  <CivilizationPage />)} />
        <Route path="builder"               element={lazy_rb("Builder",       <BuilderPage />)} />
        <Route path="models"                element={lazy_rb("Model Registry", <ModelControlCenter />)} />
        <Route path="knowledge-graph"       element={lazy_rb("Knowledge Graph", <GraphExplorerPage />)} />
        <Route path="graphify"              element={lazy_rb("Graphify",        <GraphifyPage />)} />
        <Route path="obsidian"              element={lazy_rb("Obsidian Mode",   <ObsidianPage />)} />
        <Route path="admin"                 element={lazy_rb("Admin",          <AdminPage />)} />
        <Route path="security"              element={lazy_rb("Security Center", <SecurityCenterPage />)} />
        <Route path="chat"                  element={lazy_rb("Chat",            <ChatPage />)} />
        <Route path="chat/:sessionId"       element={lazy_rb("Chat",            <ChatPage />)} />
        <Route path="chat/memory"           element={lazy_rb("Agent Memory",    <AgentMemoryPage />)} />        {/* AI Organization OS */}
        <Route path="org"             element={lazy_rb("Organizations",   <OrgListPage />)} />
        <Route path="org/:orgId"      element={lazy_rb("Org",             <OrgPage />)} />
        <Route path="org/:orgId/schedules"          element={lazy_rb("Schedules",         <ScheduledMissions />)} />
        <Route path="org/:orgId/gateway"            element={lazy_rb("Org Gateway",       <GatewaySettingsPage />)} />
        <Route path="org/:orgId/mission/:missionId" element={lazy_rb("Mission",           <MissionPage />)} />
        <Route path="org/:orgId/department/:deptId" element={lazy_rb("Department",        <DepartmentPage />)} />
        <Route path="org/:orgId/team/:teamId"       element={lazy_rb("Team",              <TeamPage />)} />
        <Route path="org/:orgId/strategic-advisor"  element={lazy_rb("Strategic Advisor", <StrategicAdvisorPage orgId="" />)} />

        {/* Workflow — the feature pages link to plural /workflows/:id/... ; keep
            singular /workflow as the list too (the sidebar links there). */}
        <Route path="workflow"                     element={lazy_rb("Workflows",            <WorkflowListPage />)} />
        <Route path="workflows"                    element={lazy_rb("Workflows",            <WorkflowListPage />)} />
        <Route path="workflows/marketplace"        element={lazy_rb("Workflow Marketplace", <WorkflowMarketplacePage />)} />
        <Route path="workflows/approvals"          element={lazy_rb("Approval Inbox",       <ApprovalInboxPage />)} />
        <Route path="workflows/:id/edit"           element={lazy_rb("Workflow Editor",      <WorkflowEditorPage />)} />
        <Route path="workflows/:id/runs"           element={lazy_rb("Workflow Runs",        <WorkflowRunsPage />)} />
        <Route path="workflows/:id/runs/:runId"    element={lazy_rb("Workflow Run",         <WorkflowRunDetailPage />)} />
        <Route path="workflows/:id/analytics"      element={lazy_rb("Workflow Analytics",   <WorkflowAnalyticsPage />)} />
        <Route path="workflows/:id/settings"       element={lazy_rb("Workflow Settings",    <WorkflowSettingsPage />)} />

        {/* Automation */}
        <Route path="triggers"        element={lazy_rb("Triggers",       <TriggersPage />)} />
        <Route path="state-machines"  element={lazy_rb("State Machines", <StateMachinesPage />)} />
        <Route path="channel-mappings" element={lazy_rb("Channels",      <ChannelMappingsPage />)} />

        {/* New JARVIS pages */}
        <Route path="eval-suites"              element={lazy_rb("Eval Suites",       <EvalSuitesPage />)} />
        <Route path="prompt-variants"          element={lazy_rb("Prompt Variants",   <PromptVariantsPage />)} />
        <Route path="red-team"                 element={lazy_rb("Red Team",          <RedTeamPage />)} />
        <Route path="agents/:agentId/credentials" element={lazy_rb("Agent Credentials", <AgentCredentialsPage />)} />
        <Route path="workflow-engine"          element={lazy_rb("Workflow Engine",   <WorkflowEnginePage />)} />

        <Route path="settings/roles"  element={lazy_rb("Role Editor",     <RoleEditorPage orgId="" />)} />
        <Route path="settings/privacy" element={lazy_rb("Privacy",        <PrivacySettings />)} />
        <Route path="settings/gateway" element={lazy_rb("Gateway",        <GatewaySettingsPage />)} />
        <Route path="*"                     element={rb("Not Found",          <NotFoundPage />)} />
      </Route>
    </Routes>
  );
}
