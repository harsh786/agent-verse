/**
 * BrainFeed — org-brain decisions timeline (audit trail of autonomous ticks).
 *
 * Shows every decision the brain has recorded, newest first — including the
 * ones it chose NOT to execute (blocked by a guardrail). Blocked/held-back
 * rows render with a visually distinct (amber) treatment so the operator can
 * see what the brain declined to do and why, separate from the normal
 * (executed/launched) rows.
 *
 * Each row is expandable to the full ordered SENSE→DECIDE→GUARD→ACT trace —
 * every guardrail check the brain ran, in order, with a pass/fail marker and
 * the numbers behind it (e.g. "$4.80/$5.00", "2/2") — so a held-back decision
 * is never just "blocked", it names exactly which guardrail stopped it.
 */
import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Brain, ShieldAlert, Loader2, AlertTriangle, ChevronDown, Check, X, Link2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { orgAutonomyApi } from '@/features/org/api';
import type { BrainDecision, GuardrailCheck } from '@/features/org/types';
import { useOrgRealtimeManager, ORG_EVENTS, type OrgEvent } from '../OrgRealtimeManager';

interface BrainFeedProps {
  orgId:     string;
  className?: string;
}

const DECISIONS_QUERY_KEY = (orgId: string) => ['org-brain-decisions', orgId] as const;

/** True when the decision reflects something the brain declined to do,
 *  rather than something it executed. */
function isHeldBack(decision: BrainDecision): boolean {
  const verdict = decision.guardrail_verdict?.toLowerCase() ?? '';
  const kind     = decision.kind?.toLowerCase() ?? '';
  const action   = decision.action?.toLowerCase() ?? '';
  return verdict.includes('block') || verdict.includes('deny') || verdict.includes('reject')
    || kind.includes('block') || action.includes('block');
}

/** snake_case guardrail name → readable label, e.g. "daily_cap" → "Daily Cap".
 *  Deliberately generic (no hardcoded per-check lookup) so any check name the
 *  backend adds/renames renders sensibly without a frontend change. */
