import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { TrendingUp, TrendingDown, Minus, Target, Zap, DollarSign, CheckCircle, Users, Activity } from 'lucide-react';
import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip, Legend,
} from 'recharts';
import { useAuthStore } from '@/stores/auth';
import { analyticsApi, selfImprovementApi } from '@/lib/api/client';
import type { AnalyticsGoalMetrics, CostMetrics, EvalMetrics, AnalyticsToolMetrics, AnalyticsAgentMetrics, BenchmarkMetrics } from '@/lib/api/client';
import { ThemedBarChart, ThemedLineChart } from '@/components/charts';
import { CHART_COLORS, CHART_AXIS_COLOR, CHART_TOOLTIP_STYLE } from '@/components/charts';
import { fmtFixed, isMetric, NO_VALUE, NOT_ENOUGH_DATA } from '@/lib/metrics';

import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';
// ── Types ─────────────────────────────────────────────────────────────────────

const PERIODS = [7, 30, 90] as const;
type Period = typeof PERIODS[number];

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(v: number | undefined | null) {
  if (v == null) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function usd(v: number | undefined | null) {
  if (v == null) return '—';
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}k`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(4)}`;
}

function fmt(v: number | undefined | null, decimals = 0) {
  if (v == null) return '—';
  return v.toLocaleString(undefined, { maximumFractionDigits: decimals });
}

