import { useState, useEffect, useMemo } from "react";
import type { JSX } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  DollarSign,
  AlertTriangle,
  CheckCircle2,
  TrendingUp,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Save,
  RotateCcw,
  Zap,
  AlertCircle,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Info,
} from "lucide-react";
import {
  costsApi,
  governanceApi,
  goalsApi,
  type CostAnomaly,
  type AgentCost,
} from "@/lib/api/client";
import { ThemedLineChart } from "@/components/charts";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

// ── Types ─────────────────────────────────────────────────────────────────────

type SortCol = "total_cost_usd" | "goal_count" | "avg_cost_per_goal";
type Health = "good" | "warn" | "crit";

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUsd(v: number): string {
  return `$${v.toFixed(2)}`;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function healthFromPct(pct: number): Health {
  if (pct >= 80) return "crit";
  if (pct >= 50) return "warn";
  return "good";
}

const HEALTH_BORDER: Record<Health, string> = {
  good: "border-l-green-500",
  warn: "border-l-amber-500",
  crit: "border-l-red-500",
};
const HEALTH_TEXT: Record<Health, string> = {
  good: "text-green-600",
  warn: "text-amber-600",
  crit: "text-red-600",
};
const HEALTH_BADGE: Record<Health, string> = {
  good: "bg-green-500/10 text-green-700",
  warn: "bg-amber-500/10 text-amber-700",
  crit: "bg-red-500/10 text-red-700",
};

// ── StatCard ──────────────────────────────────────────────────────────────────

interface StatCardProps {
  label: string;
  value: string;
  subtitle?: string;
  health: Health;
  loading?: boolean;
}

function StatCard({ label, value, subtitle, health, loading }: StatCardProps): JSX.Element {
  if (loading) {
    return (
      <div className="bg-card border border-border border-l-4 border-l-border rounded-xl p-4">
        <Skeleton className="h-3 w-24 mb-2" />
        <Skeleton className="h-7 w-20 mb-1" />
        <Skeleton className="h-3 w-28" />
      </div>
    );
  }
  return (
    <div className={`bg-card border border-border border-l-4 ${HEALTH_BORDER[health]} rounded-xl p-4`}>
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${HEALTH_TEXT[health]}`}>{value}</p>
      {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
    </div>
  );
}

// ── BudgetBar ─────────────────────────────────────────────────────────────────

function BudgetBar({ pct, className }: { pct: number; className?: string }): JSX.Element {
  const health = healthFromPct(pct);
  const barColor =
    health === "crit" ? "bg-red-500" : health === "warn" ? "bg-amber-500" : "bg-green-500";
  return (
    <div className={`relative h-2 rounded-full bg-muted overflow-hidden ${className ?? ""}`}>
      <div
        className={`absolute inset-y-0 left-0 rounded-full transition-all ${barColor}`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
}

// ── SortButton ────────────────────────────────────────────────────────────────

interface SortButtonProps {
  label: string;
  column: string;
  currentCol: string;
  dir: "asc" | "desc";
  onSort: (col: string) => void;
}

function SortButton({ label, column, currentCol, dir, onSort }: SortButtonProps): JSX.Element {
  const active = column === currentCol;
  return (
    <button
      onClick={() => onSort(column)}
      className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
    >
      {label}
      {active ? (
        dir === "desc" ? (
          <ArrowDown className="h-3 w-3" />
        ) : (
          <ArrowUp className="h-3 w-3" />
        )
      ) : (
        <ArrowUpDown className="h-3 w-3 opacity-40" />
      )}
    </button>
  );
}

// ── NumberInput ───────────────────────────────────────────────────────────────

interface NumberInputProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  placeholder?: string;
}

function NumberInput({ label, value, onChange, step = 1, min = 0 }: NumberInputProps): JSX.Element {
  return (
    <div>
      <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
      <div className="flex items-center border border-input rounded-lg overflow-hidden bg-background">
        <span className="px-3 py-2 text-sm text-muted-foreground bg-muted/50 border-r border-border">
          $
        </span>
        <input
          type="number"
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          min={min}
          step={step}
          className="flex-1 px-3 py-2 text-sm bg-transparent outline-none"
        />
      </div>
    </div>
  );
}

// ── BudgetManagerPage ─────────────────────────────────────────────────────────

export function BudgetManagerPage(): JSX.Element {
  const qc = useQueryClient();
  const navigate = useNavigate();

  // ── Form state ──────────────────────────────────────────────────────────────
  const [globalDailyBudget, setGlobalDailyBudget] = useState(10);
  const [perGoalBudget, setPerGoalBudget] = useState(1);
  const [alertThresholds, setAlertThresholds] = useState([50, 75, 90]);
  const [perAgentOverrides, setPerAgentOverrides] = useState<Record<string, number>>({});
  const [isDirty, setIsDirty] = useState(false);

  // ── Table sort ──────────────────────────────────────────────────────────────
  const [sortCol, setSortCol] = useState<SortCol>("total_cost_usd");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // ── Predictor ───────────────────────────────────────────────────────────────
  const [predictorOpen, setPredictorOpen] = useState(false);
  const [goalInput, setGoalInput] = useState("");

  // ── Queries ─────────────────────────────────────────────────────────────────
  const summaryQ = useQuery({ queryKey: ["cost-summary"], queryFn: costsApi.getSummary });
  const budgetsQ = useQuery({ queryKey: ["cost-budgets"], queryFn: costsApi.getBudgets });
  const govQ = useQuery({ queryKey: ["gov-budget"], queryFn: governanceApi.getBudget });
  const perAgentQ = useQuery({ queryKey: ["cost-per-agent"], queryFn: costsApi.getPerAgent });
  const anomaliesQ = useQuery({ queryKey: ["cost-anomalies"], queryFn: costsApi.getAnomalies });

  // ── Initialise form from fetched data (once) ─────────────────────────────────
  useEffect(() => {
    if (govQ.data && !isDirty) {
      setGlobalDailyBudget(govQ.data.per_tenant_daily_usd);
      setPerGoalBudget(govQ.data.per_goal_usd);
    }
  }, [govQ.data, isDirty]);

  // ── Mutations ────────────────────────────────────────────────────────────────
  const saveMutation = useMutation({
    mutationFn: async () => {
      await Promise.all([
        costsApi.updateBudgets({
          per_goal_usd: perGoalBudget,
          per_tenant_daily_usd: globalDailyBudget,
          per_agent_daily_usd: perAgentOverrides,
          alert_pct_thresholds: alertThresholds,
        }),
        governanceApi.setBudget({
          per_goal_usd: perGoalBudget,
          per_tenant_daily_usd: globalDailyBudget,
        }),
      ]);
    },
    onSuccess: () => {
      toast({ kind: "success", message: "Budget saved — both systems updated." });
      qc.invalidateQueries({ queryKey: ["cost-budgets"] });
      qc.invalidateQueries({ queryKey: ["gov-budget"] });
      setIsDirty(false);
    },
    onError: (e) => toast({ kind: "error", message: `Save failed: ${String(e)}` }),
  });

  const predictMutation = useMutation({
    mutationFn: (goal: string) => costsApi.predict(goal),
    onError: (e) => toast({ kind: "error", message: `Prediction failed: ${String(e)}` }),
  });

  const runGoalMutation = useMutation({
    mutationFn: (goal: string) => goalsApi.submit({ goal }),
    onSuccess: (data) => {
      toast({ kind: "success", message: `Goal submitted (${data.id.slice(0, 8)}…)` });
      void navigate("/goals");
    },
    onError: (e) => toast({ kind: "error", message: `Submit failed: ${String(e)}` }),
  });

  // ── Derived values ───────────────────────────────────────────────────────────
  const summary = summaryQ.data;
  const budgets = budgetsQ.data;
  const govBudget = govQ.data;
  const agentCosts: AgentCost[] = perAgentQ.data ?? [];
  const anomalies: CostAnomaly[] = anomaliesQ.data ?? [];

  const dailySpent = budgets?.daily_spent ?? 0;
  const dailyLimit =
    govBudget?.per_tenant_daily_usd ??
    budgets?.daily_limit ??
    budgets?.per_tenant_daily_usd ??
    10;
  const budgetPct = dailyLimit > 0 ? (dailySpent / dailyLimit) * 100 : 0;
  const overallHealth = healthFromPct(budgetPct);

  const totalGoals = agentCosts.reduce((s, a) => s + a.goal_count, 0);
  const totalCost = agentCosts.reduce((s, a) => s + a.total_cost_usd, 0);
  const perGoalAvg = totalGoals > 0 ? totalCost / totalGoals : 0;

  // Budget runway: monthly equivalent, using last-7-day burn rate
  const costByDay = summary?.cost_by_day ?? [];
  const last7 = costByDay.slice(-7);
  const avg7Burn = last7.length > 0 ? last7.reduce((s, d) => s + d.cost_usd, 0) / last7.length : 0;
  const periodSpent = costByDay.reduce((s, d) => s + d.cost_usd, 0);
  const totalBudget30 = dailyLimit * 30;
  const budgetRemaining = totalBudget30 - periodSpent;
  const runwayDays = avg7Burn > 0 ? Math.max(0, budgetRemaining / avg7Burn) : Infinity;
  const runwayHealth: Health = runwayDays < 2 ? "crit" : runwayDays < 7 ? "warn" : "good";

  // Chart data with compact date labels
  const chartData = useMemo(
    () =>
      costByDay.map((d) => ({
        date: d.date.slice(5), // "MM-DD"
        cost_usd: d.cost_usd,
      })) as Record<string, unknown>[],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [summary]
  );

  // Model cost breakdown
  const modelEntries = useMemo(() => {
    const entries = Object.entries(summary?.cost_by_model ?? {}).sort(([, a], [, b]) => b - a);
    const total = entries.reduce((s, [, v]) => s + v, 0);
    return entries.map(([model, cost]) => ({
      model,
      cost,
      pct: total > 0 ? (cost / total) * 100 : 0,
    }));
  }, [summary]);

  // Sorted agent table
  const sortedAgents = useMemo(
    () =>
      [...agentCosts].sort((a, b) => {
        const dir = sortDir === "asc" ? 1 : -1;
        return dir * (a[sortCol] - b[sortCol]);
      }),
    [agentCosts, sortCol, sortDir]
  );

  const handleSort = (col: string) => {
    if (col === sortCol) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col as SortCol);
      setSortDir("desc");
    }
  };

  const anyLoading = budgetsQ.isLoading || govQ.isLoading;

  // ── Full error state (both primary queries failed) ────────────────────────────
  if (budgetsQ.isError && govQ.isError) {
    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center h-48 text-muted-foreground gap-3"
      >
        <AlertCircle className="h-10 w-10 opacity-40" />
        <p className="text-sm font-medium">Failed to load budget config</p>
        <button
          onClick={() => {
            void budgetsQ.refetch();
            void govQ.refetch();
          }}
          className="text-xs text-primary hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────────
  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto space-y-6">
      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Budget Manager</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Monitor spend, configure limits, and predict goal costs across all agents.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {isDirty && (
            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              {saveMutation.isPending ? "Saving…" : "Save Changes"}
            </button>
          )}
          <button
            onClick={() => {
              void summaryQ.refetch();
              void budgetsQ.refetch();
              void govQ.refetch();
              void perAgentQ.refetch();
              void anomaliesQ.refetch();
            }}
            className="flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
            aria-label="Refresh all data"
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Section 1: Live Spend Overview ── */}
      <section aria-labelledby="spend-overview-heading">
        <h2
          id="spend-overview-heading"
          className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3"
        >
          Live Spend Overview
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            label="Daily Spend"
            value={fmtUsd(dailySpent)}
            subtitle={`of ${fmtUsd(dailyLimit)} limit`}
            health={overallHealth}
            loading={anyLoading}
          />
          <StatCard
            label="Daily Budget"
            value={fmtUsd(dailyLimit)}
            subtitle="per-tenant daily cap"
            health="good"
            loading={anyLoading}
          />
          {/* Budget Used — inline for custom progress bar */}
          <div
            className={`bg-card border border-border border-l-4 ${HEALTH_BORDER[overallHealth]} rounded-xl p-4`}
          >
            {anyLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-2 w-full rounded-full" />
                <Skeleton className="h-7 w-12" />
              </div>
            ) : (
              <>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  Budget Used
                </p>
                <BudgetBar pct={budgetPct} className="mt-3 mb-2" />
                <p className={`text-2xl font-bold ${HEALTH_TEXT[overallHealth]}`}>
                  {budgetPct.toFixed(1)}%
                </p>
              </>
            )}
          </div>
          <StatCard
            label="Per-Goal Avg"
            value={perGoalAvg > 0 ? fmtUsd(perGoalAvg) : "—"}
            subtitle={totalGoals > 0 ? `across ${totalGoals} goals` : "no goals yet"}
            health={
              govBudget?.per_goal_usd && perGoalAvg > 0
                ? healthFromPct((perGoalAvg / govBudget.per_goal_usd) * 100)
                : "good"
            }
            loading={anyLoading || perAgentQ.isLoading}
          />
        </div>
      </section>

      {/* ── Section 2: Cost Trend Chart ── */}
      <section
        className="bg-card border border-border rounded-xl p-5 space-y-4"
        aria-labelledby="cost-trend-heading"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2
              id="cost-trend-heading"
              className="font-semibold flex items-center gap-2"
            >
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
              30-Day Spend Trend
            </h2>
            {summary && (
              <p className="text-sm text-muted-foreground mt-0.5">
                Period total:{" "}
                <span className="font-medium text-foreground">{fmtUsd(periodSpent)}</span>
              </p>
            )}
          </div>
          {/* Budget runway indicator */}
          {!summaryQ.isLoading && avg7Burn > 0 && (
            <div
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium ${HEALTH_BADGE[runwayHealth]}`}
            >
              {runwayHealth === "good" ? (
                <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0" />
              ) : (
                <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
              )}
              {isFinite(runwayDays)
                ? `At current burn rate: budget runs out in ${Math.round(runwayDays)} days`
                : "Burn rate within budget"}
            </div>
          )}
        </div>

        {summaryQ.isLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : chartData.length === 0 ? (
          <EmptyState
            title="No cost data available"
            description="Cost data will appear as goals are executed."
          />
        ) : (
          <ThemedLineChart
            data={chartData}
            lines={[{ key: "cost_usd", label: "Cost (USD)" }]}
            xKey="date"
            height={200}
            formatValue={(v) => `$${v.toFixed(2)}`}
          />
        )}

        {/* Model cost breakdown */}
        {modelEntries.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
              Cost by Model
            </p>
            <div className="space-y-2">
              {modelEntries.map(({ model, cost, pct }) => (
                <div key={model} className="flex items-center gap-3">
                  <span
                    className="text-xs font-mono text-muted-foreground w-44 truncate"
                    title={model}
                  >
                    {model}
                  </span>
                  <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary rounded-full"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="text-xs font-medium text-foreground w-16 text-right">
                    {fmtUsd(cost)}
                  </span>
                  <span className="text-xs text-muted-foreground w-10 text-right">
                    {pct.toFixed(0)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* ── Section 3: Budget Configuration ── */}
      <section aria-labelledby="budget-config-heading">
        <h2
          id="budget-config-heading"
          className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3"
        >
          Budget Configuration
        </h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* Left: Global Limits */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4">
            <div className="flex items-center gap-2">
              <DollarSign className="h-4 w-4 text-muted-foreground" />
              <h3 className="font-semibold">Global Limits</h3>
            </div>
            {anyLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-14 w-full" />
                <Skeleton className="h-14 w-full" />
                <Skeleton className="h-14 w-full" />
              </div>
            ) : (
              <>
                <NumberInput
                  label="Daily Budget (USD)"
                  value={globalDailyBudget}
                  onChange={(v) => {
                    setGlobalDailyBudget(v);
                    setIsDirty(true);
                  }}
                  step={1}
                />
                <NumberInput
                  label="Per-Goal Budget (USD)"
                  value={perGoalBudget}
                  onChange={(v) => {
                    setPerGoalBudget(v);
                    setIsDirty(true);
                  }}
                  step={0.1}
                />
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-2">
                    Alert Thresholds (%)
                  </p>
                  <div className="grid grid-cols-3 gap-2">
                    {alertThresholds.map((t, i) => (
                      <div
                        key={i}
                        className="flex items-center border border-input rounded-lg overflow-hidden bg-background"
                      >
                        <input
                          type="number"
                          min={1}
                          max={100}
                          value={t}
                          onChange={(e) => {
                            const next = [...alertThresholds];
                            next[i] = Number(e.target.value);
                            setAlertThresholds([...next].sort((a, b) => a - b));
                            setIsDirty(true);
                          }}
                          className="flex-1 px-2 py-2 text-sm bg-transparent outline-none text-center"
                        />
                        <span className="px-2 py-2 text-xs text-muted-foreground bg-muted/50 border-l border-border">
                          %
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="flex items-start gap-2 text-xs text-muted-foreground bg-muted/30 rounded-lg px-3 py-2">
                  <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                  <span>
                    ⚠ Note: Global limits apply across all agents. Per-agent overrides take
                    precedence.
                  </span>
                </div>
                <button
                  onClick={() => saveMutation.mutate()}
                  disabled={saveMutation.isPending || !isDirty}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50"
                >
                  <Save className="h-4 w-4" />
                  {saveMutation.isPending ? "Saving…" : "Save Limits"}
                </button>
              </>
            )}
          </div>

          {/* Right: Per-Agent Overrides */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Per-Agent Overrides</h3>
              <span className="text-xs text-muted-foreground">{agentCosts.length} agents</span>
            </div>
            {perAgentQ.isLoading ? (
              <div className="space-y-3">
                {[1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : agentCosts.length === 0 ? (
              <EmptyState
                title="No agents found"
                description="Per-agent overrides will appear once agents have run goals."
              />
            ) : (
              <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
                {agentCosts.map((agent) => {
                  const override = perAgentOverrides[agent.agent_id];
                  const usagePct =
                    override !== undefined && override > 0
                      ? (agent.total_cost_usd / override) * 100
                      : 0;
                  return (
                    <div key={agent.agent_id} className="space-y-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium truncate">{agent.agent_name}</span>
                        <button
                          onClick={() => {
                            setPerAgentOverrides((prev) => {
                              const next = { ...prev };
                              delete next[agent.agent_id];
                              return next;
                            });
                            setIsDirty(true);
                          }}
                          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground flex-shrink-0"
                          aria-label={`Reset override for ${agent.agent_name}`}
                        >
                          <RotateCcw className="h-3 w-3" /> Reset
                        </button>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="flex items-center border border-input rounded-lg overflow-hidden bg-background flex-1">
                          <span className="px-2 py-1.5 text-xs text-muted-foreground bg-muted/50 border-r border-border">
                            $
                          </span>
                          <input
                            type="number"
                            min={0}
                            step={0.5}
                            placeholder="No limit"
                            value={override ?? ""}
                            onChange={(e) => {
                              const raw = e.target.value;
                              setPerAgentOverrides((prev) => {
                                if (raw === "") {
                                  const next = { ...prev };
                                  delete next[agent.agent_id];
                                  return next;
                                }
                                return { ...prev, [agent.agent_id]: Number(raw) };
                              });
                              setIsDirty(true);
                            }}
                            className="flex-1 px-2 py-1.5 text-sm bg-transparent outline-none"
                          />
                        </div>
                        <span className="text-xs text-muted-foreground w-24 text-right flex-shrink-0">
                          {fmtUsd(agent.total_cost_usd)} spent
                        </span>
                      </div>
                      {override !== undefined && override > 0 && (
                        <BudgetBar pct={usagePct} />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ── Section 4: Anomalies + Per-Agent Breakdown ── */}
      <section aria-labelledby="breakdown-heading">
        <h2
          id="breakdown-heading"
          className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3"
        >
          Cost Anomalies &amp; Agent Breakdown
        </h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* Left: Anomalies */}
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-border flex items-center justify-between">
              <h3 className="font-semibold text-sm">Cost Anomalies</h3>
              {!anomaliesQ.isLoading && (
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                    anomalies.length > 0
                      ? "bg-red-500/10 text-red-700"
                      : "bg-green-500/10 text-green-700"
                  }`}
                >
                  {anomalies.length}
                </span>
              )}
            </div>
            {anomaliesQ.isLoading ? (
              <div className="p-5 space-y-3">
                <Skeleton className="h-16 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            ) : anomalies.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 gap-2">
                <CheckCircle2 className="h-8 w-8 text-green-500" />
                <p className="text-sm font-medium">No anomalies detected</p>
                <p className="text-xs text-muted-foreground">
                  All costs within normal range
                </p>
              </div>
            ) : (
              <div className="divide-y divide-border">
                {anomalies.map((a) => {
                  const sev = a.severity ?? "low";
                  const sevBadge =
                    sev === "high"
                      ? "bg-red-500/10 text-red-700"
                      : sev === "medium"
                        ? "bg-amber-500/10 text-amber-700"
                        : "bg-green-500/10 text-green-700";
                  const delta =
                    a.cost_delta_usd ?? a.cost_actual_usd - a.cost_baseline_usd;
                  return (
                    <div key={a.id ?? a.anomaly_type} className="px-5 py-3 space-y-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={`text-xs font-semibold px-2 py-0.5 rounded-full uppercase ${sevBadge}`}
                        >
                          {sev}
                        </span>
                        <span className="text-sm font-medium">{a.anomaly_type}</span>
                        <span className="ml-auto text-sm font-mono text-red-600 flex-shrink-0">
                          +{fmtUsd(delta)}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground">
                        <span>{a.sigma_deviation.toFixed(1)}σ deviation</span>
                        <span>{timeAgo(a.detected_at)}</span>
                      </div>
                      {a.message && (
                        <p className="text-xs text-muted-foreground line-clamp-2">{a.message}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Right: Per-Agent Breakdown Table */}
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-border">
              <h3 className="font-semibold text-sm">Per-Agent Breakdown</h3>
            </div>
            {perAgentQ.isLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : sortedAgents.length === 0 ? (
              <EmptyState
                title="No agent cost data"
                description="Data will appear after goals are executed."
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30">
                      <th className="px-4 py-2.5 text-left">
                        <span className="text-xs font-medium text-muted-foreground">Agent</span>
                      </th>
                      <th className="px-4 py-2.5 text-right">
                        <SortButton
                          label="Cost"
                          column="total_cost_usd"
                          currentCol={sortCol}
                          dir={sortDir}
                          onSort={handleSort}
                        />
                      </th>
                      <th className="px-4 py-2.5 text-right">
                        <SortButton
                          label="Goals"
                          column="goal_count"
                          currentCol={sortCol}
                          dir={sortDir}
                          onSort={handleSort}
                        />
                      </th>
                      <th className="px-4 py-2.5 text-right">
                        <SortButton
                          label="Avg/Goal"
                          column="avg_cost_per_goal"
                          currentCol={sortCol}
                          dir={sortDir}
                          onSort={handleSort}
                        />
                      </th>
                      <th className="px-4 py-2.5 text-right">
                        <span className="text-xs font-medium text-muted-foreground">
                          Budget %
                        </span>
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {sortedAgents.map((agent) => {
                      const override = perAgentOverrides[agent.agent_id];
                      const budPct =
                        override !== undefined && override > 0
                          ? (agent.total_cost_usd / override) * 100
                          : null;
                      const budHealth = budPct !== null ? healthFromPct(budPct) : "good";
                      return (
                        <tr
                          key={agent.agent_id}
                          onClick={() => void navigate(`/agents/${agent.agent_id}`)}
                          className="hover:bg-muted/20 transition-colors cursor-pointer"
                        >
                          <td className="px-4 py-2.5 font-medium">{agent.agent_name}</td>
                          <td className="px-4 py-2.5 text-right font-mono">
                            {fmtUsd(agent.total_cost_usd)}
                          </td>
                          <td className="px-4 py-2.5 text-right">{agent.goal_count}</td>
                          <td className="px-4 py-2.5 text-right font-mono">
                            {fmtUsd(agent.avg_cost_per_goal)}
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            {budPct !== null ? (
                              <span
                                className={`text-xs font-medium px-1.5 py-0.5 rounded ${HEALTH_BADGE[budHealth]}`}
                              >
                                {budPct.toFixed(0)}%
                              </span>
                            ) : (
                              <span className="text-xs text-muted-foreground">N/A</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ── Section 5: Cost Predictor ── */}
      <section
        className="bg-card border border-border rounded-xl overflow-hidden"
        aria-labelledby="predictor-heading"
      >
        <button
          onClick={() => setPredictorOpen((o) => !o)}
          className="w-full flex items-center justify-between px-5 py-4 hover:bg-muted/30 transition-colors"
          aria-expanded={predictorOpen}
        >
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-muted-foreground" />
            <h2 id="predictor-heading" className="font-semibold text-sm">
              Cost Predictor
            </h2>
          </div>
          {predictorOpen ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </button>

        {predictorOpen && (
          <div className="border-t border-border p-5 space-y-4">
            <p className="text-sm text-muted-foreground">
              Estimate the cost of running a goal before executing it.
            </p>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                Goal Description
              </label>
              <textarea
                value={goalInput}
                onChange={(e) => setGoalInput(e.target.value)}
                placeholder="Describe the goal you want to estimate…"
                rows={3}
                className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background resize-none outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
            <button
              onClick={() => {
                if (goalInput.trim()) predictMutation.mutate(goalInput.trim());
              }}
              disabled={!goalInput.trim() || predictMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50"
            >
              <Zap className="h-4 w-4" />
              {predictMutation.isPending ? "Estimating…" : "Estimate Cost"}
            </button>

            {predictMutation.data && (
              <div className="bg-muted/30 rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold">Cost Estimate</p>
                  <span
                    className={`text-xs font-medium px-2 py-0.5 rounded-full capitalize ${
                      predictMutation.data.confidence === "high"
                        ? "bg-green-500/10 text-green-700"
                        : predictMutation.data.confidence === "medium"
                          ? "bg-amber-500/10 text-amber-700"
                          : "bg-red-500/10 text-red-700"
                    }`}
                  >
                    {predictMutation.data.confidence} confidence
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  {(["min", "mean", "max"] as const).map((k) => (
                    <div
                      key={k}
                      className="bg-card border border-border rounded-lg p-3 text-center"
                    >
                      <p className="text-xs text-muted-foreground uppercase">{k}</p>
                      <p className="text-lg font-bold mt-1">
                        {fmtUsd(predictMutation.data!.estimated_cost_usd[k])}
                      </p>
                    </div>
                  ))}
                </div>
                <button
                  onClick={() => runGoalMutation.mutate(goalInput.trim())}
                  disabled={runGoalMutation.isPending}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 border border-primary text-primary rounded-lg text-sm font-medium hover:bg-primary/5 disabled:opacity-50"
                >
                  <Zap className="h-4 w-4" />
                  {runGoalMutation.isPending ? "Submitting…" : "Run This Goal"}
                </button>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
