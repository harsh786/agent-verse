import { Navigate, Route, Routes } from "react-router-dom";
import { useAuthStore } from "@/stores/auth";
import { AppLayout } from "@/components/ui/AppLayout";
import { AuthPage } from "@/features/auth/AuthPage";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { GoalsListPage } from "@/features/goals/GoalsListPage";
import { GoalDetailPage } from "@/features/goals/GoalDetailPage";
import { AgentsListPage } from "@/features/agents/AgentsListPage";
import { AgentCreatePage } from "@/features/agents/AgentCreatePage";
import { ConnectorsCatalogPage } from "@/features/connectors/ConnectorsCatalogPage";
import { ConnectorsRegisteredPage } from "@/features/connectors/ConnectorsRegisteredPage";
import { SchedulesPage } from "@/features/schedules/SchedulesPage";
import { KnowledgePage } from "@/features/knowledge/KnowledgePage";
import { GovernancePage } from "@/features/governance/GovernancePage";
import { CollaborationPage } from "@/features/collaboration/CollaborationPage";
import { ObservabilityPage } from "@/features/observability/ObservabilityPage";
import { EvalPage } from "@/features/eval/EvalPage";
import { MarketplacePage } from "@/features/marketplace/MarketplacePage";
import { EnterprisePage } from "@/features/enterprise/EnterprisePage";
import { SettingsPage } from "@/features/settings/SettingsPage";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (!isAuthenticated) return <Navigate to="/auth" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/auth" element={<AuthPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="goals" element={<GoalsListPage />} />
        <Route path="goals/:goalId" element={<GoalDetailPage />} />
        <Route path="agents" element={<AgentsListPage />} />
        <Route path="agents/create" element={<AgentCreatePage />} />
        <Route path="connectors/catalog" element={<ConnectorsCatalogPage />} />
        <Route path="connectors" element={<ConnectorsRegisteredPage />} />
        <Route path="schedules" element={<SchedulesPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="governance" element={<GovernancePage />} />
        <Route path="collaboration" element={<CollaborationPage />} />
        <Route path="observability" element={<ObservabilityPage />} />
        <Route path="eval" element={<EvalPage />} />
        <Route path="marketplace" element={<MarketplacePage />} />
        <Route path="enterprise" element={<EnterprisePage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}