function humanize(name: string): string {
  return name
    .split('_')
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/** The numbers behind a check, e.g. "2/2" or "$4.80/$5.00" — falls back to
 *  whichever of value/limit is present, or null when neither is. */
function formatCheckNumbers(check: GuardrailCheck): string | null {
  if (check.value && check.limit) return `${check.value}/${check.limit}`;
  return check.value ?? check.limit ?? null;
}

/** The first failing check in an ordered trace — the guardrail that actually
 *  stopped this decision — or null when there's no trace to attribute it to. */
function failingCheckOf(decision: BrainDecision): GuardrailCheck | null {
  return decision.guardrail_trace?.find(check => !check.passed) ?? null;
}

/** One-line "which guardrail stopped it" summary for a held-back row.
 *  Prefers the guardrail_trace's failing check; falls back to the legacy
 *  free-text `reason`/`rationale` when no trace is available. */
function heldBackSummary(decision: BrainDecision, failingCheck: GuardrailCheck | null): string | null {
  if (failingCheck) {
    const numbers = formatCheckNumbers(failingCheck);
    return numbers ? `${humanize(failingCheck.name)} · ${numbers}` : humanize(failingCheck.name);
  }
  return decision.reason || decision.rationale || null;
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
  const queryClient = useQueryClient();
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const { data, isLoading, isError, error } = useQuery({
    queryKey: DECISIONS_QUERY_KEY(orgId),
    queryFn:  () => orgAutonomyApi.decisions(orgId, 50),
  });

  // Stable identity (mirrors TeamChannel's handleOrgEvent) so
  // useOrgRealtimeManager doesn't resubscribe on every render.
  const onEvent = useCallback((event: OrgEvent) => {
    if (event.event_type !== ORG_EVENTS.DECISION_RECORDED) return;
    void queryClient.invalidateQueries({ queryKey: DECISIONS_QUERY_KEY(orgId) });
  }, [orgId, queryClient]);

  useOrgRealtimeManager(orgId, { onEvent });

  const toggleExpand = useCallback((id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

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
              const heldBack     = isHeldBack(decision);
              const trace        = decision.guardrail_trace ?? null;
              const hasTrace     = !!trace && trace.length > 0;
              const failingCheck = failingCheckOf(decision);
              const headline     = decision.action || decision.kind;
              const summary      = heldBack
                ? heldBackSummary(decision, failingCheck)
                : (decision.reason || decision.rationale);
              const cost         = formatCost(decision.est_cost_usd);
              const isExpanded   = expandedIds.has(decision.id);
              const missionShort = decision.mission_id ? decision.mission_id.slice(0, 8) : null;

              return (
                <motion.li
                  key={decision.id}
                  layout
                  initial={reduce ? false : { opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 4 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 30, delay: reduce ? 0 : i * 0.02 }}
                  className={cn(
                    'rounded-lg border-l-2 bg-[#0F1623] overflow-hidden',
                    heldBack ? 'border-l-amber-500 bg-amber-500/5' : 'border-l-emerald-500/60',
                  )}
                >
                  <button
                    type="button"
                    onClick={() => toggleExpand(decision.id)}
                    aria-expanded={isExpanded}
                    aria-label={`${headline}${summary ? `: ${summary}` : ''}${heldBack ? ' (held back)' : ''} — show full guardrail trace`}
                    className="w-full text-left px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/70 focus-visible:ring-inset"
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
                      <div className="flex items-center gap-2 shrink-0">
                        <time
                          dateTime={decision.created_at}
                          className="text-[11px] text-[#475569] tabular-nums"
                        >
                          {formatTimestamp(decision.created_at)}
                        </time>
                        <ChevronDown
                          aria-hidden
                          className={cn(
                            'h-3.5 w-3.5 text-[#475569] transition-transform',
                            isExpanded && 'rotate-180',
                          )}
                        />
                      </div>
                    </div>
                    {summary && (
                      <p className={cn(
                        'mt-1 text-[12px] leading-snug',
                        heldBack ? 'text-amber-300/80' : 'text-[#94A3B8]',
                      )}>
                        {summary}
                      </p>
                    )}
                    <div className="mt-1 flex items-center gap-3">
                      {cost && (
                        <span className="text-[11px] text-[#475569] tabular-nums">
                          Est. cost: {cost}
                        </span>
                      )}
                      {missionShort && (
                        <span className="inline-flex items-center gap-1 text-[11px] text-[#64748B]">
                          <Link2 className="h-3 w-3 text-[#00D4FF]" aria-hidden />
                          mission {missionShort}
                        </span>
                      )}
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="border-t border-[#1E2535] bg-[#0B0E14] px-3 py-2.5">
                      {hasTrace ? (
                        <ol className="space-y-1.5" aria-label="Guardrail trace">
                          {trace!.map(check => {
                            const numbers = formatCheckNumbers(check);
                            return (
                              <li key={check.name} className="flex items-start gap-2 text-[11px]">
                                {check.passed ? (
                                  <Check className="mt-0.5 h-3 w-3 flex-shrink-0 text-emerald-500" aria-label="pass" />
                                ) : (
                                  <X className="mt-0.5 h-3 w-3 flex-shrink-0 text-red-500" aria-label="fail" />
                                )}
                                <span className="min-w-0 flex-1">
                                  <span className={cn(
                                    'font-medium',
                                    check.passed ? 'text-[#94A3B8]' : 'text-red-400',
                                  )}>
                                    {humanize(check.name)}
                                  </span>
                                  <span className="text-[#64748B]"> — {check.detail}</span>
                                  {numbers && (
                                    <span className="ml-1 font-mono text-[#64748B]">({numbers})</span>
                                  )}
                                </span>
                              </li>
                            );
                          })}
                        </ol>
                      ) : (
                        <p className="text-[12px] leading-snug text-[#94A3B8]">
                          {decision.rationale || decision.reason || 'No trace recorded for this decision.'}
                        </p>
                      )}
                    </div>
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
