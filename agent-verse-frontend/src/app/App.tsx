import { Navigate, Route, Routes } from "react-router-dom";
import { useEffect, useState, lazy, Suspense } from "react";
import { useAuthStore } from "@/stores/auth";
import { AppLayout } from "@/components/ui/AppLayout";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";

const CivilizationPage = lazy(() => import('../features/civilization/CivilizationPage'));
import { AuthPage } from "@/features/auth/AuthPage";
import { SSOCallbackPage } from "@/features/auth/SSOCallbackPage";
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
import { WorkflowBuilderPage } from "@/features/workflow-builder/WorkflowBuilderPage";
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

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

function RequireAuth({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const apiKey = useAuthStore((s) => s.apiKey);
  const tenantId = useAuthStore((s) => s.tenantId);
  const logout = useAuthStore((s) => s.logout);
  const [isChecking, setIsChecking] = useState(false);

  useEffect(() => {
    if (!isAuthenticated || !apiKey) return;

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
        }
      } catch {
        // Keep the session during transient backend/network failures; page queries
        // will show their own connection errors without destroying credentials.
      } finally {
        if (!cancelled) setIsChecking(false);
      }
    }

    void validateSession();

    return () => {
      cancelled = true;
    };
  }, [apiKey, isAuthenticated, logout, tenantId]);

  if (!isAuthenticated) return <Navigate to="/auth" replace />;
  if (isChecking) return null;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/auth" element={<AuthPage />} />
      <Route path="/login" element={<AuthPage />} />
      {/* OAuth2 callback — must be public (no auth required) */}
      <Route path="/auth/callback" element={<SSOCallbackPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <ErrorBoundary>
              <AppLayout />
            </ErrorBoundary>
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="goals" element={<GoalsListPage />} />
        <Route path="goals/:goalId" element={<GoalDetailPage />} />
        <Route path="agents" element={<AgentsListPage />} />
        <Route path="agents/create" element={<AgentCreatePage />} />
        <Route path="agents/:agentId" element={<AgentDetailPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="onboarding" element={<OnboardingPage />} />
        <Route path="connectors/catalog" element={<ConnectorsCatalogPage />} />
        <Route path="connectors" element={<ConnectorsRegisteredPage />} />
        <Route path="schedules" element={<SchedulesPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="governance" element={<GovernancePage />} />
        <Route path="collaboration" element={<CollaborationPage />} />
        <Route path="observability" element={<ObservabilityPage />} />
        <Route path="observability/cost" element={<CostDashboardPage />} />
        <Route path="eval" element={<EvalPage />} />
        <Route path="marketplace" element={<MarketplacePage />} />
        <Route path="enterprise" element={<EnterprisePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="workflow-builder" element={<WorkflowBuilderPage />} />
        <Route path="playground" element={<PlaygroundPage />} />
        <Route path="analytics" element={<AnalyticsDashboardPage />} />
        <Route path="simulation" element={<SimulationPage />} />
        <Route path="audit" element={<AuditExplorerPage />} />
        <Route path="rpa/live" element={<RpaLivePage />} />
        <Route path="memory" element={<MemoryExplorerPage />} />
        <Route path="artifacts" element={<ArtifactsBrowserPage />} />
        <Route path="tools" element={<ToolsPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="training-export" element={<TrainingExportPage />} />
        <Route path="perception" element={<PerceptionPage />} />
        <Route path="a2a" element={<A2APage />} />
        <Route path="notifications" element={<NotificationCenterPage />} />
        <Route path="rbac" element={<RbacPage />} />
        <Route path="compliance" element={<CompliancePage />} />
        <Route path="connectors/:connectorId" element={<ConnectorDetailPage />} />
        <Route path="agents/:agentId/dashboard" element={<AgentDashboardPage />} />
        <Route path="civilization" element={<Suspense fallback={<div className="p-6 text-gray-400">Loading...</div>}><CivilizationPage /></Suspense>} />
        <Route path="civilization/:id" element={<Suspense fallback={<div className="p-6 text-gray-400">Loading...</div>}><CivilizationPage /></Suspense>} />
      </Route>
    </Routes>
  );
}
