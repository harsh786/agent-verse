import { useState } from "react";
import type { JSX } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, XCircle, AlertCircle, Clock, FlaskConical, RotateCcw,
  TrendingUp, TrendingDown, Lightbulb, BarChart3,
} from "lucide-react";
import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip, Legend,
} from "recharts";
import { selfImprovementApi } from "@/lib/api/client";
import type { Experiment, Suggestion, BenchmarkMetrics } from "@/lib/api/client";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ThemedBarChart } from "@/components/charts";
import { CHART_COLORS, CHART_AXIS_COLOR, CHART_TOOLTIP_STYLE } from "@/components/charts";
import { toast } from "@/stores/toast";

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<Experiment["status"], string> = {
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  concluded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  pending: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
};

const SUGGESTION_STATUS_STYLES: Record<Suggestion["status"], string> = {
  pending: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  applied: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  rejected: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const days = Math.floor(diff / 86400000);
  const hours = Math.floor(diff / 3600000);
  if (days > 0) return `${days}d ago`;
  if (hours > 0) return `${hours}h ago`;
  return "recently";
}

function duration(start: string, end: string | null | undefined): string {
  if (!start) return "—";
  const endDate = end ? new Date(end) : new Date();
  const days = Math.floor((endDate.getTime() - new Date(start).getTime()) / 86400000);
  if (days === 0) return "< 1 day";
  return `${days} day${days === 1 ? "" : "s"}`;
}

// ── Experiment Detail ─────────────────────────────────────────────────────────

function ExperimentDetail({ experiment }: { experiment: Experiment }): JSX.Element {
  const controlKeys = Object.keys(experiment.control_config);
  const challengerKeys = Object.keys(experiment.challenger_config);
  const allKeys = Array.from(new Set([...controlKeys, ...challengerKeys]));

  const comparisonData = allKeys.map((key) => ({
    key,
    control:
      typeof experiment.control_config[key] === "number"
        ? (experiment.control_config[key] as number)
        : 0,
    challenger:
      typeof experiment.challenger_config[key] === "number"
        ? (experiment.challenger_config[key] as number)
        : 0,
  }));

  const durationStr = duration(experiment.started_at, experiment.concluded_at);

  return (
    <div className="bg-muted/30 rounded-xl p-4 mt-3 space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        {experiment.lift_pct !== null && (
          <div
            className={`px-3 py-1.5 rounded-lg text-sm font-bold ${
              experiment.lift_pct > 0
                ? "text-green-600 bg-green-50 dark:bg-green-900/20"
                : "text-red-600 bg-red-50 dark:bg-red-900/20"
            }`}
          >
            {experiment.lift_pct > 0 ? "+" : ""}
            {experiment.lift_pct.toFixed(1)}% lift
          </div>
        )}
        <div className="text-xs text-muted-foreground flex items-center gap-1">
          <Clock className="h-3.5 w-3.5" />
          {experiment.status === "running" ? `Running for ${durationStr}` : `Ran for ${durationStr}`}
        </div>
        <div className="text-xs text-muted-foreground">
          Started {new Date(experiment.started_at).toLocaleDateString()}
          {experiment.concluded_at &&
            ` · Concluded ${new Date(experiment.concluded_at).toLocaleDateString()}`}
        </div>
      </div>

      {comparisonData.length > 0 &&
        comparisonData.some((d) => d.control !== 0 || d.challenger !== 0) && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-2">
              Control vs Challenger (numeric fields)
            </p>
            <ThemedBarChart
              data={comparisonData}
              bars={[
                { key: "control", label: "Control", color: "#64748b" },
                { key: "challenger", label: "Challenger", color: CHART_COLORS[0] },
              ]}
              xKey="key"
              height={150}
            />
          </div>
        )}

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <p className="font-medium text-muted-foreground mb-1">Control Config</p>
          <pre className="bg-background border border-border rounded px-2 py-1.5 overflow-auto max-h-24 text-xs">
            {JSON.stringify(experiment.control_config, null, 2)}
          </pre>
        </div>
        <div>
          <p className="font-medium text-muted-foreground mb-1">Challenger Config</p>
          <pre className="bg-background border border-border rounded px-2 py-1.5 overflow-auto max-h-24 text-xs">
            {JSON.stringify(experiment.challenger_config, null, 2)}
          </pre>
        </div>
      </div>
    </div>
  );
}

