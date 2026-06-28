import { useState } from "react";
import type { JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { DollarSign, TrendingUp, AlertCircle, Zap } from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { analyticsApi, costsApi } from "@/lib/api/client";
import { ThemedLineChart, ThemedBarChart } from "@/components/charts";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";

const PERIODS = [7, 30, 90] as const;
type Period = (typeof PERIODS)[number];

const formatCost = (v: number): string => `$${v.toFixed(v >= 0.01 ? 2 : 4)}`;

// ── Cost Prediction Widget ────────────────────────────────────────────────────

function CostPredictionWidget({ goalText }: { goalText: string }): JSX.Element | null {
  const { data, isFetching } = useQuery({
    queryKey: ["cost-predict", goalText],
    queryFn: () => costsApi.predict(goalText),
    enabled: goalText.trim().length > 10,
    staleTime: 60_000,
  });

  if (!goalText.trim() || goalText.length <= 10) return null;

  return (
    <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
      <Zap className="h-3 w-3" />
      {isFetching ? (
        <span>Estimating cost…</span>
      ) : data ? (
        <span>
          Est. cost: <span className="font-medium text-foreground">{formatCost(data.estimated_cost_usd.mean)}</span>
          {" "}({data.confidence} confidence)
        </span>
      ) : null}
    </div>
  );
}

// ── Anomaly Alerts Panel ──────────────────────────────────────────────────────

function AnomalyPanel(): JSX.Element {
  const { data: anomalies = [], isLoading } = useQuery({
    queryKey: ["cost-anomalies"],
    queryFn: () => costsApi.getAnomalies(),
    refetchInterval: 60_000,
  });

  if (isLoading) return <Skeleton className="h-16 w-full" />;
  if (anomalies.length === 0) return <></>;

  return (
    <div className="space-y-2">
      {anomalies.map((anomaly) => (
        <div
          key={anomaly.id}
          className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-sm ${
            anomaly.severity === "high"
              ? "border-red-300/60 bg-red-50/40 dark:bg-red-900/20"
              : anomaly.severity === "medium"
              ? "border-yellow-300/60 bg-yellow-50/40 dark:bg-yellow-900/20"
              : "border-border bg-card"
          }`}
        >
          <AlertCircle
            className={`h-4 w-4 mt-0.5 flex-shrink-0 ${
              anomaly.severity === "high"
                ? "text-red-600 dark:text-red-400"
                : anomaly.severity === "medium"
                ? "text-yellow-600 dark:text-yellow-400"
                : "text-muted-foreground"
            }`}
          />
          <div className="min-w-0 flex-1">
            <p className="font-medium capitalize">{anomaly.type.replace(/_/g, " ")}</p>
            <p className="text-muted-foreground text-xs mt-0.5">{anomaly.message}</p>
          </div>
          <span className="text-xs text-muted-foreground flex-shrink-0">
            +{formatCost(anomaly.cost_delta_usd)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── CostDashboardPage ─────────────────────────────────────────────────────────

export function CostDashboardPage(): JSX.Element {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [days, setDays] = useState<Period>(30);
  const [predGoal, setPredGoal] = useState("");

  // Legacy KPI endpoint
  const { data: kpiData, isLoading: kpiLoading } = useQuery({
    queryKey: ["cost-metrics-kpi"],
    queryFn: async () => {
      const res = await fetch(
        `${(import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000"}/goals/cost-metrics`, // eslint-disable-line @typescript-eslint/no-explicit-any
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

  const { data: analytics } = useQuery({
    queryKey: ["analytics-costs", days],
    queryFn: () => analyticsApi.getCostMetrics(days),
    enabled: !!apiKey,
  });

  const { data: perAgent = [], isLoading: agentCostLoading } = useQuery({
    queryKey: ["cost-per-agent"],
    queryFn: () => costsApi.getPerAgent(),
    enabled: !!apiKey,
  });

  const utilization = (kpiData?.budget_utilization ?? 0) * 100;
  const utilizationColor =
    utilization > 80
      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
      : utilization > 50
      ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
      : "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400";

  const modelChartData = analytics?.cost_by_model
    ? Object.entries(analytics.cost_by_model)
        .map(([model, cost]) => ({ model: model.split("/").pop()?.slice(0, 14) ?? model, cost }))
        .sort((a, b) => (b.cost as number) - (a.cost as number))
        .slice(0, 8)
    : [];

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

      {/* Anomaly alerts */}
      <AnomalyPanel />

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
                avg{" "}
                {kpiData?.goals_today
                  ? formatCost((kpiData.cost_today_usd ?? 0) / kpiData.goals_today)
                  : "$0.00"}{" "}
                / goal
              </p>
            </div>

            <div className="bg-card border border-border rounded-xl p-5">
              <p className="text-sm text-muted-foreground mb-2">Total ({days}d)</p>
              <p className="text-2xl font-bold">
                {formatCost(analytics?.total_cost_usd ?? 0)}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">{days}-day period</p>
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

          {/* Cost prediction */}
          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="font-semibold text-sm mb-3 flex items-center gap-2">
              <Zap className="h-4 w-4" /> Cost Predictor
            </h2>
            <input
              type="text"
              value={predGoal}
              onChange={(e) => setPredGoal(e.target.value)}
              placeholder="Type a goal to estimate its cost…"
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background"
              onKeyDown={(e) => {
                if (e.key === "Enter") toast({ kind: "info", message: "Estimating cost…" });
              }}
            />
            <CostPredictionWidget goalText={predGoal} />
          </div>

          {/* Charts row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
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

          {/* Per-agent cost breakdown */}
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-border flex items-center justify-between">
              <h2 className="font-semibold text-sm">Cost by Agent</h2>
            </div>
            {agentCostLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : perAgent.length === 0 ? (
              <p className="px-5 py-4 text-sm text-muted-foreground">No per-agent cost data yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30">
                      <th className="px-5 py-3 text-left text-xs font-medium text-muted-foreground">Agent</th>
                      <th className="px-5 py-3 text-right text-xs font-medium text-muted-foreground">Total Cost</th>
                      <th className="px-5 py-3 text-right text-xs font-medium text-muted-foreground">Goals</th>
                      <th className="px-5 py-3 text-right text-xs font-medium text-muted-foreground">Avg / Goal</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {perAgent.map((ac) => (
                      <tr key={ac.agent_id} className="hover:bg-muted/20 transition-colors">
                        <td className="px-5 py-3 font-medium">{ac.agent_name}</td>
                        <td className="px-5 py-3 text-right font-mono">{formatCost(ac.total_cost_usd)}</td>
                        <td className="px-5 py-3 text-right">{ac.goal_count}</td>
                        <td className="px-5 py-3 text-right font-mono">{formatCost(ac.avg_cost_per_goal)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
