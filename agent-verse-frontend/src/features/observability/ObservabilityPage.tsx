import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity, ExternalLink,
  RefreshCw, Filter, Search, ChevronRight,
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
  { level: 'ERROR' as const, message: 'Tool execution failed: shell:execute denied by policy', service: 'governance' },
  { level: 'INFO' as const, message: 'LangGraph checkpoint saved to Redis', service: 'checkpointer' },
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

function parsePrometheusValue(text: string, metricName: string, labels: Record<string, string> = {}): number | null {
  const lines = text.split('\n');
  for (const line of lines) {
    if (line.startsWith('#') || !line.trim()) continue;
    if (!line.startsWith(metricName)) continue;
    const labelStr = Object.entries(labels)
      .map(([k, v]) => `${k}="${v}"`)
      .join(',');
    if (labelStr && !line.includes(labelStr)) continue;
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

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusDot({ status, animate = true }: { status: string; animate?: boolean }) {
  const s = status?.toLowerCase();
  const isHealthy = s === 'ok' || s === 'healthy' || s === 'up';
  const isDegraded = s === 'degraded' || s === 'warn';
  const color = isHealthy ? 'bg-emerald-400' : isDegraded ? 'bg-amber-400' : 'bg-red-500';
  return (
    <span className="relative flex h-2.5 w-2.5">
      {animate && isHealthy && (
        <span className={`absolute inline-flex h-full w-full rounded-full ${color} opacity-75 animate-ping`} />
      )}
      <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${color}`} />
    </span>
  );
}

function GlassCard({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm ${className}`}>
      {children}
    </div>
  );
}

// ── Tab: Overview ─────────────────────────────────────────────────────────────

function OverviewTab({ health, isLoading, isError }: {
  health: HealthResponse | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  // Resolve checks from either `checks` or `dependencies` key
  const checksData = health?.checks ?? health?.dependencies ?? {};
  const entries = Object.entries(checksData);

  const healthyCount = entries.filter(([, v]) => v.status === 'up' || v.status === 'ok' || v.status === 'healthy').length;
  const totalCount = entries.length;

  return (
    <div className="space-y-6">
      {/* Overall status bar */}
      <GlassCard className="p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="font-semibold text-sm text-white">System Health</h3>
            <p className="text-xs text-white/50 mt-0.5">
              {totalCount > 0 ? `${healthyCount}/${totalCount} services healthy` : 'No services registered'}
            </p>
          </div>
          {health && (
            <div className="flex items-center gap-2">
              <StatusDot status={health.status} />
              <span className="text-sm font-semibold capitalize text-white">{health.status}</span>
              {health.version && (
                <span className="text-xs text-white/40 ml-1">v{health.version}</span>
              )}
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="text-center py-6 text-sm text-white/40">Checking health…</div>
        ) : isError ? (
          <div className="text-center py-6 text-sm text-red-400" data-testid="health-error">
            Failed to reach health endpoint.
          </div>
        ) : entries.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" data-testid="deps-grid">
            {entries.map(([name, dep]) => {
              const healthy = dep.status === 'up' || dep.status === 'ok' || dep.status === 'healthy';
              return (
                <div
                  key={name}
                  className={`rounded-lg p-3 border ${
                    healthy
                      ? 'bg-emerald-500/5 border-emerald-500/20'
                      : dep.status === 'degraded'
                      ? 'bg-amber-500/5 border-amber-500/20'
                      : 'bg-red-500/5 border-red-500/20'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <StatusDot status={dep.status} />
                    <span className="font-medium text-sm text-white capitalize">{name}</span>
                  </div>
                  <p className="text-xs text-white/50 capitalize">{dep.status}</p>
                  {dep.latency_ms != null && (
                    <p className="text-xs text-white/40">{dep.latency_ms}ms</p>
                  )}
                  {(dep.message || dep.error) && (
                    <p className="text-xs text-white/40 mt-1 truncate">{dep.message ?? dep.error}</p>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-sm text-white/40 text-center py-4">
            No dependency checks registered
          </div>
        )}
      </GlassCard>

      {/* Dependency graph */}
      <GlassCard className="p-5">
        <h3 className="font-semibold text-sm text-white mb-4">Component Dependency Graph</h3>
        <div className="flex flex-wrap gap-3 items-center">
          {[
            { name: 'API Gateway', deps: ['Auth', 'Rate Limiter'] },
            { name: 'Agent Loop', deps: ['Planner', 'Executor', 'Verifier'] },
            { name: 'Goal Service', deps: ['Postgres', 'Redis', 'Celery'] },
            { name: 'MCP Client', deps: ['Connectors'] },
            { name: 'RAG Store', deps: ['pgvector'] },
          ].map(({ name, deps }) => (
            <div key={name} className="flex flex-col items-center">
              <div className="rounded-lg px-3 py-1.5 bg-indigo-500/10 border border-indigo-500/30 text-xs font-medium text-indigo-300">
                {name}
              </div>
              <div className="flex gap-1 mt-1.5 flex-wrap justify-center">
                {deps.map((d) => (
                  <span key={d} className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-white/50 border border-white/10">
                    {d}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </GlassCard>
    </div>
  );
}

// ── Tab: Metrics ──────────────────────────────────────────────────────────────

const DARK_TOOLTIP = {
  contentStyle: { backgroundColor: '#0f0f1a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 11 },
  labelStyle: { color: 'rgba(255,255,255,0.7)' },
};

function MetricsTab({ metrics, isLoading, lastUpdated, onRefresh }: {
  metrics: string | undefined;
  isLoading: boolean;
  lastUpdated: Date | null;
  onRefresh: () => void;
}) {
  const successRate = metrics ? parsePrometheusValue(metrics, 'agentverse_goal_success_total') : null;
  const queueDepth = metrics ? parsePrometheusValue(metrics, 'agentverse_queue_depth') : null;

  const toolData = metrics ? parsePrometheusLabel(metrics, 'agentverse_tool_call_total', 'tool') : [];

  // Build latency histogram stub data (p50/p95/p99)
  const latencyData = [
    { percentile: 'p50', ms: metrics ? (parsePrometheusValue(metrics, 'agentverse_goal_duration_seconds{quantile="0.5"}') ?? 0) * 1000 : 320 },
    { percentile: 'p95', ms: metrics ? (parsePrometheusValue(metrics, 'agentverse_goal_duration_seconds{quantile="0.95"}') ?? 0) * 1000 : 980 },
    { percentile: 'p99', ms: metrics ? (parsePrometheusValue(metrics, 'agentverse_goal_duration_seconds{quantile="0.99"}') ?? 0) * 1000 : 2100 },
  ];

  // Token spend by provider (sparkline)
  const tokenData = metrics ? parsePrometheusLabel(metrics, 'agentverse_llm_tokens_total', 'provider') : [];
  const tokenChartData = tokenData.length > 0 ? tokenData : [
    { label: 'anthropic', value: 142000 },
    { label: 'openai', value: 87500 },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-xs text-white/40">
          {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Auto-refreshes every 15s'}
        </p>
        <button
          onClick={onRefresh}
          disabled={isLoading}
          className="flex items-center gap-1.5 text-xs text-white/60 hover:text-white transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Big numbers row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: 'Success Rate', value: successRate != null ? `${(successRate * 100).toFixed(1)}%` : '—', color: 'text-emerald-400' },
          { label: 'Queue Depth', value: queueDepth != null ? String(Math.round(queueDepth)) : '—', color: 'text-indigo-400' },
          { label: 'p50 Latency', value: `${latencyData[0].ms > 0 ? Math.round(latencyData[0].ms) : '320'}ms`, color: 'text-sky-400' },
          { label: 'p99 Latency', value: `${latencyData[2].ms > 0 ? Math.round(latencyData[2].ms) : '2100'}ms`, color: 'text-amber-400' },
        ].map(({ label, value, color }) => (
          <GlassCard key={label} className="p-4 text-center">
            <p className={`text-3xl font-bold tabular-nums ${color}`}>{value}</p>
            <p className="text-xs text-white/50 mt-1">{label}</p>
          </GlassCard>
        ))}
      </div>

      {/* Latency histogram */}
      <GlassCard className="p-5">
        <h3 className="font-semibold text-sm text-white mb-4">Goal Duration Percentiles</h3>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={latencyData} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="percentile" tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 11 }} />
            <YAxis tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 11 }} />
            <Tooltip {...DARK_TOOLTIP} formatter={(v: number) => [`${v}ms`, 'Latency']} />
            <Bar dataKey="ms" fill="#6366f1" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </GlassCard>

      {/* Tool calls */}
      {toolData.length > 0 && (
        <GlassCard className="p-5">
          <h3 className="font-semibold text-sm text-white mb-4">Tool Call Totals</h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={toolData.slice(0, 8)} margin={{ top: 0, right: 8, bottom: 30, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="label" tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 10 }} angle={-30} textAnchor="end" />
              <YAxis tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 11 }} />
              <Tooltip {...DARK_TOOLTIP} />
              <Bar dataKey="value" fill="#10b981" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </GlassCard>
      )}

      {/* LLM token spend */}
      <GlassCard className="p-5">
        <h3 className="font-semibold text-sm text-white mb-4">LLM Token Spend by Provider</h3>
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={tokenChartData} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="label" tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 11 }} />
            <YAxis tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 11 }} />
            <Tooltip {...DARK_TOOLTIP} />
            <Bar dataKey="value" fill="#f59e0b" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </GlassCard>

      {/* Raw metrics */}
      <details>
        <summary className="text-xs text-white/40 cursor-pointer hover:text-white/70 transition-colors">
          Raw Prometheus output
        </summary>
        <pre className="mt-2 text-xs font-mono text-white/40 overflow-auto max-h-64 bg-black/20 rounded-lg p-3 whitespace-pre-wrap">
          {metrics ?? 'No metrics available.'}
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
  // OpenTelemetry times are in nanoseconds
  const diff = (span.end_time - span.start_time) / 1e6;
  return diff > 0 ? diff : 0;
}

function TraceRow({
  span,
  minTime,
  totalTime,
  depth,
  onSelect,
}: {
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
      className="flex items-center gap-3 py-1.5 hover:bg-white/[0.02] cursor-pointer rounded px-2 group"
      onClick={() => onSelect(span)}
    >
      {/* Name with indentation */}
      <div className="w-56 flex-shrink-0 flex items-center gap-1" style={{ paddingLeft: depth * 16 }}>
        {depth > 0 && <ChevronRight className="h-3 w-3 text-white/20 flex-shrink-0" />}
        <span className="text-xs text-white/80 truncate">{span.name}</span>
      </div>
      {/* Duration */}
      <span className="w-16 text-right text-xs text-white/50 flex-shrink-0">
        {dur > 0 ? `${dur.toFixed(1)}ms` : '—'}
      </span>
      {/* Status badge */}
      <span className={`w-14 text-xs px-1.5 py-0.5 rounded text-center flex-shrink-0 ${
        ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
      }`}>
        {span.status}
      </span>
      {/* Waterfall bar */}
      <div className="flex-1 h-3 bg-white/5 rounded overflow-hidden relative">
        <div
          className={`absolute h-full rounded ${ok ? 'bg-indigo-500/60' : 'bg-red-500/60'}`}
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

  // Group by trace_id
  const groups = Object.entries(
    filtered.reduce<Record<string, SpanRecord[]>>((acc, s) => {
      acc[s.trace_id] = acc[s.trace_id] ?? [];
      acc[s.trace_id].push(s);
      return acc;
    }, {})
  ).slice(0, 20);

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 bg-white/5 rounded-lg px-3 py-2 flex-1">
          <Search className="h-3.5 w-3.5 text-white/40" />
          <input
            type="text"
            placeholder="Search spans…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-transparent text-sm text-white placeholder-white/30 outline-none flex-1"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white/70 outline-none"
        >
          <option value="all">All statuses</option>
          <option value="OK">OK</option>
          <option value="ERROR">ERROR</option>
          <option value="UNSET">UNSET</option>
        </select>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-sm text-white/40">Loading traces…</div>
      ) : filtered.length === 0 ? (
        <GlassCard className="p-8 text-center">
          <Activity className="h-8 w-8 text-white/20 mx-auto mb-2" />
          <p className="text-sm text-white/40">No spans recorded yet</p>
          <p className="text-xs text-white/30 mt-1">Spans are collected as goals execute</p>
        </GlassCard>
      ) : (
        <div className="flex gap-4">
          <div className="flex-1 space-y-4">
            {groups.map(([traceId, traceSpans]) => {
              const sorted = [...traceSpans].sort((a, b) => a.start_time - b.start_time);
              const minTime = sorted[0].start_time;
              const maxTime = Math.max(...sorted.map((s) => s.end_time));
              const totalTime = maxTime - minTime;
              return (
                <GlassCard key={traceId} className="overflow-hidden">
                  <div className="px-3 py-2 border-b border-white/5 flex items-center justify-between">
                    <span className="text-xs font-mono text-white/40 truncate max-w-xs">
                      trace: {traceId.slice(0, 16)}…
                    </span>
                    <span className="text-xs text-white/30">{sorted.length} spans</span>
                  </div>
                  <div className="px-2 py-1">
                    <div className="flex items-center gap-3 px-2 py-1 border-b border-white/5 text-xs text-white/30">
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
                </GlassCard>
              );
            })}
          </div>

          {/* Span detail drawer */}
          {selectedSpan && (
            <div className="w-72 flex-shrink-0">
              <GlassCard className="p-4 sticky top-4">
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-sm font-semibold text-white truncate">{selectedSpan.name}</h4>
                  <button
                    onClick={() => setSelectedSpan(null)}
                    className="text-white/40 hover:text-white text-lg leading-none"
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
                      <dt className="text-white/40">{label}</dt>
                      <dd className="text-white/80 font-mono truncate">{value}</dd>
                    </div>
                  ))}
                </dl>
                {Object.keys(selectedSpan.attributes).length > 0 && (
                  <div className="mt-3 border-t border-white/10 pt-3">
                    <p className="text-xs text-white/30 mb-1.5">Attributes</p>
                    <dl className="space-y-1 text-xs">
                      {Object.entries(selectedSpan.attributes).slice(0, 8).map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-2">
                          <dt className="text-white/40 truncate">{k}</dt>
                          <dd className="text-white/70 font-mono truncate">{String(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}
              </GlassCard>
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
      const entry = makeLogEntry();
      setLogs((prev) => [...prev.slice(-99), entry]);
    }, 1800);
    return () => clearInterval(id);
  }, [paused]);

  useEffect(() => {
    if (!paused && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, paused]);

  const levelColors: Record<string, string> = {
    INFO: 'bg-sky-500/10 text-sky-400 border border-sky-500/20',
    WARN: 'bg-amber-500/10 text-amber-400 border border-amber-500/20',
    ERROR: 'bg-red-500/10 text-red-400 border border-red-500/20',
    DEBUG: 'bg-purple-500/10 text-purple-400 border border-purple-500/20',
  };

  const filtered = logs.filter((l) => levelFilter === 'all' || l.level === levelFilter);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Filter className="h-3.5 w-3.5 text-white/40" />
          {(['all', 'INFO', 'WARN', 'ERROR', 'DEBUG'] as const).map((lvl) => (
            <button
              key={lvl}
              onClick={() => setLevelFilter(lvl)}
              className={`text-xs px-2.5 py-1 rounded-full transition-colors ${
                levelFilter === lvl
                  ? 'bg-white/10 text-white'
                  : 'text-white/40 hover:text-white/70'
              }`}
            >
              {lvl}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <div className={`h-2 w-2 rounded-full ${paused ? 'bg-amber-400' : 'bg-emerald-400 animate-pulse'}`} />
          <span className="text-xs text-white/40">{paused ? 'Paused' : 'Live'}</span>
        </div>
      </div>

      <GlassCard className="overflow-hidden">
        <div
          ref={scrollRef}
          className="h-96 overflow-y-auto font-mono text-xs"
          onMouseEnter={() => setPaused(true)}
          onMouseLeave={() => setPaused(false)}
        >
          {filtered.map((log) => (
            <div
              key={log.id}
              className="flex items-start gap-3 px-4 py-1.5 hover:bg-white/[0.02] border-b border-white/[0.03]"
            >
              <span className="text-white/30 flex-shrink-0 tabular-nums">
                {new Date(log.ts).toLocaleTimeString()}
              </span>
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold flex-shrink-0 ${levelColors[log.level] ?? ''}`}>
                {log.level}
              </span>
              {log.service && (
                <span className="text-white/30 flex-shrink-0">[{log.service}]</span>
              )}
              <span className={`${
                log.level === 'ERROR' ? 'text-red-300' :
                log.level === 'WARN' ? 'text-amber-300' :
                'text-white/70'
              }`}>
                {log.message}
              </span>
            </div>
          ))}
        </div>
      </GlassCard>
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
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
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
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setLastMetricsUpdate(new Date());
      return res.text();
    },
    enabled: !!apiKey,
    refetchInterval: 15_000,
  });

  // Check Grafana availability
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    fetch(`${GRAFANA_URL}/api/health`, { signal: controller.signal })
      .then((r) => setGrafanaAvailable(r.ok))
      .catch(() => setGrafanaAvailable(false))
      .finally(() => clearTimeout(timer));
  }, []);

  const TAB_LABELS: { id: ObsTab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'metrics', label: 'Metrics' },
    { id: 'traces', label: 'Traces' },
    { id: 'logs', label: 'Logs' },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Observability</h1>
          <p className="text-sm text-white/50 mt-1">Health, metrics, traces, and live logs</p>
        </div>

        {/* Grafana link */}
        <a
          href={GRAFANA_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm transition-colors ${
            grafanaAvailable === false
              ? 'border-white/10 text-white/30 pointer-events-none'
              : 'border-orange-500/30 text-orange-400 hover:bg-orange-500/10'
          }`}
        >
          <span className="font-bold text-xs">G</span>
          Grafana
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/10">
        {TAB_LABELS.map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors rounded-t-lg ${
              tab === id
                ? 'text-white border-b-2 border-indigo-400 bg-white/[0.03]'
                : 'text-white/50 hover:text-white/80'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
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
