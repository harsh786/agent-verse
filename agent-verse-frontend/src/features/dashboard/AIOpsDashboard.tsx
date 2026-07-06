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

export function AIOpsDashboard() {
  const navigate = useNavigate();

  const { data: goalsData } = useQuery({
    queryKey: ['dashboard-goals'],
    queryFn: () => apiFetch<any>('/goals').catch(() => ({ goals: [] })),
    refetchInterval: 5_000,
  });

  const { data: modelsData } = useQuery({
    queryKey: ['dashboard-models'],
    queryFn: () => apiFetch<any>('/models/health').catch(() => ({ providers: [] })),
    refetchInterval: 30_000,
  });

  const { data: alertsData } = useQuery({
    queryKey: ['dashboard-alerts'],
    queryFn: () => apiFetch<any>('/ai-ops/alerts?limit=5').catch(() => ({ alerts: [] })),
    refetchInterval: 30_000,
  });

  const { data: regressionData } = useQuery({
    queryKey: ['dashboard-regression'],
    queryFn: () => apiFetch<any>('/ai-ops/regression-status').catch(() => ({ status: 'ok' })),
    refetchInterval: 60_000,
  });

  const goals = goalsData?.goals ?? [];
  const activeGoals = goals.filter((g: any) => ['executing', 'planning'].includes(g.status));
  const completedToday = goals.filter((g: any) => g.status === 'complete').length;
  const failedToday = goals.filter((g: any) => g.status === 'failed').length;
  const providers = modelsData?.providers ?? [];
  const healthyProviders = providers.filter((p: any) => p.is_healthy).length;
  const alerts = alertsData?.alerts ?? [];
  const regressionStatus = regressionData?.status ?? 'ok';

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
          <Zap className="h-6 w-6 text-primary" />
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
            value: activeGoals.length,
            icon: Activity,
            color: 'text-blue-500',
            onClick: () => navigate('/goals?status=executing'),
          },
          {
            label: 'Completed Today',
            value: completedToday,
            icon: CheckCircle,
            color: 'text-green-500',
            onClick: () => navigate('/goals?status=complete'),
          },
          {
            label: 'Failed Today',
            value: failedToday,
            icon: XCircle,
            color: failedToday > 0 ? 'text-red-500' : 'text-muted-foreground',
            onClick: () => navigate('/goals?status=failed'),
          },
          {
            label: 'Healthy Providers',
            value: `${healthyProviders}/${providers.length}`,
            icon: Brain,
            color: healthyProviders === providers.length ? 'text-green-500' : 'text-amber-500',
            onClick: () => navigate('/models'),
          },
        ].map(({ label, value, icon: Icon, color, onClick }) => (
          <button
            key={label}
            onClick={onClick}
            className="bg-card border border-border rounded-xl p-5 text-left hover:border-primary/30 transition-colors group"
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
              <Activity className="h-4 w-4 text-primary" />
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
            {activeGoals.length === 0 ? (
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
                <Brain className="h-4 w-4 text-primary" />
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
              {providers.length === 0 ? (
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
              <TrendingUp className="h-4 w-4 text-primary" />
              Regression Status
            </h2>
            <div className="flex items-center gap-2">
              {regressionStatus === 'ok' ? (
                <CheckCircle className="h-5 w-5 text-green-500" />
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
                  {regressionStatus === 'ok' ? 'All systems normal' : `${regressionStatus} detected`}
                </p>
                <p className="text-xs text-muted-foreground">
                  {regressionData?.critical_alerts ?? 0} critical · {regressionData?.warning_alerts ?? 0} warnings
                </p>
              </div>
            </div>
          </div>

          {/* Recent Alerts */}
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
