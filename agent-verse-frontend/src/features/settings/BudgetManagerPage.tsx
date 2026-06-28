import { useState, useEffect } from "react";
import type { JSX, CSSProperties } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { DollarSign, AlertCircle, Save, TrendingUp } from "lucide-react";
import { costsApi, agentsApi } from "@/lib/api/client";
import type { BudgetConfig, AgentCost } from "@/lib/api/client";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";

// ── Budget Gauge ──────────────────────────────────────────────────────────────

function BudgetGauge({ used, total, label }: { used: number; total: number; label: string }): JSX.Element {
  const pct = total > 0 ? Math.min((used / total) * 100, 100) : 0;
  const radius = 45;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference - (pct / 100) * circumference;
  const color = pct > 80 ? "#ef4444" : pct > 50 ? "#f59e0b" : "#22c55e";

  return (
    <div className="flex flex-col items-center gap-2">
      <svg width="100" height="100" viewBox="0 0 100 100" aria-label={`${label}: ${pct.toFixed(0)}% used`}>
        <circle cx="50" cy="50" r={radius} fill="none" stroke="hsl(214.3 31.8% 91.4%)" strokeWidth="8" />
        <circle
          cx="50"
          cy="50"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          style={{ "--used": `${dashOffset}` } as CSSProperties}
          className="budget-gauge-arc"
          transform="rotate(-90 50 50)"
        />
        <text x="50" y="48" textAnchor="middle" fontSize="13" fontWeight="bold" fill="currentColor">
          {pct.toFixed(0)}%
        </text>
        <text x="50" y="62" textAnchor="middle" fontSize="8" fill="hsl(215.4 16.3% 46.9%)">
          used
        </text>
      </svg>
      <p className="text-xs font-medium text-center">{label}</p>
      <p className="text-xs text-muted-foreground">
        ${used.toFixed(2)} / ${total.toFixed(2)}
      </p>
    </div>
  );
}

// ── Inline Edit Field ─────────────────────────────────────────────────────────

