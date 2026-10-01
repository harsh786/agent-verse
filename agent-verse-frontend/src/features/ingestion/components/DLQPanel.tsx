import { AlertTriangle, RotateCcw } from 'lucide-react';
import { ApiError } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { useIngestionDLQ, useRetryDLQEntry } from '../hooks';

/** Ingestion dead-letter queue: documents that failed to index, with a per-entry retry. */
export function DLQPanel() {
  const { data: entries = [], isLoading, isError } = useIngestionDLQ();
  const retry = useRetryDLQEntry();

  const onRetry = (dlqId: string) =>
    retry.mutate(dlqId, {
      onSuccess: () => toast({ kind: 'success', message: 'Retry queued.' }),
      onError: (e) =>
        toast({
          kind: 'error',
          message: e instanceof ApiError ? `Retry refused: ${e.message}` : 'Retry could not be queued.',
        }),
    });

  return (
    <section className="rounded-xl border border-border bg-card p-4" aria-label="Failed documents">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <AlertTriangle className="h-4 w-4 text-amber-500" />
        Failed documents (DLQ)
        {entries.length > 0 && <span className="rounded-full bg-muted px-2 text-xs">{entries.length}</span>}
      </h2>
      {isLoading && <div className="mt-3 h-16 rounded-lg bg-muted animate-pulse" />}
      {isError && <p className="mt-3 text-sm text-muted-foreground">The DLQ is unavailable.</p>}
      {!isLoading && !isError && entries.length === 0 && (
        <p className="mt-3 text-sm text-muted-foreground">No failed documents.</p>
      )}
      {entries.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {entries.map((entry) => (
            <li key={entry.id} className="flex items-start gap-3 rounded-lg border border-border/50 px-3 py-2 text-xs">
              <div className="flex-1 min-w-0">
                <p className="font-medium font-mono truncate">{entry.doc_id}</p>
                <p className="text-muted-foreground truncate">
                  {entry.failed_stage} · {entry.error_message} · {entry.retry_count} retries
                </p>
              </div>
              <button
                onClick={() => onRetry(entry.id)}
                disabled={retry.isPending && retry.variables === entry.id}
                aria-label={`Retry ${entry.doc_id}`}
                className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 hover:bg-muted disabled:opacity-50"
              >
                <RotateCcw className="h-3 w-3" /> Retry
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