// ── Benchmarks Tab ────────────────────────────────────────────────────────────

function BenchmarksTab(): JSX.Element {
  const { data: bm, isLoading, isError } = useQuery<BenchmarkMetrics>({
    queryKey: ["benchmarks"],
    queryFn: () => selfImprovementApi.getBenchmarks(30),
  });

  const evalDims = ["task_completion", "efficiency", "accuracy", "safety", "coherence"];
  const radarData = evalDims.map((dim) => ({
    dim: dim.replace("_", "\n"),
    yours: bm?.dimensions?.your?.[dim] ?? 0,
    platform: bm?.dimensions?.platform?.[dim] ?? 0,
  }));

  function pct(v: number) { return `${(v * 100).toFixed(1)}%`; }
  function usd(v: number) { return v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`; }

  if (isLoading) return <div className="flex justify-center p-12"><LoadingSpinner /></div>;
  if (isError || !bm) {
    return (
      <div className="flex flex-col items-center p-12 text-muted-foreground">
        <AlertCircle className="h-8 w-8 mb-2 opacity-40" />
        <p className="text-sm">Benchmark data unavailable</p>
      </div>
    );
  }

  const compBars = [
    { metric: "Success Rate", yours: bm.your_success_rate, platform: bm.platform_avg_success_rate, fmt: pct },
    { metric: "Eval Score", yours: bm.your_eval_score, platform: bm.platform_avg_eval_score, fmt: (v: number) => v.toFixed(2) },
    { metric: "Avg Cost", yours: bm.your_cost_usd, platform: bm.platform_avg_cost_usd, fmt: usd },
  ];

  const badgeClass =
    bm.percentile_success <= 10
      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
      : bm.percentile_success <= 25
      ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
      : bm.percentile_success <= 50
      ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
      : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <BarChart3 className="h-5 w-5 text-primary" />
        <div>
          <h2 className="font-semibold">Your Performance vs Platform</h2>
          <p className="text-xs text-muted-foreground">
            Anonymized comparison across all AgentVerse tenants (last 30 days)
          </p>
        </div>
        <span className={`ml-auto px-3 py-1 rounded-full text-sm font-semibold ${badgeClass}`}>
          {bm.comparison_label}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {compBars.map(({ metric, yours, platform, fmt }) => {
          const better = metric === "Avg Cost" ? yours <= platform : yours >= platform;
          return (
            <div key={metric} className="bg-muted/30 rounded-xl p-4 space-y-3">
              <p className="text-xs font-medium text-muted-foreground">{metric}</p>
              <div className="flex items-end gap-3">
                <div className="text-2xl font-bold tabular-nums">{fmt(yours)}</div>
                <div className={`text-xs ${better ? "text-emerald-500" : "text-red-400"} flex items-center gap-0.5`}>
                  {better ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                  vs {fmt(platform)} avg
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden relative">
                  <div
                    className="absolute inset-y-0 left-0 bg-muted-foreground/25 rounded-full"
                    style={{ width: `${Math.min((platform / Math.max(yours, platform, 0.001)) * 100, 100)}%` }}
                  />
                  <div
                    className={`absolute inset-y-0 left-0 rounded-full ${better ? "bg-emerald-500" : "bg-blue-500"}`}
                    style={{ width: `${Math.min((yours / Math.max(yours, platform, 0.001)) * 100, 100)}%` }}
                  />
                </div>
                <span className="text-xs text-muted-foreground">pct {metric === "Avg Cost" ? bm.percentile_cost : bm.percentile_success}</span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="bg-card border border-border rounded-xl p-5">
        <h3 className="text-xs font-medium text-muted-foreground mb-4 text-center">
          Eval Dimensions: You vs Platform Average
        </h3>
        {radarData.some((d) => d.yours > 0 || d.platform > 0) ? (
          <ResponsiveContainer width="100%" height={280}>
            <RadarChart data={radarData} margin={{ top: 10, right: 30, bottom: 10, left: 30 }}>
              <PolarGrid stroke={CHART_AXIS_COLOR} opacity={0.3} />
              <PolarAngleAxis dataKey="dim" tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }} />
              <PolarRadiusAxis domain={[0, 1]} tick={{ fill: CHART_AXIS_COLOR, fontSize: 9 }} tickCount={4} />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v: number) => [v.toFixed(2)]} />
              <Legend wrapperStyle={{ fontSize: 11, color: CHART_AXIS_COLOR }} />
              <Radar name="You" dataKey="yours" stroke={CHART_COLORS[0]} fill={CHART_COLORS[0]} fillOpacity={0.3} strokeWidth={2} />
              <Radar name="Platform Avg" dataKey="platform" stroke={CHART_COLORS[5]} fill={CHART_COLORS[5]} fillOpacity={0.1} strokeWidth={1.5} strokeDasharray="4 2" />
            </RadarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
            No eval dimension data available
          </div>
        )}
      </div>
    </div>
  );
}

// ── SelfImprovementPage ───────────────────────────────────────────────────────

type PageTab = "experiments" | "suggestions" | "benchmarks" | "history";

export function SelfImprovementPage(): JSX.Element {
  const qc = useQueryClient();
  const [tab, setTab] = useState<PageTab>("experiments");
  const [expandedExp, setExpandedExp] = useState<string | null>(null);
  const [expFilter, setExpFilter] = useState<"all" | "running" | "concluded" | "pending">("all");
  const [sugFilter, setSugFilter] = useState<"all" | "pending" | "applied" | "rejected">("all");

  const experimentsQuery = useQuery({
    queryKey: ["experiments"],
    queryFn: () => selfImprovementApi.listExperiments(),
    enabled: tab === "experiments" || tab === "history",
  });

  const suggestionsQuery = useQuery({
    queryKey: ["suggestions"],
    queryFn: () => selfImprovementApi.getSuggestions(),
    enabled: tab === "suggestions",
  });

  const applyMutation = useMutation({
    mutationFn: (id: string) => selfImprovementApi.applySuggestion(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Suggestion applied" });
      qc.invalidateQueries({ queryKey: ["suggestions"] });
    },
    onError: (e) => toast({ kind: "error", message: `Failed: apply suggestion. ${String(e)}` }),
  });

  const rejectMutation = useMutation({
    mutationFn: (id: string) => selfImprovementApi.rejectSuggestion(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Suggestion rejected" });
      qc.invalidateQueries({ queryKey: ["suggestions"] });
    },
    onError: (e) => toast({ kind: "error", message: `Failed: reject suggestion. ${String(e)}` }),
  });

  const rollbackMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      selfImprovementApi.rollbackExperiment(id, reason),
    onSuccess: () => {
      toast({ kind: "success", message: "Rolled back — agent restored to control configuration" });
      qc.invalidateQueries({ queryKey: ["experiments"] });
    },
    onError: (e) => toast({ kind: "error", message: `Rollback failed: ${String(e)}` }),
  });

  const allExperiments: Experiment[] = experimentsQuery.data ?? [];
  const allSuggestions: Suggestion[] = suggestionsQuery.data ?? [];

  const experiments =
    expFilter === "all" ? allExperiments : allExperiments.filter((e) => e.status === expFilter);
  const suggestions =
    sugFilter === "all" ? allSuggestions : allSuggestions.filter((s) => s.status === sugFilter);
  const history = allExperiments.filter((e) => e.status === "concluded");
  const pendingCount = allSuggestions.filter((s) => s.status === "pending").length;

  const runningCount = allExperiments.filter((e) => e.status === "running").length;
  const concludedCount = allExperiments.filter((e) => e.status === "concluded").length;

  const tabs: { id: PageTab; label: string; badge?: number }[] = [
    { id: "experiments", label: "experiments" },
    { id: "suggestions", label: "suggestions", badge: pendingCount },
    { id: "benchmarks", label: "benchmarks" },
    { id: "history", label: "history" },
  ];

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Self-Improvement</h1>
        <p className="text-sm text-muted-foreground mt-1">
          A/B experiments, AI-generated optimizations, and performance benchmarks.
        </p>
      </div>

      <div className="flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors capitalize ${
              tab === t.id
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
            {t.badge != null && t.badge > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 text-xs rounded-full bg-orange-500 text-white">
                {t.badge}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── Experiments tab ── */}
      {tab === "experiments" && (
        <div className="space-y-4">
          {allExperiments.length > 0 && (
            <div className="flex items-center gap-4 flex-wrap text-sm">
              <span className="flex items-center gap-1.5 text-blue-600 dark:text-blue-400">
                <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                {runningCount} running
              </span>
              <span className="text-muted-foreground">{concludedCount} concluded</span>
              <div className="ml-auto flex gap-1">
                {(["all", "running", "concluded", "pending"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setExpFilter(f)}
                    className={`px-2.5 py-1 text-xs rounded-full transition-colors capitalize ${
                      expFilter === f
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-muted-foreground hover:bg-muted/80"
                    }`}
                  >
                    {f}
                  </button>
                ))}
              </div>
            </div>
          )}

          {experimentsQuery.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : experimentsQuery.isError ? (
            <div role="alert" className="flex flex-col items-center justify-center h-32 text-muted-foreground">
              <AlertCircle className="h-8 w-8 opacity-40 mb-2" />
              <p className="text-sm">Failed to load experiments</p>
              <button onClick={() => void experimentsQuery.refetch()} className="mt-2 text-xs text-primary hover:underline">
                Retry
              </button>
            </div>
          ) : experiments.length === 0 ? (
            <EmptyState
              title="No experiments yet"
              description="A/B experiments are created automatically as the system detects optimization opportunities."
            />
          ) : (
            experiments.map((exp) => (
              <div key={exp.id} className="bg-card border border-border rounded-xl">
                <button
                  onClick={() => setExpandedExp(expandedExp === exp.id ? null : exp.id)}
                  className="w-full text-left px-5 py-4 hover:bg-muted/30 transition-colors rounded-xl"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="space-y-1 flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-sm truncate">{exp.name}</span>
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ${STATUS_STYLES[exp.status]}`}>
                          {exp.status === "running" ? (
                            <span className="flex items-center gap-1">
                              <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
                              running
                            </span>
                          ) : exp.status}
                        </span>
                        {exp.lift_pct !== null && (
                          <span className={`text-xs font-bold shrink-0 ${exp.lift_pct > 0 ? "text-green-600" : "text-red-600"}`}>
                            {exp.lift_pct > 0 ? "+" : ""}{exp.lift_pct.toFixed(1)}%
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Agent: {exp.agent_id.slice(0, 12)}… · {exp.status === "running" ? `Running ${duration(exp.started_at, null)}` : `Concluded ${relativeTime(exp.concluded_at)}`}
                      </p>
                    </div>
                    <FlaskConical className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  </div>
                </button>
                {expandedExp === exp.id && (
                  <div className="px-5 pb-5"><ExperimentDetail experiment={exp} /></div>
                )}
                {expandedExp === exp.id && exp.status === "concluded" && (
                  <div className="px-5 pb-4 border-t border-border pt-4 flex items-center justify-between">
                    <p className="text-xs text-muted-foreground">
                      {exp.lift_pct !== null && exp.lift_pct > 0
                        ? `Challenger improved by +${exp.lift_pct.toFixed(1)}% — rollback to restore control`
                        : "Rollback to restore the original agent configuration"}
                    </p>
                    <button
                      type="button"
                      onClick={() => rollbackMutation.mutate({ id: exp.id, reason: `Rolled back from UI. Lift: ${exp.lift_pct?.toFixed(1) ?? "N/A"}%.` })}
                      disabled={rollbackMutation.isPending}
                      aria-label={`Roll back experiment ${exp.name}`}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-orange-300 bg-orange-50 text-orange-700 hover:bg-orange-100 dark:border-orange-700/60 dark:bg-orange-950/30 dark:text-orange-300 disabled:opacity-50 transition-colors"
                    >
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                      {rollbackMutation.isPending ? "Rolling back…" : "Rollback"}
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Suggestions tab ── */}
      {tab === "suggestions" && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 flex-wrap">
            <Lightbulb className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm text-muted-foreground">
              {allSuggestions.length} suggestion{allSuggestions.length !== 1 ? "s" : ""}
            </span>
            <div className="ml-auto flex gap-1">
              {(["all", "pending", "applied", "rejected"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setSugFilter(f)}
                  className={`px-2.5 py-1 text-xs rounded-full transition-colors capitalize ${
                    sugFilter === f ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/80"
                  }`}
                >
                  {f}{f === "pending" && pendingCount > 0 && ` (${pendingCount})`}
                </button>
              ))}
            </div>
          </div>

          {suggestionsQuery.isLoading ? (
            <LoadingSpinner />
          ) : suggestions.length === 0 ? (
            <EmptyState
              title="No suggestions"
              description="The optimizer will generate suggestions as it analyzes agent performance."
            />
          ) : (
            suggestions.map((s) => (
              <div key={s.id} className="bg-card border border-border rounded-xl px-5 py-4 flex items-start justify-between gap-4">
                <div className="space-y-2 min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{s.type.replace(/_/g, " ").toUpperCase()}</span>
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${SUGGESTION_STATUS_STYLES[s.status]}`}>
                      {s.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 max-w-[120px] h-1.5 bg-muted rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${s.confidence >= 0.8 ? "bg-emerald-500" : s.confidence >= 0.6 ? "bg-yellow-500" : "bg-red-500"}`}
                        style={{ width: `${s.confidence * 100}%` }}
                      />
                    </div>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {(s.confidence * 100).toFixed(0)}% confidence
                    </span>
                  </div>
                  <p className="text-sm text-muted-foreground">{s.description}</p>
                  {s.agent_id && (
                    <p className="text-xs text-muted-foreground">
                      Agent: <span className="font-mono">{s.agent_id.slice(0, 12)}…</span>
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground">{relativeTime(s.created_at)}</p>
                </div>
                {s.status === "pending" && (
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={() => applyMutation.mutate(s.id)}
                      disabled={applyMutation.isPending}
                      className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white rounded-md text-xs hover:bg-green-700 disabled:opacity-50"
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" /> Apply
                    </button>
                    <button
                      onClick={() => rejectMutation.mutate(s.id)}
                      disabled={rejectMutation.isPending}
                      className="flex items-center gap-1 px-3 py-1.5 border border-border rounded-md text-xs hover:bg-muted disabled:opacity-50"
                    >
                      <XCircle className="h-3.5 w-3.5" /> Reject
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Benchmarks tab ── */}
      {tab === "benchmarks" && <BenchmarksTab />}

      {/* ── History tab ── */}
      {tab === "history" && (
        <div className="space-y-4">
          <h2 className="text-sm font-semibold">Optimization Timeline</h2>
          {experimentsQuery.isLoading ? (
            <LoadingSpinner />
          ) : history.length === 0 ? (
            <EmptyState
              title="No optimization history"
              description="Concluded experiments and applied optimizations will appear here."
            />
          ) : (
            <div className="relative pl-6 border-l border-border space-y-4">
              {history
                .slice()
                .sort((a, b) => new Date(b.concluded_at ?? b.started_at).getTime() - new Date(a.concluded_at ?? a.started_at).getTime())
                .map((exp) => (
                <div key={exp.id} className="relative">
                  <div className="absolute -left-8 top-1 w-4 h-4 rounded-full border-2 border-primary bg-background flex items-center justify-center">
                    <div className="w-2 h-2 rounded-full bg-primary" />
                  </div>
                  <div className="bg-card border border-border rounded-lg px-4 py-3 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium">{exp.name}</span>
                      {exp.lift_pct !== null && (
                        <span className={`text-xs font-bold ${exp.lift_pct > 0 ? "text-green-600" : "text-red-600"}`}>
                          {exp.lift_pct > 0 ? "+" : ""}{exp.lift_pct.toFixed(1)}%
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {exp.concluded_at ? new Date(exp.concluded_at).toLocaleDateString() : "—"}
                      {" · "}Agent: {exp.agent_id.slice(0, 12)}…
                      {" · "}Ran {duration(exp.started_at, exp.concluded_at)}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