function InlineNumberField({
  label,
  value,
  onChange,
  prefix = "$",
  step = 0.1,
  min = 0,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  prefix?: string;
  step?: number;
  min?: number;
}): JSX.Element {
  return (
    <div>
      <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
      <div className="flex items-center border border-input rounded-lg overflow-hidden bg-background">
        <span className="px-3 py-2 text-sm text-muted-foreground bg-muted/50 border-r border-border">
          {prefix}
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
  const [draft, setDraft] = useState<Partial<BudgetConfig> | null>(null);
  const [isDirty, setIsDirty] = useState(false);

  const budgetQuery = useQuery({
    queryKey: ["cost-budgets"],
    queryFn: () => costsApi.getBudgets(),
  });

  // Initialise draft from fetched data (once)
  useEffect(() => {
    if (budgetQuery.data && !draft) setDraft(budgetQuery.data);
  }, [budgetQuery.data, draft]);

  const perAgentQuery = useQuery({
    queryKey: ["cost-per-agent"],
    queryFn: () => costsApi.getPerAgent(),
  });

  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: () => agentsApi.list(),
  });

  const saveMutation = useMutation({
    mutationFn: () => costsApi.updateBudgets(draft!),
    onSuccess: () => {
      toast({ kind: "success", message: "Budget updated" });
      qc.invalidateQueries({ queryKey: ["cost-budgets"] });
      setIsDirty(false);
    },
    onError: (e) => toast({ kind: "error", message: `Failed: update budget. ${String(e)}` }),
  });

  const config = draft ?? budgetQuery.data;

  const updateField = <K extends keyof BudgetConfig>(field: K, value: BudgetConfig[K]): void => {
    setDraft((prev) => ({ ...(prev ?? budgetQuery.data ?? {}), [field]: value }));
    setIsDirty(true);
  };

  const agentCosts: AgentCost[] = perAgentQuery.data ?? [];
  const agents = agentsQuery.data ?? [];

  if (budgetQuery.isLoading) return <LoadingSpinner />;

  if (budgetQuery.isError) {
    return (
      <div role="alert" className="flex flex-col items-center justify-center h-32 text-muted-foreground">
        <AlertCircle className="h-8 w-8 opacity-40 mb-2" />
        <p className="text-sm">Failed to load budget config</p>
        <button onClick={() => void budgetQuery.refetch()} className="mt-2 text-xs text-primary hover:underline">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Budget Manager</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Configure cost limits and alert thresholds across the platform.
          </p>
        </div>
        {isDirty && (
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
          >
            <Save className="h-4 w-4" />
            {saveMutation.isPending ? "Saving…" : "Save Changes"}
          </button>
        )}
      </div>

      {/* Budget hierarchy */}
      <div className="bg-card border border-border rounded-xl p-5 space-y-5">
        <h2 className="font-semibold flex items-center gap-2">
          <DollarSign className="h-4 w-4" /> Global Budget Limits
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {config && (
            <>
              <InlineNumberField
                label="Daily Budget (USD)"
                value={config.daily_budget_usd ?? 10}
                onChange={(v) => updateField("daily_budget_usd", v)}
              />
              <InlineNumberField
                label="Per-Goal Budget (USD)"
                value={config.per_goal_budget_usd ?? 1}
                onChange={(v) => updateField("per_goal_budget_usd", v)}
              />
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">
                  Alert Threshold: {config.alert_threshold_pct ?? 80}%
                </label>
                <input
                  type="range"
                  min={50}
                  max={95}
                  step={5}
                  value={config.alert_threshold_pct ?? 80}
                  onChange={(e) => updateField("alert_threshold_pct", Number(e.target.value))}
                  className="w-full accent-primary"
                />
                <div className="flex justify-between text-xs text-muted-foreground mt-0.5">
                  <span>50%</span>
                  <span>Alert at {config.alert_threshold_pct ?? 80}%</span>
                  <span>95%</span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Budget utilization gauges */}
      {config && (
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold mb-4 flex items-center gap-2">
            <TrendingUp className="h-4 w-4" /> Budget Utilization
          </h2>
          <div className="flex flex-wrap gap-8 justify-center md:justify-start">
            <BudgetGauge
              used={agentCosts.reduce((sum, a) => sum + a.total_cost_usd, 0)}
              total={config.daily_budget_usd ?? 10}
              label="Daily Budget"
            />
            {agentCosts.slice(0, 4).map((ac) => (
              <BudgetGauge
                key={ac.agent_id}
                used={ac.total_cost_usd}
                total={(config.per_agent_budgets ?? {})[ac.agent_id] ?? (config.per_goal_budget_usd ?? 1) * 10}
                label={ac.agent_name.slice(0, 12)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Per-agent budget overrides */}
      {agents.length > 0 && config && (
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold mb-4">Per-Agent Limits</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {agents.slice(0, 8).map((agent) => (
              <InlineNumberField
                key={agent.agent_id}
                label={agent.name}
                value={(config.per_agent_budgets ?? {})[agent.agent_id] ?? 0}
                onChange={(v) =>
                  updateField("per_agent_budgets", {
                    ...(config.per_agent_budgets ?? {}),
                    [agent.agent_id]: v,
                  })
                }
              />
            ))}
          </div>
        </div>
      )}

      {/* Per-agent cost breakdown table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border">
          <h2 className="font-semibold text-sm">Cost Breakdown by Agent</h2>
        </div>
        {perAgentQuery.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : agentCosts.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted-foreground">No cost data available.</p>
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
                {agentCosts.map((ac) => (
                  <tr key={ac.agent_id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-5 py-3 font-medium">{ac.agent_name}</td>
                    <td className="px-5 py-3 text-right font-mono">${ac.total_cost_usd.toFixed(4)}</td>
                    <td className="px-5 py-3 text-right">{ac.goal_count}</td>
                    <td className="px-5 py-3 text-right font-mono">${ac.avg_cost_per_goal.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