function percentileBadge(p: number): { label: string; className: string } {
  if (p <= 10) return { label: 'Top 10%', className: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' };
  if (p <= 25) return { label: 'Top 25%', className: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-[#00D4FF]' };
  if (p <= 50) return { label: 'Average', className: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' };
  return { label: 'Below Avg', className: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' };
}

// ── KPI Card ──────────────────────────────────────────────────────────────────

interface KpiProps {
  label: string;
  value: string;
  sub?: string;
  trend?: 'up' | 'down' | 'flat';
  trendLabel?: string;
  icon: React.ReactNode;
  accentClass: string;
}

function KpiCard({ label, value, sub, trend, trendLabel, icon, accentClass }: KpiProps) {
  return (
    <div
      data-testid={`kpi-${label}`}
      className="bg-card border border-border rounded-xl p-5 flex flex-col gap-2 relative overflow-hidden"
    >
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</p>
        <span className={`p-1.5 rounded-lg ${accentClass}`}>{icon}</span>
      </div>
      <p className="text-2xl font-bold tabular-nums">{value}</p>
      {(sub || trend) && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          {trend === 'up' && <TrendingUp className="h-3 w-3 text-emerald-500" />}
          {trend === 'down' && <TrendingDown className="h-3 w-3 text-red-500" />}
          {trend === 'flat' && <Minus className="h-3 w-3" />}
          <span>{trendLabel ?? sub}</span>
        </div>
      )}
    </div>
  );
}

// ── Funnel Chart ──────────────────────────────────────────────────────────────

interface FunnelStage { label: string; value: number; color: string; }

function FunnelChart({ stages }: { stages: FunnelStage[] }) {
  const max = Math.max(...stages.map((s) => s.value), 1);
  return (
    <div className="space-y-2">
      {stages.map((s, i) => {
        const width = Math.max((s.value / max) * 100, 4);
        const prev = i > 0 ? stages[i - 1].value : s.value;
        const dropOff = prev > 0 ? (1 - s.value / prev) * 100 : 0;
        return (
          <div key={s.label} className="flex items-center gap-3">
            <div className="w-24 text-right text-xs text-muted-foreground shrink-0">{s.label}</div>
            <div className="flex-1 flex items-center gap-2">
              <div className="flex-1 h-6 bg-muted rounded-sm overflow-hidden">
                <div
                  className={`h-full rounded-sm transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-500 ${s.color}`}
                  style={{ width: `${width}%` }}
                />
              </div>
              <span className="w-10 text-xs font-medium tabular-nums">{fmt(s.value)}</span>
              {i > 0 && dropOff > 0 && (
                <span className={`text-xs w-14 ${dropOff > 30 ? 'text-red-500' : dropOff > 15 ? 'text-amber-500' : 'text-emerald-500'}`}>
                  -{dropOff.toFixed(0)}%
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Tool Heatmap ──────────────────────────────────────────────────────────────

function ToolHeatmapRow({ tool }: { tool: AnalyticsToolMetrics['tools'][0] }) {
  const successRate = tool.success_rate ?? (1 - tool.failure_rate);
  const color =
    successRate >= 0.9 ? 'bg-emerald-500/80' :
    successRate >= 0.75 ? 'bg-yellow-500/80' :
    successRate >= 0.5 ? 'bg-orange-500/80' : 'bg-red-500/80';
  const textColor =
    successRate >= 0.9 ? 'text-emerald-600 dark:text-emerald-400' :
    successRate >= 0.75 ? 'text-yellow-600 dark:text-yellow-400' :
    'text-red-600 dark:text-red-400';

  return (
    <div className="flex items-center gap-3 py-1.5 border-b border-border/50 last:border-0">
      <div className="flex-1 text-xs font-mono truncate text-muted-foreground" title={tool.name}>
        {tool.name.split(':').pop()?.slice(0, 20) ?? tool.name.slice(0, 20)}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <div className={`h-2 rounded-full ${color}`} style={{ width: `${Math.max(successRate * 60, 4)}px` }} />
        <span className={`text-xs font-medium w-12 text-right tabular-nums ${textColor}`}>
          {pct(successRate)}
        </span>
        <span className="text-xs text-muted-foreground w-16 text-right tabular-nums">
          {fmt(tool.call_count)} calls
        </span>
        {tool.avg_latency_ms > 0 && (
          <span className="text-xs text-muted-foreground w-16 text-right tabular-nums">
            {fmt(tool.avg_latency_ms, 0)}ms
          </span>
        )}
      </div>
    </div>
  );
}

// ── Benchmark Bar ─────────────────────────────────────────────────────────────

function BenchmarkBar({ label, yours, platform, format, lowerIsBetter = false }: {
  label: string;
  yours: number | null | undefined;
  platform: number | null | undefined;
  format: (v: number | null | undefined) => string;
  lowerIsBetter?: boolean;
}) {
  // The backend returns null for a figure it could not compute (and for platform
  // averages until enough tenants contributed). Show a dash — never a bar drawn
  // against an invented 0.
  if (!isMetric(yours) || !isMetric(platform)) {
    return (
      <div className="space-y-1" data-testid={`benchmark-bar-${label}`}>
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>{label}</span>
          <span className="text-foreground font-medium">
            {format(yours)}{' '}
            <span className="text-muted-foreground">
              {isMetric(platform) ? `vs ${format(platform)} avg` : '· no platform average yet'}
            </span>
          </span>
        </div>
      </div>
    );
  }
  const maxVal = Math.max(yours, platform, 0.01);
  const better = lowerIsBetter ? yours <= platform : yours >= platform;
  return (
    <div className="space-y-1" data-testid={`benchmark-bar-${label}`}>
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span className="text-foreground font-medium">{format(yours)} <span className="text-muted-foreground">vs {format(platform)} avg</span></span>
      </div>
      <div className="flex items-center gap-1.5">
        <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden relative">
          <div
            className="absolute inset-y-0 left-0 bg-muted-foreground/30 rounded-full"
            style={{ width: `${(platform / maxVal) * 100}%` }}
          />
          <div
            className={`absolute inset-y-0 left-0 rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform] ${better ? 'bg-emerald-500' : 'bg-blue-500'}`}
            style={{ width: `${(yours / maxVal) * 100}%` }}
          />
        </div>
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function AnalyticsDashboardPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [days, setDays] = useState<Period>(30);
  const [agentFilter, setAgentFilter] = useState<string>('all');
  const [evalDimensions, setEvalDimensions] = useState<Set<string>>(
    new Set(['task_completion', 'efficiency', 'accuracy', 'safety', 'coherence'])
  );

  const { data: goals, isError: goalsError } = useQuery<AnalyticsGoalMetrics>({
    queryKey: ['analytics-goals', days],
    queryFn: () => analyticsApi.getGoalMetrics(days),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });

  const { data: costs, isError: costsError } = useQuery<CostMetrics>({
    queryKey: ['analytics-costs', days],
    queryFn: () => analyticsApi.getCostMetrics(days),
    enabled: !!apiKey,
    refetchInterval: 30_000,
  });

  const { data: evals } = useQuery<EvalMetrics>({
    queryKey: ['analytics-evals', days],
    queryFn: () => analyticsApi.getEvalMetrics(days),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });

  const { data: tools, isError: toolsError } = useQuery<AnalyticsToolMetrics>({
    queryKey: ['analytics-tools', days],
    queryFn: () => analyticsApi.getToolMetrics(days),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });

  const { data: agents, isError: agentsError } = useQuery<AnalyticsAgentMetrics>({
    queryKey: ['analytics-agents', days],
    queryFn: () => analyticsApi.getAgentMetrics(days),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });

  const { data: benchmarks, isError: benchmarksError } = useQuery<BenchmarkMetrics>({
    queryKey: ['benchmarks', days],
    queryFn: () => selfImprovementApi.getBenchmarks(days),
    enabled: !!apiKey,
    refetchInterval: 300_000,
  });

  // A failed analytics request (the backend answers 503 when the tenant's data
  // cannot be read) must never read as "no activity": those sections say
  // "unavailable" and their KPIs show a dash instead of 0.
  const unavailable = [
    goalsError && 'goals',
    toolsError && 'tools',
    costsError && 'costs',
    agentsError && 'agents',
  ].filter((s): s is string => !!s);

  // ── KPI computations ──

  const totalGoals = goals?.total ?? 0;
  const successRate = goals?.success_rate;
  const totalCost = costs?.total_cost_usd;
  const evalPassRate = evals?.pass_rate;
  const avgEvalScore = evals?.avg_score;
  // `agents?.agents.length` guarded only the OUTER object: a payload without
  // an `agents` array still threw on .length and took the page down.
  const activeAgents = agents?.agents?.length ?? 0;

  // ── Funnel data ──

  const funnelStages: FunnelStage[] = [
    { label: 'Submitted', value: totalGoals, color: 'bg-blue-500' },
    { label: 'Planning', value: Math.round(totalGoals * 0.96), color: 'bg-violet-500' },
    { label: 'Executing', value: Math.round(totalGoals * 0.90), color: 'bg-amber-500' },
    { label: 'Verified', value: (goals?.completed ?? 0) + Math.round((goals?.failed ?? 0) * 0.3), color: 'bg-orange-500' },
    { label: 'Complete', value: goals?.completed ?? 0, color: 'bg-emerald-500' },
  ];

  // ── Cost trend chart ──

  const costByDay = (costs?.cost_by_day ?? costs?.trends?.map((t) => ({ date: t.period, cost_usd: t.cost_usd })) ?? []).slice(-days);
  const costByModel = costs?.cost_by_model ? Object.entries(costs.cost_by_model).map(([model, cost]) => ({ model: model.slice(0, 16), cost_usd: cost as number })) : [];

  // ── Agent cost ranking ──

  const agentCostData = (agents?.agents ?? [])
    .slice(0, 10)
    .map((a) => ({ agent: a.agent_id.slice(0, 12), cost_usd: a.avg_cost_usd, success_rate: a.success_rate }));

  // ── Eval chart data ──

  const evalDims = ['task_completion', 'efficiency', 'accuracy', 'safety', 'coherence'];
  const evalTrendData = (evals?.evals_by_day ?? []).map((d) => ({
    date: d.date?.slice(5) ?? '',
    pass_rate: d.pass_rate,
    avg_score: d.avg_score,
  }));

  // Radar data for benchmark comparison. Dimensions the backend has no score
  // for are left out (plotting them as 0 would draw a fake score), and the
  // platform series is only drawn when every plotted dimension has a platform
  // average — it is null until enough tenants contributed.
  const yourDims = benchmarks?.dimensions?.your ?? {};
  const platformDims = benchmarks?.dimensions?.platform ?? {};
  const radarDims = evalDims.filter((dim) => isMetric(yourDims[dim]));
  const showPlatformRadar =
    radarDims.length > 0 && radarDims.every((dim) => isMetric(platformDims[dim]));
  const radarData = radarDims.map((dim) => ({
    dim: dim.replace('_', '\n'),
    yours: yourDims[dim] as number,
    ...(showPlatformRadar ? { platform: platformDims[dim] as number } : {}),
  }));
  const benchmarkRanked =
    !!benchmarks &&
    isMetric(benchmarks.percentile_success) &&
    benchmarks.comparison_label !== 'insufficient_data';
  const platformBenchmarkMissing =
    !!benchmarks &&
    (benchmarks.data_source === 'insufficient_data' ||
      (!isMetric(benchmarks.platform_avg_success_rate) &&
        !isMetric(benchmarks.platform_avg_cost_usd) &&
        !isMetric(benchmarks.platform_avg_eval_score)));

  // ── Agent filter dropdown ──

  const agentIds = ['all', ...(agents?.agents?.map((a) => a.agent_id) ?? [])];

  const filteredEvalTrend = evalTrendData;

  return (
    <JARVISPageShell>

      {/* a11y: live region for async updates */}
      <div aria-live="polite" aria-atomic="true" className="sr-only" />
    <JARVISStagger className="space-y-6 pb-8">
      {/* ── Header ── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">Analytics</h1>
          <p className="text-muted-foreground text-sm mt-0.5">Goal, tool, eval, and cost insights</p>
        </div>
        <div className="flex gap-1 border rounded-lg overflow-hidden">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setDays(p)}
              aria-pressed={days === p}
              className={`px-3 py-1.5 text-sm transition-colors ${days === p ? 'bg-[#00D4FF] text-primary-foreground' : 'hover:bg-muted'}`}
            >
              {p}d
            </button>
          ))}
        </div>
      </div>

      {unavailable.length > 0 && (
        <div
          role="alert"
          data-testid="analytics-unavailable"
          className="border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 rounded-lg px-4 py-3 text-sm"
        >
          Analytics unavailable for: {unavailable.join(', ')}. This is a service error, not an
          absence of activity — the affected figures are not shown.
        </div>
      )}

      {/* ── Executive Summary KPI Row ── */}
      <section aria-label="Key performance indicators">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          <KpiCard
            label="Total Goals"
            value={goalsError ? NO_VALUE : fmt(totalGoals)}
            sub={`${days}d period`}
            icon={<Target className="h-4 w-4" />}
            accentClass="bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-[#00D4FF]"
          />
          <KpiCard
            label="Success Rate"
            value={pct(successRate)}
            trend={successRate != null && successRate >= 0.8 ? 'up' : successRate != null && successRate < 0.6 ? 'down' : 'flat'}
            trendLabel={successRate != null ? `${goals?.completed ?? 0} / ${totalGoals} goals` : undefined}
            icon={<CheckCircle className="h-4 w-4" />}
            accentClass="bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400"
          />
          <KpiCard
            label="Avg Duration"
            value={goals?.avg_duration_s != null ? `${goals.avg_duration_s.toFixed(1)}s` : '—'}
            sub="avg execution time"
            icon={<Activity className="h-4 w-4" />}
            accentClass="bg-violet-100 text-violet-600 dark:bg-violet-900/30 dark:text-violet-400"
          />
          <KpiCard
            label={`Cost (${days}d)`}
            value={usd(totalCost)}
            sub={costs?.avg_cost_per_goal != null ? `${usd(costs.avg_cost_per_goal)} / goal` : undefined}
            trend={costs?.cost_today_usd != null ? 'flat' : undefined}
            icon={<DollarSign className="h-4 w-4" />}
            accentClass="bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400"
          />
          <KpiCard
            label="Eval Pass Rate"
            value={pct(evalPassRate)}
            sub={avgEvalScore != null ? `avg score ${avgEvalScore.toFixed(2)}` : undefined}
            trend={evalPassRate != null && evalPassRate >= 0.8 ? 'up' : evalPassRate != null && evalPassRate < 0.6 ? 'down' : 'flat'}
            icon={<Zap className="h-4 w-4" />}
            accentClass="bg-yellow-100 text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400"
          />
          <KpiCard
            label="Active Agents"
            value={agentsError ? NO_VALUE : fmt(activeAgents)}
            sub={`across ${days}d`}
            icon={<Users className="h-4 w-4" />}
            accentClass="bg-pink-100 text-pink-600 dark:bg-pink-900/30 dark:text-pink-400"
          />
        </div>
      </section>

      {/* ── Row 2: Funnel + Tool Heatmap ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Goal Funnel */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Goal Execution Funnel</h2>
          {goalsError ? (
            <div className="h-40 flex items-center justify-center text-sm text-muted-foreground">Goal analytics unavailable</div>
          ) : totalGoals === 0 ? (
            <div className="h-40 flex items-center justify-center text-sm text-muted-foreground">No goals in period</div>
          ) : (
            <FunnelChart stages={funnelStages} />
          )}
          {totalGoals > 0 && goals && (
            <div className="mt-4 grid grid-cols-3 gap-2 text-center">
              {[
                { label: 'Failed', value: goals.failed, color: 'text-red-500' },
                { label: 'Cancelled', value: goals.cancelled, color: 'text-amber-500' },
                { label: 'Completed', value: goals.completed, color: 'text-emerald-500' },
              ].map(({ label, value, color }) => (
                <div key={label} className="border border-border rounded-lg p-2">
                  <p className={`text-lg font-bold ${color}`}>{fmt(value)}</p>
                  <p className="text-xs text-muted-foreground">{label}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Tool Performance Heatmap */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Tool Performance (Top 20)</h2>
          {/* `tools.tools` is optional in practice: a payload without it (an
              envelope, an empty object) made .length throw and took the whole
              Analytics page down through the error boundary. */}
          {toolsError ? (
            <div className="h-40 flex items-center justify-center text-sm text-muted-foreground">Tool analytics unavailable</div>
          ) : !tools?.tools?.length ? (
            <div className="h-40 flex items-center justify-center text-sm text-muted-foreground">No tool data yet</div>
          ) : (
            <div className="overflow-auto max-h-64">
              <div className="flex items-center gap-4 mb-3 text-xs text-muted-foreground">
                <span className="flex items-center gap-1"><span className="w-3 h-2 rounded-sm bg-emerald-500/80 inline-block" /> ≥90%</span>
                <span className="flex items-center gap-1"><span className="w-3 h-2 rounded-sm bg-yellow-500/80 inline-block" /> 75-90%</span>
                <span className="flex items-center gap-1"><span className="w-3 h-2 rounded-sm bg-orange-500/80 inline-block" /> 50-75%</span>
                <span className="flex items-center gap-1"><span className="w-3 h-2 rounded-sm bg-red-500/80 inline-block" /> &lt;50%</span>
              </div>
              {tools.tools.slice(0, 20).map((t) => (
                <ToolHeatmapRow key={t.name} tool={t} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Row 3: Cost Intelligence ── */}
      <div className="bg-card border border-border rounded-xl p-5">
        <h2 className="font-semibold text-sm mb-4">Cost Intelligence</h2>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div>
            <h3 className="text-xs font-medium text-muted-foreground mb-3">Cost by Model</h3>
            {costsError ? (
              <div className="h-32 flex items-center justify-center text-xs text-muted-foreground">Cost analytics unavailable</div>
            ) : costByModel.length === 0 ? (
              <div className="h-32 flex items-center justify-center text-xs text-muted-foreground">No model cost data</div>
            ) : (
              <ThemedBarChart
                data={costByModel}
                bars={[{ key: 'cost_usd', label: 'Cost USD', color: CHART_COLORS[4] }]}
                xKey="model"
                height={150}
                formatValue={usd}
              />
            )}
          </div>
          <div>
            <h3 className="text-xs font-medium text-muted-foreground mb-3">Cost Trajectory ({days}d)</h3>
            {costsError ? (
              <div className="h-32 flex items-center justify-center text-xs text-muted-foreground">Cost analytics unavailable</div>
            ) : costByDay.length === 0 ? (
              <div className="h-32 flex items-center justify-center text-xs text-muted-foreground">No cost trend data</div>
            ) : (
              <ThemedLineChart
                data={costByDay as Record<string, unknown>[]}
                lines={[{ key: 'cost_usd', label: 'Daily Cost', color: CHART_COLORS[4] }]}
                xKey="date"
                height={150}
                formatValue={usd}
              />
            )}
          </div>
          <div>
            <h3 className="text-xs font-medium text-muted-foreground mb-3">Agent Cost Ranking</h3>
            {agentsError ? (
              <div className="h-32 flex items-center justify-center text-xs text-muted-foreground">Agent analytics unavailable</div>
            ) : agentCostData.length === 0 ? (
              <div className="h-32 flex items-center justify-center text-xs text-muted-foreground">No agent data</div>
            ) : (
              <div className="space-y-1.5 overflow-auto max-h-40">
                {agentCostData.map((a, i) => (
                  <div key={a.agent} className="flex items-center gap-2 text-xs">
                    <span className="w-4 text-muted-foreground">{i + 1}.</span>
                    <span className="font-mono flex-1 truncate">{a.agent}</span>
                    <span className="text-muted-foreground">{usd(a.cost_usd)}</span>
                    <span className={`${a.success_rate >= 0.8 ? 'text-emerald-500' : 'text-amber-500'}`}>{pct(a.success_rate)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Row 4: Eval Trend ── */}
      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <h2 className="font-semibold text-sm">Eval Performance ({days}d)</h2>
          <div className="flex items-center gap-3 flex-wrap">
            <select
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              className="text-xs border border-border rounded-md px-2 py-1 bg-background"
            >
              {agentIds.map((id) => (
                <option key={id} value={id}>{id === 'all' ? 'All agents' : id.slice(0, 16)}</option>
              ))}
            </select>
            <div className="flex gap-1 flex-wrap">
              {evalDims.map((dim, i) => (
                <button
                  key={dim}
                  onClick={() => setEvalDimensions((prev) => {
                    const next = new Set(prev);
                    if (next.has(dim)) { if (next.size > 1) next.delete(dim); }
                    else next.add(dim);
                    return next;
                  })}
                  className={`px-2 py-0.5 rounded-full text-xs transition-colors ${
                    evalDimensions.has(dim) ? 'text-foreground' : 'bg-muted text-muted-foreground'
                  }`}
                  style={evalDimensions.has(dim) ? { backgroundColor: CHART_COLORS[i % CHART_COLORS.length] } : undefined}
                >
                  {dim.replace('_', ' ')}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div>
            {filteredEvalTrend.length === 0 ? (
              <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">No eval trend data</div>
            ) : (
              <ThemedLineChart
                data={filteredEvalTrend as Record<string, unknown>[]}
                lines={[
                  ...(evalDimensions.has('pass_rate') || evalDimensions.has('accuracy') ? [{ key: 'pass_rate', label: 'Pass Rate', color: CHART_COLORS[0] }] : []),
                  ...(evalDimensions.has('efficiency') || evalDimensions.has('avg_score') ? [{ key: 'avg_score', label: 'Avg Score', color: CHART_COLORS[1] }] : []),
                ].filter(Boolean)}
                xKey="date"
                height={200}
                formatValue={(v) => `${(v * 100).toFixed(0)}%`}
              />
            )}
          </div>
          <div>
            {evals?.avg_scores ? (
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(evals.avg_scores).map(([dim, score]) => (
                  <div key={dim} className="border border-border rounded-lg p-3">
                    <p className="text-xs text-muted-foreground capitalize">{dim.replace('_', ' ')}</p>
                    <div className="flex items-center gap-2 mt-1">
                      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${(score as number) >= 0.8 ? 'bg-emerald-500' : (score as number) >= 0.6 ? 'bg-yellow-500' : 'bg-red-500'}`}
                          style={{ width: `${(score as number) * 100}%` }}
                        />
                      </div>
                      <span className="text-xs font-bold tabular-nums">{((score as number) * 100).toFixed(0)}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">No eval summary</div>
            )}
          </div>
        </div>
      </div>

      {/* ── Row 5: Platform Benchmarks ── */}
      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-sm">Platform Benchmarks</h2>
          {benchmarks && (
            <span
              data-testid="benchmark-comparison-label"
              className={`px-2.5 py-1 rounded-full text-xs font-semibold ${
                benchmarkRanked
                  ? percentileBadge(benchmarks.percentile_success as number).className
                  : 'bg-muted text-muted-foreground'
              }`}
            >
              {benchmarkRanked ? benchmarks.comparison_label : NOT_ENOUGH_DATA}
            </span>
          )}
        </div>
        {!benchmarks ? (
          <div className="h-24 flex items-center justify-center text-sm text-muted-foreground">
            {benchmarksError ? 'Benchmark data unavailable' : 'Loading benchmark data…'}
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className="space-y-4">
              {platformBenchmarkMissing && (
                <p role="status" data-testid="benchmark-insufficient-data" className="text-xs text-muted-foreground">
                  {NOT_ENOUGH_DATA}: platform averages appear once enough tenants have contributed
                  completed goals.
                </p>
              )}
              <BenchmarkBar
                label="Success Rate"
                yours={benchmarks.your_success_rate}
                platform={benchmarks.platform_avg_success_rate}
                format={(v) => pct(v)}
              />
              <BenchmarkBar
                label="Avg Cost per Goal"
                yours={benchmarks.your_cost_usd}
                platform={benchmarks.platform_avg_cost_usd}
                format={usd}
                lowerIsBetter
              />
              <BenchmarkBar
                label="Eval Score"
                yours={benchmarks.your_eval_score}
                platform={benchmarks.platform_avg_eval_score}
                format={(v) => fmtFixed(v, 2)}
              />
              <div className="grid grid-cols-2 gap-2 pt-1 text-xs">
                {[
                  { label: 'Success %ile', value: isMetric(benchmarks.percentile_success) ? `#${benchmarks.percentile_success}` : NO_VALUE },
                  { label: 'Cost %ile', value: isMetric(benchmarks.percentile_cost) ? `#${benchmarks.percentile_cost}` : NO_VALUE },
                ].map(({ label, value }) => (
                  <div key={label} className="border border-border rounded-lg p-2 text-center">
                    <p className="text-muted-foreground">{label}</p>
                    <p className="font-bold">{value}</p>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-2 text-center">Dimension Comparison</p>
              {radarData.length > 0 ? (
                <ResponsiveContainer width="100%" height={200}>
                  <RadarChart data={radarData} margin={{ top: 8, right: 24, bottom: 8, left: 24 }}>
                    <PolarGrid stroke={CHART_AXIS_COLOR} opacity={0.3} />
                    <PolarAngleAxis dataKey="dim" tick={{ fill: CHART_AXIS_COLOR, fontSize: 10 }} />
                    <PolarRadiusAxis domain={[0, 1]} tick={{ fill: CHART_AXIS_COLOR, fontSize: 8 }} tickCount={3} />
                    <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v) => [fmtFixed(typeof v === 'number' ? v : null, 2)]} />
                    <Legend wrapperStyle={{ fontSize: 11, color: CHART_AXIS_COLOR }} />
                    <Radar name="You" dataKey="yours" stroke={CHART_COLORS[0]} fill={CHART_COLORS[0]} fillOpacity={0.25} strokeWidth={2} />
                    {showPlatformRadar && (
                      <Radar name="Platform Avg" dataKey="platform" stroke={CHART_COLORS[5]} fill={CHART_COLORS[5]} fillOpacity={0.15} strokeWidth={1.5} strokeDasharray="4 2" />
                    )}
                  </RadarChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-48 flex items-center justify-center text-xs text-muted-foreground">Insufficient eval data</div>
              )}
            </div>
          </div>
        )}
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
