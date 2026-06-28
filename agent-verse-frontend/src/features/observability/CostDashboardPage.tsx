import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DollarSign, TrendingUp, AlertCircle } from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { analyticsApi } from "@/lib/api/client";
import { ThemedLineChart, ThemedBarChart } from "@/components/charts";

const PERIODS = [7, 30, 90] as const;
type Period = typeof PERIODS[number];

const formatCost = (v: number) => `$${v.toFixed(v >= 0.01 ? 2 : 4)}`;

export function CostDashboardPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [days, setDays] = useState<Period>(30);

  // Legacy KPI endpoint
  const { data: kpiData, isLoading: kpiLoading } = useQuery({
    queryKey: ["cost-metrics-kpi"],
    queryFn: async () => {
      const res = await fetch(
        `${(import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000"}/goals/cost-metrics`,
        { headers: { "X-API-Key": apiKey } }
      );
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json() as Promise<{
        cost_today_usd: number;
        daily_budget_usd: number;
        budget_utilization: number;
        active_goals: number;
        total_goals: number;
        goals_today: number;
        per_goal_budget_usd: number;
      }>;
    },
    refetchInterval: 30_000,
    enabled: !!apiKey,
  });

  // Detailed analytics (time-series + model breakdown)
  const { data: analytics } = useQuery({
    queryKey: ["analytics-costs", days],
    queryFn: () => analyticsApi.getCostMetrics(days),
    enabled: !!apiKey,
  });

  const utilization = (kpiData?.budget_utilization ?? 0) * 100;
  const utilizationColor =
    utilization > 80
      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
      : utilization > 50
      ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
      : "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400";

  // Cost by model bar chart data
  const modelChartData = analytics?.cost_by_model
    ? Object.entries(analytics.cost_by_model)
        .map(([model, cost]) => ({ model: model.split("/").pop()?.slice(0, 14) ?? model, cost }))
        .sort((a, b) => (b.cost as number) - (a.cost as number))
        .slice(0, 8)
    : [];

  // Daily cost line data
  const dailyData = analytics?.cost_by_day?.slice(-days) ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Cost Dashboard</h1>
          <p className="text-muted-foreground text-sm mt-1">
            LLM spend, budget tracking and goal economics
          </p>
        </div>
        {/* Time period selector */}
        <div className="flex gap-1 border rounded-lg overflow-hidden">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setDays(p)}
              aria-pressed={days === p}
              className={`px-3 py-1.5 text-sm transition-colors ${
                days === p ? "bg-primary text-primary-foreground" : "hover:bg-muted"
              }`}
            >
              {p}d
            </button>
          ))}
        </div>
      </div>

      {kpiLoading ? (
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
              <p className="text-2xl font-bold">
                {formatCost(kpiData?.cost_today_usd ?? 0)}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                of ${(kpiData?.daily_budget_usd ?? 0).toFixed(2)} daily budget
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
              <p className="text-2xl font-bold">{kpiData?.goals_today ?? 0}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                avg {kpiData?.goals_today
                  ? formatCost((kpiData.cost_today_usd ?? 0) / kpiData.goals_today)
                  : "$0.00"} / goal
              </p>
            </div>

            <div className="bg-card border border-border rounded-xl p-5">
              <p className="text-sm text-muted-foreground mb-2">Total ({days}d)</p>
              <p className="text-2xl font-bold">
                {formatCost(analytics?.total_cost_usd ?? 0)}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {days}-day period
              </p>
            </div>
          </div>

          {/* Budget progress bar */}
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-semibold text-sm">Daily Budget Consumption</h2>
              {utilization > 80 && (
                <div className="flex items-center gap-1.5 text-red-600 dark:text-red-400 text-xs">
                  <AlertCircle className="h-3.5 w-3.5" />
                  Budget almost exhausted
                </div>
              )}
            </div>
            <div className="w-full bg-muted rounded-full h-3">
              <div
                className={`h-3 rounded-full transition-all ${
                  utilization > 80 ? "bg-red-500" : utilization > 50 ? "bg-yellow-500" : "bg-green-500"
                }`}
                style={{ width: `${Math.min(utilization, 100)}%` }}
              />
            </div>
            <div className="flex justify-between text-xs text-muted-foreground mt-1.5">
              <span>{formatCost(kpiData?.cost_today_usd ?? 0)} used</span>
              <span>${(kpiData?.daily_budget_usd ?? 0).toFixed(2)} limit</span>
            </div>
          </div>

          {/* Charts row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Daily cost time-series */}
            <div className="bg-card border border-border rounded-xl p-5">
              <h2 className="font-semibold text-sm mb-4">Daily Cost ({days}d)</h2>
              {dailyData.length === 0 ? (
                <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
                  No data yet
                </div>
              ) : (
                <ThemedLineChart
                  data={dailyData as Record<string, unknown>[]}
                  lines={[{ key: "cost_usd", label: "Cost" }]}
                  xKey="date"
                  height={200}
                  formatValue={formatCost}
                />
              )}
            </div>

            {/* Cost by model */}
            <div className="bg-card border border-border rounded-xl p-5">
              <h2 className="font-semibold text-sm mb-4">Cost by Model</h2>
              {modelChartData.length === 0 ? (
                <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
                  No model data yet
                </div>
              ) : (
                <ThemedBarChart
                  data={modelChartData as Record<string, unknown>[]}
                  bars={[{ key: "cost", label: "Cost" }]}
                  xKey="model"
                  height={200}
                  layout="vertical"
                  formatValue={(v) => `$${v.toFixed(3)}`}
                />
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
