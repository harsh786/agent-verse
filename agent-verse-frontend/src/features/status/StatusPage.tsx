/**
 * Public system status page — accessible at /status without auth.
 * Shows component health to operators and customers.
 */
import { useQuery } from '@tanstack/react-query';
import { CheckCircle, AlertTriangle, HelpCircle, RefreshCw } from 'lucide-react';

interface Component { status: 'operational' | 'degraded' | 'unknown'; latency_ms?: number; }
interface StatusData { status: string; components: Record<string, Component>; timestamp: number; }

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

const STATUS_CONFIG = {
  operational: { icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50 dark:bg-green-950/20', border: 'border-green-200 dark:border-green-800', label: 'All Systems Operational' },
  degraded: { icon: AlertTriangle, color: 'text-amber-600', bg: 'bg-amber-50 dark:bg-amber-950/20', border: 'border-amber-200 dark:border-amber-800', label: 'Partial Service Disruption' },
  unknown: { icon: HelpCircle, color: 'text-muted-foreground', bg: 'bg-muted', border: 'border-border', label: 'Status Unknown' },
};

export function StatusPage() {
  const { data, isLoading, error, refetch, dataUpdatedAt } = useQuery<StatusData>({
    queryKey: ['public-status'],
    queryFn: () => fetch(`${API_BASE}/status`).then(r => r.json()),
    refetchInterval: 30_000,
    retry: 2,
  });

  const overall = (data?.status ?? 'unknown') as keyof typeof STATUS_CONFIG;
  const cfg = STATUS_CONFIG[overall] ?? STATUS_CONFIG.unknown;
  const StatusIcon = cfg.icon;
  const lastUpdated = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : '—';

  return (
    <div className="min-h-screen bg-background" data-testid="status-page">
      {/* Header */}
      <div className="border-b border-border bg-card">
        <div className="max-w-2xl mx-auto px-6 py-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">AgentVerse Status</h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              Last updated: <time dateTime={new Date(dataUpdatedAt ?? 0).toISOString()}>{lastUpdated}</time>
            </p>
          </div>
          <button
            onClick={() => refetch()}
            aria-label="Refresh status"
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </button>
        </div>
      </div>

      <div className="max-w-2xl mx-auto px-6 py-8 space-y-6">
        {/* Overall banner */}
        <div className={`rounded-xl p-4 flex items-center gap-3 border ${cfg.bg} ${cfg.border}`} role="status" aria-live="polite">
          <StatusIcon className={`h-5 w-5 flex-shrink-0 ${cfg.color}`} aria-hidden />
          <div>
            <p className="font-semibold">{cfg.label}</p>
            {overall === 'degraded' && (
              <p className="text-sm text-muted-foreground mt-0.5">Some services are experiencing issues.</p>
            )}
          </div>
        </div>

        {/* Component list */}
        {isLoading && (
          <div className="space-y-2" aria-busy="true" aria-label="Loading status">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-14 bg-muted rounded-lg animate-pulse" />
            ))}
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
            Unable to load status. The status endpoint may be unavailable.
          </div>
        )}

        {data && (
          <ul className="space-y-2" role="list" aria-label="Service components">
            {Object.entries(data.components).map(([name, comp]) => {
              const compCfg = STATUS_CONFIG[comp.status as keyof typeof STATUS_CONFIG] ?? STATUS_CONFIG.unknown;
              const CompIcon = compCfg.icon;
              return (
                <li key={name} className="flex items-center justify-between p-3 rounded-lg border border-border bg-card">
                  <div className="flex items-center gap-3">
                    <CompIcon className={`h-4 w-4 flex-shrink-0 ${compCfg.color}`} aria-hidden />
                    <span className="text-sm font-medium capitalize">{name.replace(/_/g, ' ')}</span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    {comp.latency_ms !== undefined && (
                      <span className="tabular-nums">{comp.latency_ms.toFixed(0)}ms</span>
                    )}
                    <span className={`font-medium capitalize ${compCfg.color}`}>{comp.status}</span>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <p className="text-xs text-muted-foreground text-center">
          Auto-refreshes every 30 seconds. For incidents, check{' '}
          <a href="https://twitter.com/agentverse" className="underline hover:text-foreground" target="_blank" rel="noreferrer">@agentverse</a>.
        </p>
      </div>
    </div>
  );
}
