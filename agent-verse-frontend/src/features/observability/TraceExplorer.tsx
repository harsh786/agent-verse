
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/auth';

const API = import.meta.env.VITE_API_BASE_URL || '';

interface Span {
  span_id: string;
  name: string;
  start_time: number;
  duration_ms: number;
  status: 'ok' | 'error';
  attributes: Record<string, string>;
}

interface Trace {
  trace_id: string;
  goal_id: string;
  goal: string;
  spans: Span[];
  total_cost_usd: number;
  total_tokens: number;
  created_at: string;
}

function SpanRow({ span }: { span: Span }) {
  const color = span.status === 'error' ? 'text-destructive' : 'text-foreground';
  return (
    <div className="flex items-center gap-3 py-1.5 px-3 hover:bg-muted/40 rounded text-xs">
      <span className="text-muted-foreground w-8">{span.duration_ms}ms</span>
      <div
        className="h-2 rounded bg-primary/40"
        style={{ width: `${Math.min(span.duration_ms, 200)}px`, minWidth: 4 }}
      />
      <span className={`${color} flex-1 font-mono`}>{span.name}</span>
    </div>
  );
}

export function TraceExplorer({ goalId }: { goalId?: string }) {
  const apiKey = useAuthStore(s => s.apiKey) || '';

  const { data, isLoading } = useQuery<{ traces: Trace[] }>({
    queryKey: ['traces', goalId],
    queryFn: async () => {
      const url = goalId
        ? `${API}/analytics/observability/traces?goal_id=${goalId}`
        : `${API}/analytics/observability/traces`;
      const res = await fetch(url, { headers: { 'X-API-Key': apiKey } });
      if (!res.ok) return { traces: [] };
      return res.json();
    },
    enabled: !!apiKey,
  });

  const traces = data?.traces || [];

  if (isLoading) return <div className="text-sm text-muted-foreground animate-pulse">Loading traces…</div>;

  if (traces.length === 0) {
    return (
      <div className="rounded-lg border border-dashed bg-card p-6 text-center">
        <p className="text-sm text-muted-foreground">No traces yet</p>
        <p className="text-xs text-muted-foreground mt-1">Traces appear after goals execute with OTel enabled</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {traces.map(trace => (
        <div key={trace.trace_id} className="rounded-lg border bg-card">
          <div className="px-4 py-3 border-b flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-foreground">{trace.goal}</p>
              <p className="text-xs text-muted-foreground">{trace.created_at}</p>
            </div>
            <div className="text-right text-xs text-muted-foreground">
              <p>{trace.total_tokens?.toLocaleString()} tokens</p>
              <p>${trace.total_cost_usd?.toFixed(6)}</p>
            </div>
          </div>
          <div className="py-2">
            {trace.spans?.map(span => <SpanRow key={span.span_id} span={span} />)}
          </div>
        </div>
      ))}
    </div>
  );
}
