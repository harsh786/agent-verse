import { RefreshCw, AlertCircle } from 'lucide-react';
import type { TriggerDLQEntry } from '../types';
import { useTriggerDLQ, useRetryDLQEntry } from '../hooks';

export function TriggerDLQPanel() {
  const { data: entries, isLoading, refetch } = useTriggerDLQ();
  const retry = useRetryDLQEntry();

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Failed trigger dispatches that will be retried automatically.
        </p>
        <button
          onClick={() => refetch()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors"
          aria-label="Refresh dead letter queue"
        >
          <RefreshCw className="h-3 w-3" />
          Refresh
        </button>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {[1, 2].map((i) => (
            <div key={i} className="h-14 rounded-xl bg-muted animate-pulse" />
          ))}
        </div>
      )}

      {!isLoading && !entries?.length && (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
          <AlertCircle className="h-10 w-10 mb-3 opacity-20" />
          <p className="text-sm">Dead Letter Queue is empty. ✓</p>
        </div>
      )}

      {entries?.map((entry: TriggerDLQEntry) => (
        <div
          key={entry.id}
          className="rounded-xl border border-border bg-card p-4 flex items-start gap-4"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <code className="text-xs font-mono text-muted-foreground">{entry.trigger_id.slice(0, 12)}…</code>
              <span className="rounded bg-destructive/10 text-destructive text-xs px-1.5 py-0.5 font-medium">
                {entry.failure_type}
              </span>
              <span className="text-xs text-muted-foreground">retry {entry.retry_count}×</span>
            </div>
            <p className="mt-1 text-sm text-destructive/90 truncate">{entry.error_message}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {new Date(entry.created_at).toLocaleString()}
              {entry.next_retry_at && ` · next retry ${new Date(entry.next_retry_at).toLocaleString()}`}
            </p>
          </div>
          <button
            onClick={() => retry.mutate(entry.id)}
            disabled={retry.isPending}
            className="shrink-0 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors disabled:opacity-50"
            aria-label={`Retry DLQ entry ${entry.id}`}
          >
            {retry.isPending ? '…' : 'Retry now'}
          </button>
        </div>
      ))}
    </div>
  );
}
