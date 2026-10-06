import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { tenantsApi } from '@/lib/api/client';
import { removalSummary } from './chatTranscriptRemoval';

/**
 * Owner decision 7 — the workspace switch for chat transcripts as knowledge.
 * Off by default; admin only. Each person must also opt in for their own chats
 * (Chat → Agent Memory). Turning it off removes every transcript already indexed.
 */
export function ChatTranscriptsKnowledgeSetting() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ['tenant', 'chat-transcripts-knowledge'],
    queryFn: () => tenantsApi.getChatTranscriptsKnowledge(),
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => tenantsApi.setChatTranscriptsKnowledge(enabled),
    onSuccess: (data) => qc.setQueryData(['tenant', 'chat-transcripts-knowledge'], data),
  });
  const enabled = query.data?.enabled === true;
  const error = query.error ?? toggle.error;
  const summary = removalSummary(toggle.data);

  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <h3 className="text-base font-semibold">Chat transcripts as knowledge</h3>
        <p className="text-sm text-muted-foreground mt-1">
          Lets a knowledge Source index the chats of the people who opt in for their own chats
          (PII and secrets redacted). Off by default. Turning it off removes every transcript
          already indexed.
        </p>
        {summary && (
          <p aria-live="polite" className="text-sm text-muted-foreground mt-1">
            {summary}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400 mt-1">
            {error instanceof Error ? error.message : String(error)}
          </p>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label="Chat transcripts as knowledge"
        disabled={!query.isSuccess || toggle.isPending}
        onClick={() => toggle.mutate(!enabled)}
        className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
          enabled ? 'bg-primary' : 'bg-muted'
        }`}
      >
        <span
          className={`inline-block h-5 w-5 rounded-full bg-background shadow transform transition-transform mt-0.5 ${
            enabled ? 'translate-x-5' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}
