import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/auth';

// Mirrors the backend GET /observability/goals/{id}/trace (RunTimelineSpanProcessor).
interface TraceEntry {
  name: string;
  start_ns: number;
  duration_ms: number;
  role: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  tool: string | null;
  status: string | null;
  trace_id: string;
  span_id: string;
}

interface GoalTrace {
  goal_id: string;
  entries: TraceEntry[];
  summary: {
    steps: number;
    generations: number;
    total_cost_usd: number;
    total_input_tokens: number;
    total_output_tokens: number;
  };
}

type Kind = 'generation' | 'tool' | 'node';

function kindOf(e: TraceEntry): Kind {
  if (e.name.startsWith('gen_ai')) return 'generation';
  if (e.tool || e.name.includes('tool')) return 'tool';
  return 'node';
}

const KIND_STYLE: Record<Kind, { bar: string; dot: string; label: string }> = {
  generation: { bar: 'bg-purple-500', dot: 'bg-purple-500', label: 'LLM' },
  tool: { bar: 'bg-blue-500', dot: 'bg-blue-500', label: 'Tool' },
  node: { bar: 'bg-slate-400', dot: 'bg-slate-400', label: 'Step' },
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold text-foreground tabular-nums">{value}</div>
    </div>
  );
}

function TimelineRow({ entry, maxMs }: { entry: TraceEntry; maxMs: number }) {
  const kind = kindOf(entry);
  const style = KIND_STYLE[kind];
  const widthPct = maxMs > 0 ? Math.max(2, (entry.duration_ms / maxMs) * 100) : 2;
  const failed = entry.status && entry.status !== 'OK' && entry.status !== 'UNSET';
  const tokens =
    (entry.input_tokens ?? 0) + (entry.output_tokens ?? 0) || null;
  return (
    <div className="jarvis-rise-in flex items-center gap-3 border-b border-border/60 py-2 last:border-0">
      <span className={`h-2 w-2 shrink-0 rounded-full ${style.dot}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-foreground">
            {entry.role || entry.tool || entry.name}
          </span>
          <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {style.label}
          </span>
          {entry.model && (
            <span className="truncate font-mono text-[11px] text-muted-foreground">{entry.model}</span>
          )}
          {failed && (
            <span className="shrink-0 rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-medium text-red-500">
              {entry.status}
            </span>
          )}
        </div>
        {/* latency bar (proportional to the slowest step) */}
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div className={`h-full rounded-full ${style.bar}`} style={{ width: `${widthPct}%` }} />
        </div>
      </div>
      <div className="shrink-0 text-right">
        <div className="text-sm tabular-nums text-foreground">{entry.duration_ms.toFixed(0)} ms</div>
        <div className="text-[11px] tabular-nums text-muted-foreground">
          {tokens != null ? `${tokens} tok` : ''}
          {entry.cost_usd ? ` · $${entry.cost_usd.toFixed(5)}` : ''}
        </div>
      </div>
    </div>
  );
}

/**
 * Run Inspector — renders how a goal executed: an ordered timeline of LangGraph
 * steps, LLM generations (model/tokens/cost/latency) and tool calls, each with a
 * proportional latency bar, plus a cost/token summary. Backed by the per-goal
 * span timeline the RunTimelineSpanProcessor captures.
 */
export function GoalRunInspector({ goalId }: { goalId: string }) {
  const apiKey = useAuthStore(s => s.apiKey) || '';
  const { data, isLoading, isError } = useQuery<GoalTrace>({
    queryKey: ['goal-trace', goalId],
    queryFn: async () => {
      const res = await fetch(`/api/observability/goals/${goalId}/trace`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    refetchInterval: 4000, // live-ish while the goal runs
  });

  if (isLoading) {
    return <div className="p-4 text-sm text-muted-foreground">Loading run trace…</div>;
  }
  if (isError) {
    return <div className="p-4 text-sm text-red-500">Failed to load run trace.</div>;
  }
  const entries = data?.entries ?? [];
  if (entries.length === 0) {
    return (
      <div className="p-6 text-center text-sm text-muted-foreground">
        No trace captured yet for this goal.
      </div>
    );
  }
  const maxMs = Math.max(...entries.map(e => e.duration_ms), 0);
  const s = data!.summary;

  return (
    <div className="jarvis-page-in space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Steps" value={String(s.steps)} />
        <Stat label="LLM calls" value={String(s.generations)} />
        <Stat label="Tokens" value={(s.total_input_tokens + s.total_output_tokens).toLocaleString()} />
        <Stat label="Cost" value={`$${s.total_cost_usd.toFixed(5)}`} />
      </div>
      <div className="rounded-lg border border-border bg-card p-3">
        {entries.map((e, i) => (
          <TimelineRow key={`${e.span_id}-${i}`} entry={e} maxMs={maxMs} />
        ))}
      </div>
    </div>
  );
}
