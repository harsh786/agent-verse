/**
 * AI Ops Dashboard — Mission Control for autonomous agents.
 * Shows live goal status, model health, alerts, cost, and active agents.
 */
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  Brain,
  AlertTriangle,
  Zap,
  TrendingUp,
  CheckCircle,
  XCircle,
  ArrowRight,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '@/lib/api/client';

/** GET /ai-ops/eval-results row (the fields this card shows). */
interface EvalRun {
  result_id: string;
  dataset_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'abandoned' | string;
  total_cases?: number;
  completed_cases?: number;
  avg_score?: number;
  passed?: boolean;
  error?: string;
}

function evalRunLabel(run: EvalRun): string {
  switch (run.status) {
    case 'queued':
      return 'queued — waiting for a worker';
    case 'running':
      return `running · ${run.completed_cases ?? 0}/${run.total_cases ?? '?'} cases`;
    case 'abandoned':
      return 'abandoned — no progress, the run was lost';
    case 'failed':
      return `failed${run.error ? ` · ${run.error}` : ''}`;
    case 'completed':
      return `${run.passed ? 'passed' : 'did not pass'} · ${Math.round((run.avg_score ?? 0) * 100)}%`;
    default:
      return run.status;
  }
}

const EVAL_RUN_COLORS: Record<string, string> = {
  queued: 'text-muted-foreground',
  running: 'text-[#00D4FF]',
  completed: 'text-green-600 dark:text-green-400',
  failed: 'text-red-500',
  abandoned: 'text-amber-500',
};

