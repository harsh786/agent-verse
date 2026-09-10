/**
 * EvalSuggestionsPanel — the self-improvement read surface for a goal.
 *
 * Fetches GET /goals/:id/eval/suggestions (real eval scores → each dimension
 * below the config-driven pass threshold, worst first) and renders actionable
 * suggestions. Honest empty state when every dimension passes; nothing is
 * fabricated — the trigger and score are real, the copy points at the lever.
 */
import { useQuery } from '@tanstack/react-query';
import { Lightbulb, CheckCircle2 } from 'lucide-react';

import { goalsApi, type EvalSuggestions } from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';

export function EvalSuggestionsPanel({ goalId, enabled }: { goalId: string; enabled: boolean }) {
  const { data, isLoading } = useQuery<EvalSuggestions>({
    queryKey: ['eval-suggestions', goalId],
    queryFn: () => goalsApi.getEvalSuggestions(goalId),
    enabled: enabled && !!goalId,
    staleTime: 15_000,
  });

  if (!enabled) return null;
  if (isLoading) return <Skeleton className="h-20 w-full rounded-xl" />;
  if (!data || data.status === 'not_evaluated') return null;

  return (
    <section className="rounded-xl border bg-card overflow-hidden" aria-label="Improvement suggestions">
      <div className="flex items-center gap-2 px-4 py-3 border-b bg-muted/30">
        <Lightbulb className="h-3.5 w-3.5 text-amber-500" aria-hidden="true" />
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Improvement suggestions
        </h4>
        {data.pass_threshold != null && (
          <span className="ml-auto text-[10px] text-muted-foreground tabular-nums">
            below pass threshold {Math.round(data.pass_threshold * 100)}%
          </span>
        )}
      </div>

      {data.suggestions.length === 0 ? (
        <div className="flex items-center gap-2 px-4 py-4 text-sm text-muted-foreground">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden="true" />
          Every dimension passed — no improvements suggested.
        </div>
      ) : (
        <ul className="divide-y">
          {data.suggestions.map((s) => (
            <li key={s.dimension} className="px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold capitalize">{s.dimension.replace(/_/g, ' ')}</span>
                <span className="text-[10px] font-bold tabular-nums text-red-600 dark:text-red-400">
                  {Math.round(s.score * 100)}%
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{s.suggestion}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
