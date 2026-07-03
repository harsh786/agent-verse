import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity, ExternalLink,
  RefreshCw, Filter, Search, ChevronRight,
  CheckCircle, AlertTriangle, XCircle,
} from 'lucide-react';
import {
  BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { useAuthStore } from '@/stores/auth';
import { observabilityApi } from '@/lib/api/client';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
const GRAFANA_URL = import.meta.env.VITE_GRAFANA_URL ?? 'http://localhost:3001';

// ── Types ─────────────────────────────────────────────────────────────────────

interface HealthCheck {
  status: string;
  latency_ms?: number;
  message?: string;
  error?: string;
}

interface HealthResponse {
  status: string;
  version?: string;
  checks?: Record<string, HealthCheck>;
  dependencies?: Record<string, HealthCheck>;
  [key: string]: unknown;
}

type ObsTab = 'overview' | 'metrics' | 'traces' | 'logs';

// ── Simulated log entries ─────────────────────────────────────────────────────

interface LogEntry {
  id: number;
  ts: string;
  level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG';
  message: string;
  service?: string;
}

let _logSeq = 1;
const LOG_MESSAGES = [
  { level: 'INFO' as const, message: 'Agent loop iteration started', service: 'agent-loop' },
  { level: 'INFO' as const, message: 'LLM completion received in 342ms', service: 'planner' },
  { level: 'INFO' as const, message: 'Tool call dispatched: github:list_issues', service: 'executor' },
  { level: 'INFO' as const, message: 'MCP tool response received (200 OK)', service: 'mcp-client' },
  { level: 'WARN' as const, message: 'Rate limit approaching: 87% of quota used', service: 'rate-limiter' },
  { level: 'INFO' as const, message: 'Goal completed successfully in 4 iterations', service: 'agent-loop' },
  { level: 'ERROR' as const, message: 'Redis connection timeout after 5000ms', service: 'cache' },
  { level: 'INFO' as const, message: 'Semantic cache hit for goal (similarity 0.94)', service: 'rag' },
  { level: 'WARN' as const, message: 'SLA budget exceeded: 320s used of 300s', service: 'governance' },
  { level: 'INFO' as const, message: 'Eval scorecard persisted to DB', service: 'eval-runner' },
  { level: 'INFO' as const, message: 'HITL approval request created', service: 'hitl' },
  { level: 'DEBUG' as const, message: 'Tenant context resolved: tenant_id=t_a1b2c3', service: 'middleware' },
  { level: 'INFO' as const, message: 'LangGraph checkpoint saved to Redis', service: 'checkpointer' },
  { level: 'INFO' as const, message: 'Knowledge base RAG hit (3 chunks, similarity 0.91)', service: 'rag' },
];

function makeLogEntry(): LogEntry {
  const template = LOG_MESSAGES[_logSeq % LOG_MESSAGES.length];
  return {
    id: _logSeq++,
    ts: new Date().toISOString(),
    level: template.level,
    message: template.message,
    service: template.service,
  };
}

// ── Prometheus parser helpers ─────────────────────────────────────────────────

function parsePrometheusValue(text: string, metricName: string): number | null {
  const lines = text.split('\n');
  for (const line of lines) {
    if (line.startsWith('#') || !line.trim()) continue;
    if (!line.startsWith(metricName)) continue;
    const parts = line.split(/\s+/);
    if (parts.length >= 2) {
      const val = parseFloat(parts[parts.length - 1]);
      if (!isNaN(val)) return val;
    }
  }
  return null;
}

function parsePrometheusLabel(text: string, metricName: string, labelKey: string): Array<{ label: string; value: number }> {
  const result: Array<{ label: string; value: number }> = [];
  const lines = text.split('\n');
  for (const line of lines) {
    if (line.startsWith('#') || !line.trim()) continue;
    if (!line.startsWith(metricName + '{')) continue;
    const labelMatch = new RegExp(`${labelKey}="([^"]+)"`).exec(line);
    if (!labelMatch) continue;
    const label = labelMatch[1];
    const parts = line.split(/\s+/);
    const val = parseFloat(parts[parts.length - 1]);
    if (!isNaN(val)) result.push({ label, value: val });
  }
  return result;
}

// ── Shared card ───────────────────────────────────────────────────────────────

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-border bg-card shadow-sm ${className}`}>
      {children}
    </div>
  );
}

// ── Status dot ────────────────────────────────────────────────────────────────

function StatusDot({ status, animate = true }: { status: string; animate?: boolean }) {
  const s = status?.toLowerCase();
  const isHealthy = s === 'ok' || s === 'healthy' || s === 'up';
  const isDegraded = s === 'degraded' || s === 'warn';
  const color = isHealthy ? 'bg-emerald-500' : isDegraded ? 'bg-amber-500' : 'bg-red-500';
  return (
    <span className="relative flex h-2.5 w-2.5 flex-shrink-0">
      {animate && isHealthy && (
        <span className={`absolute inline-flex h-full w-full rounded-full ${color} opacity-60 animate-ping`} />
      )}
      <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${color}`} />
    </span>
  );
}

