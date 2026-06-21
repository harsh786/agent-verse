import { useQuery } from "@tanstack/react-query";
import { Target, CheckCircle, Clock, DollarSign, Activity } from "lucide-react";
import { goalsApi } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";

function KpiCard({
  icon: Icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  sub?: string;
  color: string;
}) {
  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="text-2xl font-bold mt-1">{value}</p>
          {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
        </div>
        <div className={`p-2 rounded-lg ${color}`}>
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    complete: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    executing: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
    planning: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
    failed: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
    waiting_human: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  };
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${colors[status] ?? "bg-muted text-muted-foreground"}`}>
      {status.replace("_", " ")}
    </span>
  );
}

export function DashboardPage() {
  const tenantId = useAuthStore((s) => s.tenantId);
  const { data, isLoading } = useQuery({
    queryKey: ["goals", tenantId],
    queryFn: () => goalsApi.list(),
    refetchInterval: 10_000,
  });

  const goals = data?.goals ?? [];
  const activeCount = goals.filter((g) => ["executing", "planning"].includes(g.status)).length;
  const successCount = goals.filter((g) => g.status === "complete").length;
  const successRate = goals.length ? Math.round((successCount / goals.length) * 100) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-muted-foreground text-sm mt-1">Real-time platform overview</p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          icon={Target}
          label="Active Goals"
          value={isLoading ? "—" : activeCount}
          sub="executing + planning"
          color="bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400"
        />
        <KpiCard
          icon={CheckCircle}
          label="Success Rate"
          value={isLoading ? "—" : `${successRate}%`}
          sub={`${successCount} of ${goals.length} complete`}
          color="bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400"
        />
        <KpiCard
          icon={Clock}
          label="Avg Latency"
          value="—"
          sub="p95 not yet tracked"
          color="bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400"
        />
        <KpiCard
          icon={DollarSign}
          label="Cost Today"
          value="$0.00"
          sub="cost ledger pending"
          color="bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400"
        />
      </div>

      {/* Activity Feed */}
      <div className="bg-card border border-border rounded-xl">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
          <Activity className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="font-semibold text-sm">Live Activity Feed</h2>
        </div>

        {isLoading ? (
          <div className="px-5 py-8 text-center text-sm text-muted-foreground">Loading…</div>
        ) : goals.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-muted-foreground">
            No goals yet. Submit your first goal from the Goals page.
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {goals.slice(0, 20).map((goal) => (
              <li key={goal.id} className="flex items-center justify-between px-5 py-3 hover:bg-accent/50 transition-colors">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate">{goal.goal}</p>
                  <p className="text-xs text-muted-foreground mt-0.5 font-mono">{goal.id}</p>
                </div>
                <StatusBadge status={goal.status} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
