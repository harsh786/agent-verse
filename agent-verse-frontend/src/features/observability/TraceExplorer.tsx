
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

/** One retrieved chunk, as encoded (JSON) into a span's `retrieved_chunks` attribute. */
interface RetrievedChunk {
  chunk_id: string;
  score: number;
  text: string;
  source?: string;
}

// Well-known attribute keys that get special, structured rendering below —
// everything else in `attributes` still renders as a plain key/value pill so
// no real data is ever silently dropped.
const RETRIEVED_CHUNKS_KEYS = ['retrieved_chunks', 'retrieval.chunks'];
const GROUNDED_CHUNK_KEYS = ['grounded_chunk_id', 'grounding.chunk_id'];

function firstPresentKey(attrs: Record<string, string>, keys: string[]): string | undefined {
  return keys.find(k => attrs[k] !== undefined);
}

/** Parses the JSON-encoded retrieved-chunks attribute. Returns null (render nothing)
 *  on a missing or malformed value — never fabricates chunk data. */
function parseRetrievedChunks(attrs: Record<string, string>): RetrievedChunk[] | null {
  const key = firstPresentKey(attrs, RETRIEVED_CHUNKS_KEYS);
  if (!key) return null;
  try {
    const parsed: unknown = JSON.parse(attrs[key]);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter(
      (c): c is RetrievedChunk => c && typeof c === 'object' && typeof (c as RetrievedChunk).chunk_id === 'string'
    );
  } catch {
    return null;
  }
}

/** Renders a span's retrieval/grounding attributes. Real data only — a span with
 *  no attributes (or none of the retrieval-specific keys) renders nothing. */
function SpanAttributes({ attributes }: { attributes: Record<string, string> | undefined }) {
  if (!attributes || Object.keys(attributes).length === 0) return null;

  const chunks = parseRetrievedChunks(attributes);
  const groundedKey = firstPresentKey(attributes, GROUNDED_CHUNK_KEYS);
  const groundedChunkId = groundedKey ? attributes[groundedKey] : undefined;

  const structuredKeys = new Set([...RETRIEVED_CHUNKS_KEYS, ...GROUNDED_CHUNK_KEYS]);
  const otherEntries = Object.entries(attributes).filter(([k]) => !structuredKeys.has(k));

  if ((!chunks || chunks.length === 0) && otherEntries.length === 0) return null;

  return (
    <div className="ml-11 mr-3 mb-2 pb-2 space-y-2" data-testid="span-attributes">
      {chunks && chunks.length > 0 && (
        <div className="space-y-1" aria-label="Retrieved chunks">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Retrieved chunks
          </p>
          {chunks.map(c => {
            const isGrounded = !!groundedChunkId && c.chunk_id === groundedChunkId;
            return (
              <div
                key={c.chunk_id}
                className={`flex items-start gap-2 rounded px-2 py-1 text-[11px] ${
                  isGrounded ? 'bg-emerald-500/10 border border-emerald-500/30' : 'bg-muted/30'
                }`}
              >
                <span className="font-mono text-muted-foreground shrink-0 w-9 text-right">
                  {Math.round(c.score * 100)}%
                </span>
                <span className="flex-1 min-w-0 truncate text-foreground/90">{c.text}</span>
                {isGrounded && (
                  <span className="text-emerald-500 text-[9px] font-semibold shrink-0 uppercase tracking-wide">
                    Grounded
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
      {otherEntries.length > 0 && (
        <div className="flex flex-wrap gap-1" aria-label="Span attributes">
          {otherEntries.map(([k, v]) => (
            <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-muted/50 text-muted-foreground font-mono">
              {k}: {v}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function SpanRow({ span }: { span: Span }) {
  const color = span.status === 'error' ? 'text-destructive' : 'text-foreground';
  return (
    <div>
      <div className="flex items-center gap-3 py-1.5 px-3 hover:bg-muted/40 rounded text-xs">
        <span className="text-muted-foreground w-8">{span.duration_ms}ms</span>
        <div
          className="h-2 rounded bg-primary/40"
          style={{ width: `${Math.min(span.duration_ms, 200)}px`, minWidth: 4 }}
        />
        <span className={`${color} flex-1 font-mono`}>{span.name}</span>
      </div>
      <SpanAttributes attributes={span.attributes} />
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
