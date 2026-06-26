import { useQuery } from "@tanstack/react-query";
import { DollarSign, TrendingUp, AlertCircle } from "lucide-react";
import { useAuthStore } from "@/stores/auth";

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000";

interface CostMetrics {
  cost_today_usd: number;
  daily_budget_usd: number;
  budget_utilization: number;
  active_goals: number;
  total_goals: number;
  goals_today: number;
  per_goal_budget_usd: number;
}

export function CostDashboardPage() {
  const apiKey = useAuthStore((s) => s.apiKey);

  const { data, isLoading } = useQuery({
    queryKey: ["cost-metrics"],
    queryFn: async (): Promise<CostMetrics> => {
      const res = await fetch(`${API_BASE}/goals/cost-metrics`, {
        headers: { "X-API-Key": apiKey },
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    refetchInterval: 30_000,
    enabled: !!apiKey,
  });

  const utilization = (data?.budget_utilization ?? 0) * 100;
  const utilizationColor =
    utilization > 80 ? "text-red-600 bg-red-100" :
    utilization > 50 ? "text-yellow-600 bg-yellow-100" :
    "text-green-600 bg-green-100";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Cost Dashboard</h1>
        <p className="text-muted-foreground text-sm mt-1">
          LLM spend, budget tracking and goal economics
        </p>
      </div>

      {isLoading ? (
        <div className="text-center py-10 text-sm text-muted-foreground">Loading cost data…</div>
      ) : (
        <>
          {/* KPI Row */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-card border border-border rounded-xl p-5">
              <div className="flex items-center gap-2 mb-2">
                <DollarSign className="h-4 w-4 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">Cost Today</p>
              </div>
              <p className="text-2xl font-bold">${(data?.cost_today_usd ?? 0).toFixed(4)}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                of ${(data?.daily_budget_usd ?? 0).toFixed(2)} daily budget
              </p>
            </div>

            <div className="bg-card border border-border rounded-xl p-5">
              <div className="flex items-center gap-2 mb-2">
                <TrendingUp className="h-4 w-4 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">Budget Used</p>
              </div>
              <p className={`text-2xl font-bold px-2 py-0.5 rounded-lg inline-block ${utilizationColor}`}>
                {utilization.toFixed(1)}%
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">of daily limit</p>
            </div>

            <div className="bg-card border border-border rounded-xl p-5">
              <p className="text-sm text-muted-foreground mb-2">Goals Today</p>
              <p className="text-2xl font-bold">{data?.goals_today ?? 0}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                avg ${data?.goals_today
                  ? ((data.cost_today_usd ?? 0) / data.goals_today).toFixed(4)
                  : "0.0000"} / goal
              </p>
            </div>

            <div className="bg-card border border-border rounded-xl p-5">
              <p className="text-sm text-muted-foreground mb-2">Per-Goal Budget</p>
              <p className="text-2xl font-bold">${(data?.per_goal_budget_usd ?? 0).toFixed(2)}</p>
              <p className="text-xs text-muted-foreground mt-0.5">max per goal</p>
            </div>
          </div>

          {/* Budget progress bar */}
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-semibold text-sm">Daily Budget Consumption</h2>
              {utilization > 80 && (
                <div className="flex items-center gap-1.5 text-red-600 text-xs">
                  <AlertCircle className="h-3.5 w-3.5" />
                  Budget almost exhausted
                </div>
              )}
            </div>
            <div className="w-full bg-muted rounded-full h-3">
              <div
                className={`h-3 rounded-full transition-all ${
                  utilization > 80 ? "bg-red-500" :
                  utilization > 50 ? "bg-yellow-500" :
                  "bg-green-500"
                }`}
                style={{ width: `${Math.min(utilization, 100)}%` }}
              />
            </div>
            <div className="flex justify-between text-xs text-muted-foreground mt-1.5">
              <span>${(data?.cost_today_usd ?? 0).toFixed(4)} used</span>
              <span>${(data?.daily_budget_usd ?? 0).toFixed(2)} limit</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