export function AIOpsDashboard() {
  const navigate = useNavigate();

  // No .catch(() => empty) fallbacks: a failed request used to read as
  // "0 active goals", "0/0 providers" and "no alerts" during an outage.
  const { data: goalsData, isError: goalsError } = useQuery({
    queryKey: ['dashboard-goals'],
    queryFn: () => apiFetch<any>('/goals'),
    refetchInterval: 5_000,
  });

  const { data: modelsData, isError: modelsError } = useQuery({
    queryKey: ['dashboard-models'],
    queryFn: () => apiFetch<any>('/models/health'),
    refetchInterval: 30_000,
  });

  const { data: alertsData, isError: alertsError } = useQuery({
    queryKey: ['dashboard-alerts'],
    queryFn: () => apiFetch<any>('/ai-ops/alerts?limit=5'),
    refetchInterval: 30_000,
  });

  const { data: regressionData, isError: regressionError } = useQuery({
    queryKey: ['dashboard-regression'],
    queryFn: () => apiFetch<any>('/ai-ops/regression-status'),
    refetchInterval: 60_000,
  });

  // Dataset eval runs execute on workers; poll while any is still in flight.
  const { data: runsData, isError: runsError } = useQuery({
    queryKey: ['dashboard-eval-runs'],
    queryFn: () => apiFetch<{ results?: EvalRun[] }>('/ai-ops/eval-results'),
    refetchInterval: (query) => {
      const rows = (query.state.data as { results?: EvalRun[] } | undefined)?.results ?? [];
      return rows.some((r) => r.status === 'queued' || r.status === 'running') ? 10_000 : 60_000;
    },
  });
  const evalRuns = (runsData?.results ?? []).slice(0, 5);

  const goals = goalsData?.goals ?? [];
  const activeGoals = goals.filter((g: any) => ['executing', 'planning'].includes(g.status));
  const completedToday = goals.filter((g: any) => g.status === 'complete').length;
  const failedToday = goals.filter((g: any) => g.status === 'failed').length;
  const providers = modelsData?.providers ?? [];
  const healthyProviders = providers.filter((p: any) => p.is_healthy).length;
  const alerts = alertsData?.alerts ?? [];
  // An unreachable endpoint is "unknown" — never 'ok' ("All systems normal").
  const regressionStatus = regressionError ? 'unknown' : (regressionData?.status ?? 'unknown');
  const UNAVAILABLE = '—';

  const REGRESSION_COLORS: Record<string, string> = {
    ok: 'text-green-600 dark:text-green-400',
    warning: 'text-amber-600 dark:text-amber-400',
    critical: 'text-red-600 dark:text-red-400',
  };

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Zap className="h-6 w-6 text-[#00D4FF]" />
          AI Operations Center
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Live platform health · {new Date().toLocaleTimeString()}
        </p>
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {[
          {
            label: 'Active Goals',
            value: goalsError ? UNAVAILABLE : activeGoals.length,
            icon: Activity,
            color: 'text-blue-500',
            onClick: () => navigate('/goals?status=executing'),
          },
          {
            label: 'Completed Today',
            value: goalsError ? UNAVAILABLE : completedToday,
            icon: CheckCircle,
            color: 'text-green-500',
            onClick: () => navigate('/goals?status=complete'),
          },
          {
            label: 'Failed Today',
            value: goalsError ? UNAVAILABLE : failedToday,
            icon: XCircle,
            color: !goalsError && failedToday > 0 ? 'text-red-500' : 'text-muted-foreground',
            onClick: () => navigate('/goals?status=failed'),
          },
          {
            label: 'Healthy Providers',
            value: modelsError ? UNAVAILABLE : `${healthyProviders}/${providers.length}`,
            icon: Brain,
            color: modelsError
              ? 'text-muted-foreground'
              : healthyProviders === providers.length ? 'text-green-500' : 'text-amber-500',
            onClick: () => navigate('/models'),
          },
        ].map(({ label, value, icon: Icon, color, onClick }) => (
          <button
            key={label}
            onClick={onClick}
            className="bg-card border border-border rounded-xl p-5 text-left hover:border-[#00D4FF]/30 transition-colors group"
          >
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs font-medium text-muted-foreground">{label}</p>
              <Icon className={`h-5 w-5 ${color}`} />
            </div>
            <p className="text-3xl font-bold tabular-nums">{value}</p>
            <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              View details <ArrowRight className="h-3 w-3" />
            </p>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Active Goals */}
        <div className="lg:col-span-2 bg-card border border-border rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-border">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Activity className="h-4 w-4 text-[#00D4FF]" />
              Active Goals
            </h2>
            <button
              onClick={() => navigate('/goals')}
              className="text-xs text-primary hover:underline"
            >
              View all →
            </button>
          </div>
          <div className="divide-y divide-border">
            {goalsError ? (
              <div role="alert" className="px-5 py-8 text-center text-red-500">
                <XCircle className="h-8 w-8 opacity-40 mx-auto mb-2" />
                <p className="text-sm">Goals could not be loaded</p>
              </div>
            ) : activeGoals.length === 0 ? (
              <div className="px-5 py-8 text-center text-muted-foreground">
                <CheckCircle className="h-8 w-8 opacity-20 mx-auto mb-2" />
                <p className="text-sm">No active goals</p>
              </div>
            ) : (
              activeGoals.slice(0, 5).map((goal: any) => (
                <button
                  key={goal.id}
                  onClick={() => navigate(`/goals/${goal.id}`)}
                  className="w-full px-5 py-3.5 flex items-center gap-3 hover:bg-muted/30 transition-colors text-left"
                >
                  <div
                    className={`w-2 h-2 rounded-full shrink-0 ${
                      goal.status === 'executing'
                        ? 'bg-blue-500 animate-pulse'
                        : 'bg-amber-500 animate-pulse'
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm truncate">{goal.goal}</p>
                    <p className="text-xs text-muted-foreground capitalize">
                      {goal.status} · {goal.agent_id ? 'agent assigned' : 'auto'}
                    </p>
                  </div>
                  <ArrowRight className="h-4 w-4 text-muted-foreground shrink-0" />
                </button>
              ))
            )}
          </div>
        </div>

        {/* Right panel */}
        <div className="space-y-4">
          {/* Model Health */}
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <h2 className="text-sm font-semibold flex items-center gap-2">
                <Brain className="h-4 w-4 text-[#00D4FF]" />
                Provider Health
              </h2>
              <button
                onClick={() => navigate('/models')}
                className="text-xs text-primary hover:underline"
              >
                Details →
              </button>
            </div>
            <div className="p-4 space-y-2">
              {modelsError ? (
                <p role="alert" className="text-xs text-red-500">Provider health could not be loaded</p>
              ) : providers.length === 0 ? (
                <p className="text-xs text-muted-foreground">No providers tested yet</p>
              ) : (
                providers.map((p: any) => (
                  <div key={p.provider} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {p.is_healthy ? (
                        <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
                      ) : (
                        <div className="w-1.5 h-1.5 rounded-full bg-red-500" />
                      )}
                      <span className="text-xs capitalize">{p.provider}</span>
                    </div>
                    <span className="text-[10px] text-muted-foreground">
                      {p.avg_latency_ms > 0 ? `${Math.round(p.avg_latency_ms)}ms` : 'Not tested'}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Regression Status */}
          <div className="bg-card border border-border rounded-xl p-4">
            <h2 className="text-sm font-semibold flex items-center gap-2 mb-3">
              <TrendingUp className="h-4 w-4 text-[#00D4FF]" />
              Regression Status
            </h2>
            <div className="flex items-center gap-2">
              {regressionStatus === 'ok' ? (
                <CheckCircle className="h-5 w-5 text-green-500" />
              ) : regressionStatus === 'unknown' ? (
                <AlertTriangle className="h-5 w-5 text-muted-foreground" />
              ) : regressionStatus === 'warning' ? (
                <AlertTriangle className="h-5 w-5 text-amber-500" />
              ) : (
                <XCircle className="h-5 w-5 text-red-500" />
              )}
              <div>
                <p
                  className={`text-sm font-medium capitalize ${
                    REGRESSION_COLORS[regressionStatus] ?? 'text-muted-foreground'
                  }`}
                >
                  {regressionStatus === 'ok'
                    ? 'All systems normal'
                    : regressionStatus === 'unknown'
                      ? 'Status unavailable'
                      : `${regressionStatus} detected`}
                </p>
                {!regressionError && regressionData && (
                  <p className="text-xs text-muted-foreground">
                    {regressionData.critical_alerts ?? 0} critical · {regressionData.warning_alerts ?? 0} warnings
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Eval runs (AI-Ops datasets) */}
          {(runsError || evalRuns.length > 0) && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <h2 className="text-sm font-semibold flex items-center gap-2">
                  <Activity className="h-4 w-4 text-[#00D4FF]" />
                  Eval Runs
                </h2>
              </div>
              {runsError ? (
                <p role="alert" className="px-4 py-3 text-xs text-red-500">Eval runs could not be loaded</p>
              ) : (
                <div className="divide-y divide-border">
                  {evalRuns.map((run) => (
                    <div key={run.result_id} data-testid={`eval-run-${run.result_id}`} className="px-4 py-2.5">
                      <p className="text-xs font-medium truncate">{run.dataset_id}</p>
                      <p className={`text-[10px] mt-0.5 ${EVAL_RUN_COLORS[run.status] ?? 'text-muted-foreground'}`}>
                        {evalRunLabel(run)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Recent Alerts */}
          {alertsError && (
            <div role="alert" className="bg-card border border-red-500/30 rounded-xl px-4 py-3 text-xs text-red-500">
              Alerts could not be loaded — there may be alerts you are not seeing.
            </div>
          )}
          {alerts.length > 0 && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <h2 className="text-sm font-semibold flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-amber-500" />
                  Recent Alerts
                </h2>
              </div>
              <div className="divide-y divide-border">
                {alerts.slice(0, 3).map((a: any) => (
                  <div key={a.alert_id} className="px-4 py-2.5">
                    <p className="text-xs font-medium truncate">{a.message}</p>
                    <p
                      className={`text-[10px] mt-0.5 ${
                        a.severity === 'critical' ? 'text-red-500' : 'text-amber-500'
                      }`}
                    >
                      {a.severity} · {a.drift_type}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