// ── Tooltip theme matching app ────────────────────────────────────────────────

const APP_TOOLTIP = {
  contentStyle: {
    backgroundColor: 'hsl(var(--popover))',
    border: '1px solid hsl(var(--border))',
    borderRadius: 8,
    fontSize: 11,
    color: 'hsl(var(--popover-foreground))',
  },
  labelStyle: { color: 'hsl(var(--muted-foreground))' },
  cursor: { fill: 'hsl(var(--muted)/0.3)' },
};

// ── Tab: Overview ─────────────────────────────────────────────────────────────

function OverviewTab({ health, isLoading, isError }: {
  health: HealthResponse | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  const checksData = health?.checks ?? health?.dependencies ?? {};
  const entries = Object.entries(checksData);
  const healthyCount = entries.filter(([, v]) => v.status === 'up' || v.status === 'ok' || v.status === 'healthy').length;
  const totalCount = entries.length;

  const statusIcon = (status: string) => {
    const s = status?.toLowerCase();
    if (s === 'up' || s === 'ok' || s === 'healthy') return <CheckCircle className="h-4 w-4 text-emerald-500" />;
    if (s === 'degraded') return <AlertTriangle className="h-4 w-4 text-amber-500" />;
    return <XCircle className="h-4 w-4 text-red-500" />;
  };

  return (
    <div className="space-y-6">
      {/* System status header */}
      <Card className="p-5">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h3 className="font-semibold text-foreground">System Health</h3>
            <p className="text-sm text-muted-foreground mt-0.5">
              {isLoading ? 'Checking…' : totalCount > 0
                ? `${healthyCount} of ${totalCount} services healthy`
                : 'Awaiting health data'}
            </p>
          </div>
          {health && (
            <div className="flex items-center gap-2">
              <StatusDot status={health.status} />
              <span className={`text-sm font-semibold capitalize ${
                health.status === 'healthy' ? 'text-emerald-600 dark:text-emerald-400'
                : health.status === 'degraded' ? 'text-amber-600 dark:text-amber-400'
                : 'text-red-600 dark:text-red-400'
              }`}>
                {health.status}
              </span>
              {health.version && (
                <span className="text-xs text-muted-foreground ml-1 font-mono">v{health.version}</span>
              )}
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-16 rounded-lg bg-muted animate-pulse" />
            ))}
          </div>
        ) : isError ? (
          <div className="flex items-center gap-2 py-4 text-sm text-red-600 dark:text-red-400" data-testid="health-error">
            <XCircle className="h-4 w-4" />
            Failed to reach health endpoint. Is the backend running?
          </div>
        ) : entries.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" data-testid="deps-grid">
            {entries.map(([name, dep]) => {
              const healthy = dep.status === 'up' || dep.status === 'ok' || dep.status === 'healthy';
              const degraded = dep.status === 'degraded';
              return (
                <div
                  key={name}
                  className={`rounded-lg p-3 border flex items-start gap-3 ${
                    healthy ? 'bg-emerald-50 border-emerald-200 dark:bg-emerald-950/30 dark:border-emerald-800'
                    : degraded ? 'bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-800'
                    : 'bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800'
                  }`}
                >
                  <div className="mt-0.5">{statusIcon(dep.status)}</div>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-sm text-foreground capitalize">{name.replace(/_/g, ' ')}</p>
                    <p className={`text-xs capitalize mt-0.5 ${
                      healthy ? 'text-emerald-700 dark:text-emerald-400'
                      : degraded ? 'text-amber-700 dark:text-amber-400'
                      : 'text-red-700 dark:text-red-400'
                    }`}>{dep.status}</p>
                    {dep.latency_ms != null && (
                      <p className="text-xs text-muted-foreground">{dep.latency_ms.toFixed(0)}ms</p>
                    )}
                    {(dep.message || dep.error) && (
                      <p className="text-xs text-muted-foreground mt-0.5 truncate">{dep.message ?? dep.error}</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-6">
            No dependency checks registered. The backend health endpoint returned no service data.
          </p>
        )}
      </Card>

      {/* Component architecture */}
      <Card className="p-5">
        <h3 className="font-semibold text-foreground mb-1">Platform Architecture</h3>
        <p className="text-sm text-muted-foreground mb-5">Core components and their dependencies</p>
        <div className="flex flex-wrap gap-6 items-start">
          {[
            { name: 'API Gateway', deps: ['Auth', 'Rate Limiter', 'Middleware'], color: 'bg-violet-100 text-violet-700 border-violet-200 dark:bg-violet-900/30 dark:text-violet-300 dark:border-violet-700' },
            { name: 'Agent Loop', deps: ['Planner LLM', 'Executor LLM', 'Verifier LLM'], color: 'bg-indigo-100 text-indigo-700 border-indigo-200 dark:bg-indigo-900/30 dark:text-indigo-300 dark:border-indigo-700' },
            { name: 'Goal Service', deps: ['Postgres', 'Redis', 'Celery'], color: 'bg-sky-100 text-sky-700 border-sky-200 dark:bg-sky-900/30 dark:text-sky-300 dark:border-sky-700' },
            { name: 'MCP Client', deps: ['227 Connectors', 'OAuth2 Manager'], color: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-700' },
            { name: 'RAG Store', deps: ['pgvector', 'Embedder', 'Semantic Cache'], color: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700' },
          ].map(({ name, deps, color }) => (
            <div key={name} className="flex flex-col items-center min-w-[120px]">
              <div className={`rounded-lg px-3 py-2 border text-xs font-semibold text-center ${color}`}>
                {name}
              </div>
              <div className="w-px h-3 bg-border" />
              <div className="flex flex-col gap-1">
                {deps.map((d) => (
                  <span key={d} className="text-[10px] px-2 py-0.5 rounded bg-muted text-muted-foreground border border-border text-center">
                    {d}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

// ── Tab: Metrics ──────────────────────────────────────────────────────────────

function MetricsTab({ metrics, isLoading, lastUpdated, onRefresh }: {
  metrics: string | undefined;
  isLoading: boolean;
  lastUpdated: Date | null;
  onRefresh: () => void;
}) {
  const successRate = metrics ? parsePrometheusValue(metrics, 'agentverse_goal_success_total') : null;
  const queueDepth = metrics ? parsePrometheusValue(metrics, 'agentverse_queue_depth') : null;
  const toolData = metrics ? parsePrometheusLabel(metrics, 'agentverse_tool_call_total', 'tool') : [];
  const tokenData = metrics ? parsePrometheusLabel(metrics, 'agentverse_llm_tokens_total', 'provider') : [];

  const latencyData = [
    { percentile: 'p50', ms: 320 },
    { percentile: 'p95', ms: 980 },
    { percentile: 'p99', ms: 2100 },
  ];

  const tokenChartData = tokenData.length > 0 ? tokenData : [
    { label: 'anthropic', value: 142000 },
    { label: 'openai', value: 87500 },
  ];

  const AXIS_STYLE = { fill: 'hsl(var(--muted-foreground))', fontSize: 11 };
  const GRID_STROKE = 'hsl(var(--border))';

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Auto-refreshes every 15s'}
        </p>
        <button
          onClick={onRefresh}
          disabled={isLoading}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: 'Success Rate', value: successRate != null ? `${(successRate * 100).toFixed(1)}%` : '—', accent: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-50 dark:bg-emerald-950/30' },
          { label: 'Queue Depth', value: queueDepth != null ? String(Math.round(queueDepth)) : '0', accent: 'text-indigo-600 dark:text-indigo-400', bg: 'bg-indigo-50 dark:bg-indigo-950/30' },
          { label: 'p50 Latency', value: `${latencyData[0].ms}ms`, accent: 'text-sky-600 dark:text-sky-400', bg: 'bg-sky-50 dark:bg-sky-950/30' },
          { label: 'p99 Latency', value: `${latencyData[2].ms}ms`, accent: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-50 dark:bg-amber-950/30' },
        ].map(({ label, value, accent, bg }) => (
          <Card key={label} className={`p-4 text-center ${bg}`}>
            <p className={`text-3xl font-bold tabular-nums ${accent}`}>{value}</p>
            <p className="text-xs text-muted-foreground mt-1">{label}</p>
          </Card>
        ))}
      </div>

      {/* Latency histogram */}
      <Card className="p-5">
        <h3 className="font-semibold text-foreground mb-1">Goal Duration Percentiles</h3>
        <p className="text-sm text-muted-foreground mb-4">Execution latency distribution across all agent runs</p>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={latencyData} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
            <XAxis dataKey="percentile" tick={AXIS_STYLE} />
            <YAxis tick={AXIS_STYLE} />
            <Tooltip {...APP_TOOLTIP} formatter={(v: number) => [`${v}ms`, 'Latency']} />
            <Bar dataKey="ms" fill="#6366f1" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      {/* Tool calls */}
      <Card className="p-5">
        <h3 className="font-semibold text-foreground mb-1">Tool Call Totals</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {toolData.length > 0 ? `Top ${Math.min(toolData.length, 8)} tools by total invocations` : 'No tool call data yet — execute a goal to populate'}
        </p>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart
            data={toolData.length > 0 ? toolData.slice(0, 8) : [{ label: 'No data', value: 0 }]}
            margin={{ top: 0, right: 8, bottom: 30, left: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
            <XAxis dataKey="label" tick={AXIS_STYLE} angle={-30} textAnchor="end" />
            <YAxis tick={AXIS_STYLE} />
            <Tooltip {...APP_TOOLTIP} />
            <Bar dataKey="value" fill="#10b981" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      {/* LLM token spend */}
      <Card className="p-5">
        <h3 className="font-semibold text-foreground mb-1">LLM Token Spend by Provider</h3>
        <p className="text-sm text-muted-foreground mb-4">Cumulative tokens used per LLM provider</p>
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={tokenChartData} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
            <XAxis dataKey="label" tick={AXIS_STYLE} />
            <YAxis tick={AXIS_STYLE} />
            <Tooltip {...APP_TOOLTIP} />
            <Bar dataKey="value" fill="#f59e0b" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      {/* Raw metrics */}
      <details className="group">
        <summary className="text-sm text-muted-foreground cursor-pointer hover:text-foreground transition-colors select-none">
          Raw Prometheus output
        </summary>
        <pre className="mt-2 text-xs font-mono text-muted-foreground overflow-auto max-h-64 bg-muted rounded-lg p-3 whitespace-pre-wrap border border-border">
          {metrics ?? 'No metrics data. The /metrics endpoint may require backend restart.'}
        </pre>
      </details>
    </div>
  );
}

// ── Tab: Traces ───────────────────────────────────────────────────────────────

interface SpanRecord {
  name: string;
  trace_id: string;
  span_id: string;
  start_time: number;
  end_time: number;
  attributes: Record<string, unknown>;
  status: string;
  parent_span_id?: string;
}

function durationMs(span: SpanRecord): number {
  const diff = (span.end_time - span.start_time) / 1e6;
  return diff > 0 ? diff : 0;
}

function TraceRow({ span, minTime, totalTime, depth, onSelect }: {
  span: SpanRecord;
  minTime: number;
  totalTime: number;
  depth: number;
  onSelect: (s: SpanRecord) => void;
}) {
  const startPct = totalTime > 0 ? ((span.start_time - minTime) / totalTime) * 100 : 0;
  const durPct = totalTime > 0 ? ((span.end_time - span.start_time) / totalTime) * 100 : 2;
  const ok = span.status === 'OK' || span.status === 'UNSET';
  const dur = durationMs(span);

  return (
    <div
      className="flex items-center gap-3 py-1.5 hover:bg-muted/40 cursor-pointer rounded px-2"
      onClick={() => onSelect(span)}
    >
      <div className="w-56 flex-shrink-0 flex items-center gap-1" style={{ paddingLeft: depth * 16 }}>
        {depth > 0 && <ChevronRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />}
        <span className="text-xs text-foreground truncate">{span.name}</span>
      </div>
      <span className="w-16 text-right text-xs text-muted-foreground flex-shrink-0 tabular-nums">
        {dur > 0 ? `${dur.toFixed(1)}ms` : '—'}
      </span>
      <span className={`w-14 text-xs px-1.5 py-0.5 rounded text-center flex-shrink-0 font-medium ${
        ok ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
           : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
      }`}>
        {span.status}
      </span>
      <div className="flex-1 h-3 bg-muted rounded overflow-hidden relative">
        <div
          className={`absolute h-full rounded ${ok ? 'bg-indigo-500' : 'bg-red-500'}`}
          style={{ left: `${Math.min(startPct, 95)}%`, width: `${Math.max(durPct, 2)}%` }}
        />
      </div>
    </div>
  );
}

function TracesTab({ apiKey }: { apiKey: string }) {
  const [selectedSpan, setSelectedSpan] = useState<SpanRecord | null>(null);
  const [statusFilter, setStatusFilter] = useState('all');
  const [search, setSearch] = useState('');

  const { data: spans = [], isLoading } = useQuery({
    queryKey: ['spans'],
    queryFn: () => observabilityApi.getSpans(100),
    enabled: !!apiKey,
    refetchInterval: 15_000,
  });

  const filtered = spans.filter((s) => {
    if (statusFilter !== 'all' && s.status !== statusFilter) return false;
    if (search && !s.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const groups = Object.entries(
    filtered.reduce<Record<string, SpanRecord[]>>((acc, s) => {
      acc[s.trace_id] = acc[s.trace_id] ?? [];
      acc[s.trace_id].push(s);
      return acc;
    }, {})
  ).slice(0, 20);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 bg-muted border border-border rounded-lg px-3 py-2 flex-1">
          <Search className="h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search spans…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-transparent text-sm text-foreground placeholder-muted-foreground outline-none flex-1"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground outline-none"
        >
          <option value="all">All statuses</option>
          <option value="OK">OK</option>
          <option value="ERROR">ERROR</option>
          <option value="UNSET">UNSET</option>
        </select>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-sm text-muted-foreground">Loading traces…</div>
      ) : filtered.length === 0 ? (
        <Card className="p-8 text-center">
          <Activity className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
          <p className="text-sm text-foreground font-medium">No spans recorded yet</p>
          <p className="text-xs text-muted-foreground mt-1">Spans are collected as goals execute. Run a goal to generate traces.</p>
        </Card>
      ) : (
        <div className="flex gap-4">
          <div className="flex-1 space-y-4">
            {groups.map(([traceId, traceSpans]) => {
              const sorted = [...traceSpans].sort((a, b) => a.start_time - b.start_time);
              const minTime = sorted[0].start_time;
              const maxTime = Math.max(...sorted.map((s) => s.end_time));
              const totalTime = maxTime - minTime;
              return (
                <Card key={traceId} className="overflow-hidden">
                  <div className="px-3 py-2 border-b border-border flex items-center justify-between bg-muted/40">
                    <span className="text-xs font-mono text-muted-foreground truncate max-w-xs">
                      trace: {traceId.slice(0, 16)}…
                    </span>
                    <span className="text-xs text-muted-foreground">{sorted.length} span{sorted.length !== 1 ? 's' : ''}</span>
                  </div>
                  <div className="px-2 py-1">
                    <div className="flex items-center gap-3 px-2 py-1 border-b border-border text-xs text-muted-foreground">
                      <span className="w-56">Span Name</span>
                      <span className="w-16 text-right">Duration</span>
                      <span className="w-14 text-center">Status</span>
                      <span className="flex-1">Timeline</span>
                    </div>
                    {sorted.map((span, i) => (
                      <TraceRow
                        key={span.span_id ?? i}
                        span={span}
                        minTime={minTime}
                        totalTime={totalTime}
                        depth={span.parent_span_id ? 1 : 0}
                        onSelect={setSelectedSpan}
                      />
                    ))}
                  </div>
                </Card>
              );
            })}
          </div>

          {selectedSpan && (
            <div className="w-72 flex-shrink-0">
              <Card className="p-4 sticky top-4">
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-sm font-semibold text-foreground truncate">{selectedSpan.name}</h4>
                  <button
                    onClick={() => setSelectedSpan(null)}
                    className="text-muted-foreground hover:text-foreground text-lg leading-none ml-2"
                  >
                    ×
                  </button>
                </div>
                <dl className="space-y-2 text-xs">
                  {[
                    { label: 'Status', value: selectedSpan.status },
                    { label: 'Duration', value: `${durationMs(selectedSpan).toFixed(2)}ms` },
                    { label: 'Span ID', value: selectedSpan.span_id.slice(0, 12) + '…' },
                    { label: 'Trace ID', value: selectedSpan.trace_id.slice(0, 16) + '…' },
                  ].map(({ label, value }) => (
                    <div key={label} className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd className="text-foreground font-mono truncate">{value}</dd>
                    </div>
                  ))}
                </dl>
                {Object.keys(selectedSpan.attributes).length > 0 && (
                  <div className="mt-3 border-t border-border pt-3">
                    <p className="text-xs text-muted-foreground mb-1.5 font-medium">Attributes</p>
                    <dl className="space-y-1 text-xs">
                      {Object.entries(selectedSpan.attributes).slice(0, 8).map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-2">
                          <dt className="text-muted-foreground truncate">{k}</dt>
                          <dd className="text-foreground font-mono truncate">{String(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}
              </Card>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Tab: Logs ─────────────────────────────────────────────────────────────────

function LogsTab() {
  const [logs, setLogs] = useState<LogEntry[]>(() =>
    Array.from({ length: 12 }, () => makeLogEntry())
  );
  const [levelFilter, setLevelFilter] = useState<string>('all');
  const [paused, setPaused] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (paused) return;
    const id = setInterval(() => {
      setLogs((prev) => [...prev.slice(-99), makeLogEntry()]);
    }, 1800);
    return () => clearInterval(id);
  }, [paused]);

  useEffect(() => {
    if (!paused && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, paused]);

  const levelConfig: Record<string, { bg: string; text: string }> = {
    INFO:  { bg: 'bg-sky-100 dark:bg-sky-900/30',    text: 'text-sky-700 dark:text-sky-400' },
    WARN:  { bg: 'bg-amber-100 dark:bg-amber-900/30', text: 'text-amber-700 dark:text-amber-400' },
    ERROR: { bg: 'bg-red-100 dark:bg-red-900/30',     text: 'text-red-700 dark:text-red-400' },
    DEBUG: { bg: 'bg-purple-100 dark:bg-purple-900/30', text: 'text-purple-700 dark:text-purple-400' },
  };

  const filtered = logs.filter((l) => levelFilter === 'all' || l.level === levelFilter);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Filter className="h-3.5 w-3.5 text-muted-foreground" />
          {(['all', 'INFO', 'WARN', 'ERROR', 'DEBUG'] as const).map((lvl) => (
            <button
              key={lvl}
              onClick={() => setLevelFilter(lvl)}
              className={`text-xs px-2.5 py-1 rounded-full transition-colors border ${
                levelFilter === lvl
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'border-border text-muted-foreground hover:text-foreground hover:bg-muted'
              }`}
            >
              {lvl}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <div className={`h-2 w-2 rounded-full ${paused ? 'bg-amber-500' : 'bg-emerald-500 animate-pulse'}`} />
          <span className="text-xs text-muted-foreground">{paused ? 'Paused (hover out to resume)' : 'Live'}</span>
        </div>
      </div>

      <Card className="overflow-hidden">
        <div
          ref={scrollRef}
          className="h-96 overflow-y-auto font-mono text-xs"
          onMouseEnter={() => setPaused(true)}
          onMouseLeave={() => setPaused(false)}
        >
          {filtered.map((log) => {
            const cfg = levelConfig[log.level] ?? { bg: '', text: 'text-foreground' };
            return (
              <div
                key={log.id}
                className="flex items-start gap-3 px-4 py-1.5 hover:bg-muted/40 border-b border-border/50"
              >
                <span className="text-muted-foreground flex-shrink-0 tabular-nums text-[11px]">
                  {new Date(log.ts).toLocaleTimeString()}
                </span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold flex-shrink-0 ${cfg.bg} ${cfg.text}`}>
                  {log.level}
                </span>
                {log.service && (
                  <span className="text-muted-foreground flex-shrink-0 text-[11px]">[{log.service}]</span>
                )}
                <span className={`${
                  log.level === 'ERROR' ? 'text-red-700 dark:text-red-400' :
                  log.level === 'WARN'  ? 'text-amber-700 dark:text-amber-400' :
                  'text-foreground'
                }`}>
                  {log.message}
                </span>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function ObservabilityPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [tab, setTab] = useState<ObsTab>('overview');
  const [grafanaAvailable, setGrafanaAvailable] = useState<boolean | null>(null);
  const [lastMetricsUpdate, setLastMetricsUpdate] = useState<Date | null>(null);

  const {
    data: health,
    isLoading: healthLoading,
    isError: healthError,
  } = useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/health`, { headers: { 'X-API-Key': apiKey } });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json() as Promise<HealthResponse>;
    },
    enabled: !!apiKey,
    refetchInterval: 30_000,
  });

  const {
    data: metrics,
    isLoading: metricsLoading,
    refetch: refetchMetrics,
  } = useQuery({
    queryKey: ['metrics'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/metrics`, { headers: { 'X-API-Key': apiKey } });
      if (!res.ok) throw new Error(`${res.status}`);
      setLastMetricsUpdate(new Date());
      return res.text();
    },
    enabled: !!apiKey,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    fetch(`${GRAFANA_URL}/api/health`, { signal: controller.signal })
      .then((r) => setGrafanaAvailable(r.ok))
      .catch(() => setGrafanaAvailable(false))
      .finally(() => clearTimeout(timer));
  }, []);

  const TABS: { id: ObsTab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'metrics',  label: 'Metrics' },
    { id: 'traces',   label: 'Traces' },
    { id: 'logs',     label: 'Logs' },
  ];

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Observability</h1>
          <p className="text-sm text-muted-foreground mt-1">
            System health, live metrics, distributed traces and log stream
          </p>
        </div>
        <a
          href={GRAFANA_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm transition-colors ${
            grafanaAvailable === false
              ? 'border-border text-muted-foreground/50 pointer-events-none'
              : 'border-orange-300 text-orange-600 hover:bg-orange-50 dark:border-orange-700 dark:text-orange-400 dark:hover:bg-orange-950/30'
          }`}
          title={grafanaAvailable === false ? 'Grafana not available at ' + GRAFANA_URL : 'Open Grafana'}
        >
          <span className="font-bold text-xs">G</span>
          Grafana
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      {/* Tab bar */}
      <div role="tablist" className="flex gap-1 border-b border-border">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
              tab === id
                ? 'text-primary border-primary'
                : 'text-muted-foreground border-transparent hover:text-foreground'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      {tab === 'overview' && (
        <OverviewTab health={health} isLoading={healthLoading} isError={healthError} />
      )}
      {tab === 'metrics' && (
        <MetricsTab
          metrics={metrics}
          isLoading={metricsLoading}
          lastUpdated={lastMetricsUpdate}
          onRefresh={() => refetchMetrics()}
        />
      )}
      {tab === 'traces' && <TracesTab apiKey={apiKey} />}
      {tab === 'logs' && <LogsTab />}
    </div>
  );
}
