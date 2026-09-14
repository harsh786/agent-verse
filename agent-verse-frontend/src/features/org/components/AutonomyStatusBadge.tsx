/**
 * AutonomyStatusBadge — the always-visible "kill switch is right here" pill.
 *
 * Mounted at the very top of the command panel (unlike the full
 * `AutonomyControl` panel further down, which can scroll out of view), this
 * is a compact, calm, always-legible readout of the org's current autonomy
 * mode plus a one-click Pause/Resume. It shares AutonomyControl's exact
 * query key + fetcher (`['org-autonomy', orgId]` / `orgAutonomyApi.get`) so
 * both read the same TanStack Query cache entry — pausing here updates
 * AutonomyControl's panel (and vice versa) without a second network round
 * trip. (A prior bug shipped BudgetGauges with a mismatched key/fn pair
 * against the same cache entry, causing duplicate fetches and drift — this
 * component takes care to match verbatim instead.)
 *
 * Task 12 (Situation Room craft pass) — see
 * .superpowers/sdd/2026-09-14-situation-room-ux/task-12-brief.md.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Pause, Play, ShieldAlert } from 'lucide-react';
import { cn } from '@/lib/utils';
import { orgAutonomyApi } from '@/features/org/api';

interface AutonomyStatusBadgeProps {
  orgId:      string;
  className?: string;
}

// Verbatim match to AutonomyControl's `queryKey`/`queryFn` — see the
// module-level note above. Kept as local constants (rather than importing
// from AutonomyControl, which isn't a shared module) so the intent is
// explicit at the call site; the shape is enforced by AutonomyStatusBadge.test.tsx.
const AUTONOMY_QUERY_KEY = (orgId: string) => ['org-autonomy', orgId] as const;

export function AutonomyStatusBadge({ orgId, className }: AutonomyStatusBadgeProps) {
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: AUTONOMY_QUERY_KEY(orgId),
    queryFn:  () => orgAutonomyApi.get(orgId),
  });

  const mutation = useMutation({
    mutationFn: (paused: boolean) => orgAutonomyApi.patch(orgId, { settings: { paused } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AUTONOMY_QUERY_KEY(orgId) });
    },
  });

  // No data yet (loading, or the query hasn't resolved) — render a quiet
  // placeholder rather than nothing, so the badge's footprint doesn't jump.
  if (!data) {
    return (
      <div
        className={cn(
          'flex items-center gap-2 px-3 py-2 rounded-xl border border-[#1E2535] bg-[#0B0E14] text-[#475569] text-xs',
          className,
        )}
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        Checking autonomy status…
      </div>
    );
  }

  const { autonomy_level, settings } = data;
  const paused = settings.paused;
  const busy = mutation.isPending;

  const handleToggle = () => {
    mutation.mutate(!paused);
  };

  return (
    <div
      role="status"
      aria-label={paused ? 'Org autonomy is paused' : `Org autonomy is active at level ${autonomy_level}`}
      className={cn(
        'flex items-center gap-2 px-3 py-2 rounded-xl border-2 transition-colors',
        paused
          ? 'border-amber-500/60 bg-amber-500/10'
          : autonomy_level >= 3
            ? 'border-[#00D4FF]/40 bg-[#00D4FF]/5'
            : 'border-[#1E2535] bg-[#0B0E14]',
        className,
      )}
    >
      {/* Mode pill */}
      <span
        className={cn(
          'flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] font-bold uppercase tracking-wider shrink-0',
          paused
            ? 'text-amber-300'
            : autonomy_level >= 3
              ? 'text-[#00D4FF]'
              : 'text-[#64748B]',
        )}
      >
        {paused ? (
          <>
            <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
            Paused
          </>
        ) : (
          <>
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                autonomy_level >= 3 ? 'bg-[#00D4FF] animate-pulse' : 'bg-[#64748B]',
              )}
              aria-hidden
            />
            Autonomous · L{autonomy_level}
          </>
        )}
      </span>

      <span className="flex-1 min-w-0 text-[11px] text-[#475569] truncate">
        {paused
          ? 'No autonomous ticks are running.'
          : autonomy_level >= 3
            ? 'The org brain is acting on its own.'
            : 'Observing / recommending only.'}
      </span>

      {/* One-click kill switch */}
      <button
        type="button"
        onClick={handleToggle}
        disabled={busy}
        aria-label={paused ? 'Resume org autonomy' : 'Pause org autonomy'}
        style={{ touchAction: 'manipulation' }}
        className={cn(
          'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold shrink-0 transition-all',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
          'min-h-[32px] border',
          paused
            ? 'bg-emerald-500/10 border-emerald-500/60 text-emerald-400 hover:bg-emerald-500/20'
            : 'bg-red-500/10 border-red-500/60 text-red-400 hover:bg-red-500/20',
          busy && 'opacity-60 cursor-wait',
        )}
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : paused ? (
          <Play className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <Pause className="h-3.5 w-3.5" aria-hidden />
        )}
        {paused ? 'Resume' : 'Pause'}
      </button>
    </div>
  );
}

export default AutonomyStatusBadge;
