/**
 * HeuristicDraftConfirm — shown when POST /agents/create answered 502 because
 * the agent-designer LLM failed. The backend created NOTHING and returned a
 * name-and-command guess ("heuristic draft"). The user sees exactly what would
 * be created and must explicitly choose "Create anyway", which resends the
 * request with `accept_heuristic: true`.
 */
import { AlertTriangle, Loader2 } from 'lucide-react';
import type { HeuristicAgentDraft } from '@/lib/api/client';

interface Props {
  draft: HeuristicAgentDraft;
  onConfirm: () => void;
  onCancel: () => void;
  isPending?: boolean;
}

export function HeuristicDraftConfirm({ draft, onConfirm, onCancel, isPending = false }: Props) {
  const d = draft.draft;
  const rows: Array<[string, string]> = [
    ['Name', d.name || '—'],
    ['Goal template', d.goal_template || '—'],
    ['Connectors', d.connectors.length ? d.connectors.join(', ') : 'none'],
    ['Trigger', d.trigger_type || '—'],
    ['Autonomy', d.autonomy_mode || '—'],
  ];
  return (
    <div
      role="alertdialog"
      aria-labelledby="heuristic-draft-title"
      aria-describedby="heuristic-draft-desc"
      data-testid="heuristic-draft-confirm"
      className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 space-y-3"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0 text-amber-500" aria-hidden="true" />
        <div className="space-y-1">
          <p id="heuristic-draft-title" className="text-sm font-semibold">
            The AI designer couldn&apos;t design this agent — nothing was created
          </p>
          <p id="heuristic-draft-desc" className="text-xs opacity-80">
            {draft.detail} Below is a basic draft guessed from your description, without the
            designer&apos;s tool, trigger or policy choices. Review it before creating it.
          </p>
          {draft.fallbackReason && (
            <p className="text-[11px] opacity-60">Reason: {draft.fallbackReason}</p>
          )}
        </div>
      </div>

      <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="opacity-60">{k}</dt>
            <dd className="font-medium break-words">{v}</dd>
          </div>
        ))}
      </dl>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={isPending}
          className="px-3 py-1.5 border border-border rounded-md text-xs hover:bg-muted/40 disabled:opacity-50"
        >
          Discard draft
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
        >
          {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
          {isPending ? 'Creating…' : 'Create anyway'}
        </button>
      </div>
    </div>
  );
}
