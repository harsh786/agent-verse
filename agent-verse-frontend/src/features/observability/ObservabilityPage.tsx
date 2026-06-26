import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle, XCircle, AlertCircle, ExternalLink } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';
const GRAFANA_URL = 'http://localhost:3001';

interface HealthDependency {
  status: string;
  latency_ms?: number;
  message?: string;
}

interface HealthResponse {
  status: string;
  version?: string;
  dependencies?: Record<string, HealthDependency>;
  [key: string]: unknown;
}

async function fetchHealth(apiKey: string): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/health`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function fetchMetrics(apiKey: string): Promise<string> {
  const res = await fetch(`${API_BASE}/metrics`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  // metrics endpoint often returns Prometheus text format
  return res.text();
}

function StatusIcon({ status }: { status: string }) {
  const s = status?.toLowerCase();
  if (s === 'ok' || s === 'healthy' || s === 'up') {
    return <CheckCircle className="h-5 w-5 text-green-500" />;
  }
  if (s === 'degraded' || s === 'warn') {
    return <AlertCircle className="h-5 w-5 text-yellow-500" />;
  }
  return <XCircle className="h-5 w-5 text-red-500" />;
}

function statusColor(status: string) {
  const s = status?.toLowerCase();
  if (s === 'ok' || s === 'healthy' || s === 'up') return 'bg-green-50 border-green-200';
  if (s === 'degraded' || s === 'warn') return 'bg-yellow-50 border-yellow-200';
  return 'bg-red-50 border-red-200';
}

export function ObservabilityPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [grafanaAvailable, setGrafanaAvailable] = useState<boolean | null>(null);

  const {
    data: health,
    isLoading: healthLoading,
    error: healthError,
  } = useQuery({
    queryKey: ['health'],
    queryFn: () => fetchHealth(apiKey),
    enabled: !!apiKey,
    refetchInterval: 30_000,
  });

  const {
    data: metrics,
    isLoading: metricsLoading,
    error: metricsError,
  } = useQuery({
    queryKey: ['metrics'],
    queryFn: () => fetchMetrics(apiKey),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });

  // Check Grafana availability
  React.useEffect(() => {
    fetch(GRAFANA_URL, { mode: 'no-cors' })
      .then(() => setGrafanaAvailable(true))
      .catch(() => setGrafanaAvailable(false));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Observability</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Health, metrics, and external dashboards
        </p>
      </div>

      {/* Grafana link */}
      <div
        className={`bg-card border rounded-xl p-4 flex items-center justify-between ${
          grafanaAvailable === false ? 'opacity-50' : ''
        }`}
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-orange-100 rounded-lg flex items-center justify-center text-orange-600 font-bold text-sm">
            G
          </div>
          <div>
            <p className="font-medium text-sm">Grafana Dashboard</p>
            <p className="text-xs text-muted-foreground">{GRAFANA_URL}</p>
          </div>
        </div>
        <a
          href={GRAFANA_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={`flex items-center gap-1.5 text-sm text-primary hover:opacity-70 ${
            grafanaAvailable === false ? 'pointer-events-none' : ''
          }`}
        >
          Open <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      {/* Health status */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <h2 className="font-semibold text-sm">System Health</h2>
          {health && (
            <div className="flex items-center gap-1.5">
              <StatusIcon status={health.status} />
              <span className="text-sm font-medium capitalize">{health.status}</span>
              {health.version && (
                <span className="text-xs text-muted-foreground ml-2">
                  v{health.version}
                </span>
              )}
            </div>
          )}
        </div>

        {healthLoading ? (
          <div className="px-5 py-8 text-center text-sm text-muted-foreground">
            Checking health…
          </div>
        ) : healthError ? (
          <div className="px-5 py-8 text-center text-sm text-red-500">
            Failed to reach health endpoint.
          </div>
        ) : (
          <div className="p-5">
            {health?.dependencies && Object.keys(health.dependencies).length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {Object.entries(health.dependencies).map(([name, dep]) => (
                  <div
                    key={name}
                    className={`border rounded-lg p-3 ${statusColor(dep.status)}`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <StatusIcon status={dep.status} />
                      <span className="font-medium text-sm capitalize">{name}</span>
                    </div>
                    <p className="text-xs text-muted-foreground capitalize">{dep.status}</p>
                    {dep.latency_ms != null && (
                      <p className="text-xs text-muted-foreground">{dep.latency_ms}ms</p>
                    )}
                    {dep.message && (
                      <p className="text-xs text-muted-foreground mt-1">{dep.message}</p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <pre className="text-xs font-mono text-muted-foreground overflow-auto whitespace-pre-wrap">
                {JSON.stringify(health, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>

      {/* Metrics */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-sm">Prometheus Metrics</h2>
        </div>
        {metricsLoading ? (
          <div className="px-5 py-8 text-center text-sm text-muted-foreground">
            Loading metrics…
          </div>
        ) : metricsError ? (
          <div className="px-5 py-8 text-center text-sm text-red-500">
            Failed to load metrics.
          </div>
        ) : (
          <pre className="px-5 py-4 text-xs font-mono text-muted-foreground overflow-auto max-h-96 whitespace-pre-wrap">
            {metrics ?? 'No metrics available.'}
          </pre>
        )}
      </div>
    </div>
  );
}
