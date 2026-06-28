/**
 * Mission Control Dashboard
 *
 * The world's most compelling agent OS dashboard:
 * - Live KPI cards with animated tickers
 * - Real-time activity stream (clickable, navigates to goal detail)
 * - Agent orbit visualization
 * - Quick-submit goal from dashboard
 * - Pending approvals banner with count
 * - Cost today with live ticker
 * - Quick action buttons
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Zap,
  CheckCircle2,
  AlertTriangle,
  Activity,
  DollarSign,
  Bot,
  ChevronRight,
  Play,
  Target,
  BarChart3,
  Shield,
  Plus,
  ArrowUpRight,
} from "lucide-react";
import { goalsApi, governanceApi, agentsApi, analyticsApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { LiveActivityStream } from "./components/LiveActivityStream";
import { AgentOrbitView } from "./components/AgentOrbitView";
import { toast } from "@/stores/toast";

// ── KPI Card ──────────────────────────────────────────────────────────────────

function KpiCard({
  icon: Icon,
  label,
  value,
  sub,
  trend,
  accent = "blue",
  isLoading,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  sub?: string;
  trend?: "up" | "down" | "neutral";
  accent?: "blue" | "green" | "amber" | "red" | "violet";
  isLoading?: boolean;
  onClick?: () => void;
}) {
  const ACCENT_CLASSES = {
    blue:   "bg-blue-50   dark:bg-blue-950/30  text-blue-600   dark:text-blue-400   border-blue-100   dark:border-blue-900",
    green:  "bg-green-50  dark:bg-green-950/30 text-green-600  dark:text-green-400  border-green-100  dark:border-green-900",
    amber:  "bg-amber-50  dark:bg-amber-950/30 text-amber-600  dark:text-amber-400  border-amber-100  dark:border-amber-900",
    red:    "bg-red-50    dark:bg-red-950/30   text-red-600    dark:text-red-400    border-red-100    dark:border-red-900",
    violet: "bg-violet-50 dark:bg-violet-950/30 text-violet-600 dark:text-violet-400 border-violet-100 dark:border-violet-900",
  };

  return (
    <button
      onClick={onClick}
      className={`bg-card border border-border rounded-xl p-4 text-left hover:border-primary/30 hover:shadow-sm transition-all ${
        onClick ? "cursor-pointer" : "cursor-default"
      }`}
    >
      <div className="flex items-start justify-between">
        <div className={`p-2 rounded-lg border ${ACCENT_CLASSES[accent]}`}>
          <Icon className="h-4 w-4" aria-hidden="true" />
        </div>
        {trend && (
          <span
            className={`text-xs font-medium ${
              trend === "up"
                ? "text-green-600 dark:text-green-400"
                : trend === "down"
                ? "text-red-500 dark:text-red-400"
                : "text-muted-foreground"
            }`}
          >
            {trend === "up" ? "↑" : trend === "down" ? "↓" : "—"}
          </span>
        )}
      </div>
      <div className="mt-3">
        {isLoading ? (
          <Skeleton className="h-7 w-16 mb-1" />
        ) : (
          <p className="text-2xl font-bold text-foreground tabular-nums">{value}</p>
        )}
        <p className="text-xs text-muted-foreground mt-0.5">{label}</p>
        {sub && <p className="text-xs text-muted-foreground/60 mt-0.5">{sub}</p>}
      </div>
    </button>
  );
}

// ── Quick Goal Submit ─────────────────────────────────────────────────────────

function QuickGoalSubmit() {
  const [goal, setGoal] = useState("");
  const qc = useQueryClient();
  const navigate = useNavigate();

  const submit = useMutation({
    mutationFn: () => goalsApi.submit({ goal: goal.trim() }),
    onSuccess: (data) => {
      toast({ kind: "success", message: "Goal submitted! Tracking execution…" });
      qc.invalidateQueries({ queryKey: ["goals"] });
      navigate(`/goals/${data.goal_id ?? data.id}`);
    },
    onError: (e) => toast({ kind: "error", message: `Failed: ${String(e)}` }),
  });

  return (
    <div className="bg-card border border-border rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="p-1.5 rounded-lg bg-primary/10">
          <Zap className="h-4 w-4 text-primary" />
        </div>
        <h2 className="text-sm font-semibold">Quick Goal</h2>
      </div>
      <div className="flex gap-2">
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && goal.trim() && submit.mutate()}
          placeholder="What should your agents do? (Enter to submit)"
          className="flex-1 px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary placeholder:text-muted-foreground"
          disabled={submit.isPending}
          aria-label="Goal description"
        />
        <button
          onClick={() => submit.mutate()}
          disabled={!goal.trim() || submit.isPending}
          className="px-4 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5 shrink-0"
          aria-label="Submit goal"
        >
          <Play className="h-3.5 w-3.5" />
          {submit.isPending ? "Running…" : "Run"}
        </button>
      </div>
    </div>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export function DashboardPage() {
  const navigate = useNavigate();

  // ── Data fetching ──────────────────────────────────────────────────────
  const { data: goals = [], isLoading: goalsLoading } = useQuery({
    queryKey: ["goals"],
    queryFn: () => goalsApi.list().then((d) => (d as any).goals ?? d ?? []), // eslint-disable-line @typescript-eslint/no-explicit-any
    refetchInterval: 5_000,
  });

  const { data: metrics } = useQuery({
    queryKey: ["goal-metrics"],
    queryFn: () => governanceApi.goalMetrics(),
    refetchInterval: 15_000,
  });

  const { data: approvals = [] } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 10_000,
  });

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: () => agentsApi.list(),
    refetchInterval: 30_000,
  });

  const { data: costData } = useQuery({
    queryKey: ["cost-today"],
    queryFn: () => analyticsApi.getCostMetrics(1),
    refetchInterval: 30_000,
  });

  // ── Computed values ────────────────────────────────────────────────────
  const goalsArr = Array.isArray(goals) ? goals : (goals as any).goals ?? []; // eslint-disable-line @typescript-eslint/no-explicit-any
  const activeGoals = goalsArr.filter((g: any) => // eslint-disable-line @typescript-eslint/no-explicit-any
    ["planning", "executing", "verifying"].includes(g.status),
  );
  const pendingApprovals = (approvals as any[]).filter((a: any) => a.status === "pending"); // eslint-disable-line @typescript-eslint/no-explicit-any
  const completedGoals = goalsArr.filter(
    (g: any) => g.status === "complete" || g.status === "completed", // eslint-disable-line @typescript-eslint/no-explicit-any
  );
  const failedGoals = goalsArr.filter((g: any) => g.status === "failed"); // eslint-disable-line @typescript-eslint/no-explicit-any
  const successRate =
    goalsArr.length > 0
      ? Math.round((completedGoals.length / goalsArr.length) * 100)
      : 0;
  const costToday =
    (costData as any)?.cost_today_usd ?? // eslint-disable-line @typescript-eslint/no-explicit-any
    (metrics as any)?.cost_today_usd ?? // eslint-disable-line @typescript-eslint/no-explicit-any
    0;

  // Build agent orbit data
  const agentOrbitNodes = (Array.isArray(agents) ? agents : [])
    .map((a: any) => { // eslint-disable-line @typescript-eslint/no-explicit-any
      const agentGoals = goalsArr.filter((g: any) => g.agent_id === a.agent_id); // eslint-disable-line @typescript-eslint/no-explicit-any
      const isActive = agentGoals.some((g: any) => // eslint-disable-line @typescript-eslint/no-explicit-any
        ["planning", "executing", "verifying"].includes(g.status),
      );
      return {
        id: a.agent_id as string,
        label: (a.name ?? a.agent_id) as string,
        status: (isActive ? "active" : "idle") as "active" | "idle" | "error",
        goalCount: (agentGoals as any[]).filter((g: any) => g.status !== "failed").length, // eslint-disable-line @typescript-eslint/no-explicit-any
      };
    })
    .slice(0, 8);

  // ── Quick action items ─────────────────────────────────────────────────
  const quickActions = [
    { label: "View Goals",    icon: Target,   path: "/goals",               color: "text-blue-500"   },
    { label: "Manage Agents", icon: Bot,       path: "/agents",              color: "text-violet-500" },
    { label: "Analytics",     icon: BarChart3, path: "/analytics",           color: "text-green-500"  },
    { label: "Governance",    icon: Shield,    path: "/governance",          color: "text-amber-500"  },
  ];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* ── Header ───────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Mission Control</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {activeGoals.length > 0
              ? `${activeGoals.length} goal${activeGoals.length !== 1 ? "s" : ""} running right now`
              : "All systems nominal"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div
            className={`h-2 w-2 rounded-full ${
              activeGoals.length > 0 ? "bg-green-500 animate-pulse" : "bg-muted-foreground/40"
            }`}
            aria-hidden="true"
          />
          <span className="text-xs text-muted-foreground">
            {activeGoals.length > 0 ? "Live" : "Standby"}
          </span>
        </div>
      </div>

      {/* ── Pending approvals banner ──────────────────────────────────── */}
      {pendingApprovals.length > 0 && (
        <button
          onClick={() => navigate("/approvals")}
          className="w-full flex items-center justify-between p-3 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-xl text-amber-800 dark:text-amber-300 hover:opacity-90 transition-opacity"
          aria-label={`${pendingApprovals.length} pending approvals`}
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span className="text-sm font-medium">
              {pendingApprovals.length} action
              {pendingApprovals.length !== 1 ? "s require" : " requires"} your approval
            </span>
          </div>
          <div className="flex items-center gap-1 text-xs">
            Review <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </div>
        </button>
      )}

      {/* ── KPI Cards ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          icon={Activity}
          label="Active Goals"
          value={goalsLoading ? "—" : activeGoals.length}
          sub={`${goalsArr.length} total`}
          accent="blue"
          isLoading={goalsLoading}
          onClick={() => navigate("/goals")}
          trend={activeGoals.length > 0 ? "up" : "neutral"}
        />
        <KpiCard
          icon={CheckCircle2}
          label="Success Rate"
          value={goalsLoading ? "—" : `${successRate}%`}
          sub={`${completedGoals.length} completed`}
          accent="green"
          isLoading={goalsLoading}
          onClick={() => navigate("/analytics")}
          trend={successRate > 70 ? "up" : successRate > 40 ? "neutral" : "down"}
        />
        <KpiCard
          icon={DollarSign}
          label="Cost Today"
          value={goalsLoading ? "—" : `$${(costToday as number).toFixed(4)}`}
          sub={`${failedGoals.length} failed`}
          accent="amber"
          isLoading={goalsLoading}
          onClick={() => navigate("/observability/cost")}
        />
        <KpiCard
          icon={Bot}
          label="Agents"
          value={Array.isArray(agents) ? agents.length : "—"}
          sub={`${agentOrbitNodes.filter((a) => a.status === "active").length} active`}
          accent="violet"
          onClick={() => navigate("/agents")}
        />
      </div>

      {/* ── Quick Goal Submit ──────────────────────────────────────────── */}
      <QuickGoalSubmit />

      {/* ── Main content: Activity + Orbit ───────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Live Activity Stream — takes 3/5 */}
        <div className="lg:col-span-3 bg-card border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <div
                className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse"
                aria-hidden="true"
              />
              <h2 className="text-sm font-semibold">Live Activity</h2>
            </div>
            <button
              onClick={() => navigate("/goals")}
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
              aria-label="View all goals"
            >
              View all <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
            </button>
          </div>
          {goalsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-lg" />
              ))}
            </div>
          ) : (
            <LiveActivityStream goals={goalsArr} maxItems={10} />
          )}
        </div>

        {/* Agent Orbit — takes 2/5 */}
        <div className="lg:col-span-2 bg-card border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold">Agent Network</h2>
            <button
              onClick={() => navigate("/agents")}
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
              aria-label="Manage agents"
            >
              Manage <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
            </button>
          </div>
          {agentOrbitNodes.length > 0 ? (
            <AgentOrbitView
              agents={agentOrbitNodes}
              width={280}
              height={200}
              className="mx-auto"
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-48 text-muted-foreground">
              <Bot className="h-10 w-10 opacity-20 mb-2" aria-hidden="true" />
              <p className="text-sm">No agents configured</p>
              <button
                onClick={() => navigate("/agents/create")}
                className="mt-3 flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:opacity-90 transition-opacity"
              >
                <Plus className="h-3 w-3" aria-hidden="true" />
                Create Agent
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Quick Actions ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {quickActions.map((action) => (
          <button
            key={action.path}
            onClick={() => navigate(action.path)}
            className="flex items-center gap-2.5 p-3 bg-card border border-border rounded-xl hover:border-primary/30 hover:shadow-sm transition-all text-left"
            aria-label={`Navigate to ${action.label}`}
          >
            <action.icon className={`h-4 w-4 shrink-0 ${action.color}`} aria-hidden="true" />
            <span className="text-sm font-medium text-foreground">{action.label}</span>
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground ml-auto shrink-0" aria-hidden="true" />
          </button>
        ))}
      </div>
    </div>
  );
}
