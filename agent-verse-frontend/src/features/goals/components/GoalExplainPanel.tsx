/**
 * GoalExplainPanel — "Why?" tab showing decision traces, model selection,
 * RAG citations, and execution plan provenance.
 */
import { useQuery } from '@tanstack/react-query';
import { Brain, Database, Route, Info } from 'lucide-react';
import { Skeleton } from '@/components/ui/Skeleton';

interface Props { goalId: string; }

interface ExplainData {
  decision_traces: Array<{ action: string; reasoning: string; evidence: string[]; confidence: number }>;
  model_selections: Record<string, string>;
  rag_citations: Array<{ source_url?: string; collection_id?: string; content?: string }>;
  plan: string[];
  status: string;
}

async function fetchExplanation(goalId: string): Promise<ExplainData> {
  const apiKey = (await import('@/stores/auth')).useAuthStore.getState().apiKey;
  const apiBase = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
  const resp = await fetch(`${apiBase}/goals/${goalId}/explain`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!resp.ok) throw new Error(`Explain failed: ${resp.status}`);
  return resp.json();
}

export function GoalExplainPanel({ goalId }: Props) {
  const { data, isLoading, error } = useQuery<ExplainData>({
    queryKey: ['goal-explain', goalId],
    queryFn: () => fetchExplanation(goalId),
    enabled: !!goalId,
  });

  if (isLoading) {
    return (
      <div className="space-y-3" aria-label="Loading explanation" aria-busy="true">
        {[1, 2, 3].map(i => <Skeleton key={i} className="h-16 w-full" />)}
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="text-sm text-muted-foreground text-center py-8" data-testid="explain-empty">
        No explanation available for this goal.
      </div>
    );
  }

  return (
    <div className="space-y-5" data-testid="explain-panel">
      {/* Model selections */}
      {Object.keys(data.model_selections ?? {}).length > 0 && (
        <section aria-labelledby="model-selection-heading">
          <h3 id="model-selection-heading" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5 mb-2">
            <Brain className="h-3.5 w-3.5" aria-hidden /> Model Selection
          </h3>
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(data.model_selections).map(([role, model]) => (
              <div key={role} className="rounded-lg border border-border bg-card p-2 text-xs">
                <p className="text-muted-foreground capitalize">{role}</p>
                <p className="font-mono font-medium truncate" title={model}>{model}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Execution plan */}
      {Array.isArray(data.plan) && data.plan.length > 0 && (
        <section aria-labelledby="plan-heading">
          <h3 id="plan-heading" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5 mb-2">
            <Route className="h-3.5 w-3.5" aria-hidden /> Execution Plan
          </h3>
          <ol className="space-y-1.5" role="list">
            {data.plan.map((step, i) => (
              <li key={i} className="flex items-start gap-2 text-xs">
                <span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-[10px]" aria-hidden>
                  {i + 1}
                </span>
                <span className="text-foreground leading-relaxed">{step}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* RAG citations */}
      {Array.isArray(data.rag_citations) && data.rag_citations.length > 0 && (
        <section aria-labelledby="citations-heading">
          <h3 id="citations-heading" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5 mb-2">
            <Database className="h-3.5 w-3.5" aria-hidden /> Knowledge Sources Used
          </h3>
          <div className="space-y-1.5">
            {data.rag_citations.slice(0, 5).map((c, i) => (
              <div key={i} className="rounded border border-border bg-muted/30 p-2 text-xs">
                <p className="font-medium truncate">{c.source_url || c.collection_id || 'Knowledge base'}</p>
                {c.content && <p className="text-muted-foreground mt-0.5 line-clamp-2">{c.content.slice(0, 120)}…</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Decision traces */}
      {Array.isArray(data.decision_traces) && data.decision_traces.length > 0 && (
        <section aria-labelledby="traces-heading">
          <h3 id="traces-heading" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5 mb-2">
            <Info className="h-3.5 w-3.5" aria-hidden /> Decision Traces
          </h3>
          <div className="space-y-2">
            {data.decision_traces.slice(0, 5).map((t, i) => (
              <div key={i} className="rounded-lg border border-border bg-card p-3 text-xs space-y-1">
                <p className="font-semibold">{t.action}</p>
                <p className="text-muted-foreground leading-relaxed">{t.reasoning}</p>
                {t.confidence !== undefined && (
                  <div className="flex items-center gap-2 mt-1" role="progressbar" aria-valuenow={Math.round(t.confidence * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={`Confidence ${Math.round(t.confidence * 100)}%`}>
                    <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                      <div className="h-full bg-primary rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform]" style={{ width: `${t.confidence * 100}%` }} />
                    </div>
                    <span className="text-muted-foreground tabular-nums">{Math.round(t.confidence * 100)}%</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Empty state */}
      {!data.model_selections && !data.plan?.length && !data.rag_citations?.length && !data.decision_traces?.length && (
        <p className="text-sm text-muted-foreground text-center py-4">No decision data recorded for this goal.</p>
      )}
    </div>
  );
}
