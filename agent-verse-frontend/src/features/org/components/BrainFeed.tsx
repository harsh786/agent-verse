/**
 * BrainFeed — org-brain decisions timeline (audit trail of autonomous ticks).
 *
 * Shows every decision the brain has recorded, newest first — including the
 * ones it chose NOT to execute (blocked by a guardrail). Blocked/held-back
 * rows render with a visually distinct (amber) treatment so the operator can
 * see what the brain declined to do and why, separate from the normal
 * (executed/launched) rows.
 */
import { useQuery } from '@tanstack/react-query';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Brain, ShieldAlert, Loader2, AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { orgAutonomyApi } from '@/features/org/api';
import type { BrainDecision } from '@/features/org/types';

interface BrainFeedProps {
  orgId:     string;
  className?: string;
}

/** True when the decision reflects something the brain declined to do,
 *  rather than something it executed. */
function isHeldBack(decision: BrainDecision): boolean {
  const verdict = decision.guardrail_verdict?.toLowerCase() ?? '';
  const kind     = decision.kind?.toLowerCase() ?? '';
  const action   = decision.action?.toLowerCase() ?? '';
  return verdict.includes('block') || verdict.includes('deny') || verdict.includes('reject')
    || kind.includes('block') || action.includes('block');
}

function formatCost(cost: number | null): string | null {
  if (cost === null || cost === undefined) return null;
  return `$${cost.toFixed(2)}`;
}

function formatTimestamp(iso: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export function BrainFeed({ orgId, className }: BrainFeedProps) {
  const reduce = useReducedMotion();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['org-brain-decisions', orgId],
    queryFn:  () => orgAutonomyApi.decisions(orgId, 50),
  });

  return (
    <section className={cn('flex flex-col gap-3', className)} aria-label="Autonomous decisions">
      <div className="flex items-center gap-2">
        <Brain className="h-3.5 w-3.5 text-[#00D4FF]" aria-hidden />
        <h3 className="text-[11px] font-medium uppercase tracking-[0.08em] text-[#475569]">
          Brain Decisions
        </h3>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-3 text-[13px] text-[#475569]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Loading decisions…
        </div>
      ) : isError ? (
        <div className="flex items-center gap-2 py-3 text-[13px] text-red-400">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          Failed to load decisions{error instanceof Error ? `: ${error.message}` : '.'}
        </div>
      ) : !data || data.length === 0 ? (
        <div className="py-4 text-center">
          <p className="text-[13px] text-[#475569]">No autonomous decisions yet</p>
        </div>
      ) : (
        <ul className="space-y-1.5" aria-label="Recent brain decisions">
          <AnimatePresence mode="popLayout" initial={false}>
            {data.map((decision, i) => {
              const heldBack = isHeldBack(decision);
              const headline = decision.action || decision.kind;
              const detail   = decision.reason || decision.rationale;
              const cost     = formatCost(decision.est_cost_usd);

              return (
                <motion.li
                  key={decision.id}
                  layout
                  initial={reduce ? false : { opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 4 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 30, delay: reduce ? 0 : i * 0.02 }}
                  className={cn(
                    'rounded-lg border-l-2 bg-[#0F1623] px-3 py-2',
                    heldBack ? 'border-l-amber-500 bg-amber-500/5' : 'border-l-emerald-500/60',
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="text-[13px] font-medium text-[#F1F5F9] truncate">
                        {headline}
                      </span>
                      {heldBack && (
                        <span
                          role="status"
                          className="inline-flex items-center gap-1 shrink-0 rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-400"
                        >
                          <ShieldAlert className="h-2.5 w-2.5" aria-hidden />
                          Held back
                        </span>
                      )}
                    </div>
                    <time
                      dateTime={decision.created_at}
                      className="shrink-0 text-[11px] text-[#475569] tabular-nums"
                    >
                      {formatTimestamp(decision.created_at)}
                    </time>
                  </div>
                  {detail && (
                    <p className={cn(
                      'mt-1 text-[12px] leading-snug',
                      heldBack ? 'text-amber-300/80' : 'text-[#94A3B8]',
                    )}>
                      {detail}
                    </p>
                  )}
                  {cost && (
                    <p className="mt-1 text-[11px] text-[#475569] tabular-nums">
                      Est. cost: {cost}
                    </p>
                  )}
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}

export default BrainFeed;
