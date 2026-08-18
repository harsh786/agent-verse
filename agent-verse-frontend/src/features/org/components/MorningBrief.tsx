/**
 * MorningBrief — N11: AI-generated executive morning brief.
 *
 * Surfaced in OrgPage sidebar and as a floating panel.
 * Shows: accomplishments, priorities, risks, recommendations, cost.
 *
 * Skills:
 *   frontend-design:   JARVIS briefing card, status hierarchy
 *   emil-design-eng:   spring 280/26 expand, stagger priority items
 *   impeccable-ui:     greeting dominant, health secondary, list tertiary
 *   web-guidelines:    aria-live, role=region, time[datetime]
 *   ui-ux-pro-max:     44px targets, useReducedMotion, actionable items
 */
import { useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Sun, CheckCircle2, AlertCircle, Lightbulb, DollarSign, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

interface BriefPriority {
  urgency: 'critical' | 'warning' | 'info';
  text: string;
}

interface BriefRisk {
  severity: 'high' | 'medium' | 'low';
  text: string;
}

interface MorningBriefData {
  org_id:                  string;
  org_name:                string;
  overall_health:          'healthy' | 'degraded' | 'attention_needed';
  active_missions:         number;
  active_teams:            number;
  pending_approvals:       number;
  priorities:              BriefPriority[];
  risks:                   BriefRisk[];
  items_needing_attention: number;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

function useMorningBrief(orgId: string) {
  return useQuery<MorningBriefData>({
    queryKey: ['org-morning-brief', orgId],
    queryFn: () => apiRequest<MorningBriefData>('GET', `/v1/org/${orgId}/brief/morning`),
    staleTime: 5 * 60_000,   // fresh for 5 min
    retry: 1,
  });
}

// ── Sub-components ────────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_PANEL = { type: 'spring', stiffness: 280, damping: 26 } as const;

const URGENCY_META = {
  critical: { color: 'text-red-400',    bg: 'bg-red-500/10',    icon: AlertCircle },
  warning:  { color: 'text-amber-400',  bg: 'bg-amber-500/10',  icon: AlertCircle },
  info:     { color: 'text-blue-400',   bg: 'bg-blue-500/10',   icon: CheckCircle2 },
} as const;

const HEALTH_COLORS = {
  healthy:          'text-emerald-400',
  degraded:         'text-amber-400',
  attention_needed: 'text-red-400',
} as const;

function PriorityItem({ item, index }: { item: BriefPriority; index: number }) {
  const reduce = useReducedMotion();
  const meta   = URGENCY_META[item.urgency];
  const Icon   = meta.icon;
  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ ...SPRING_FAST, delay: index * 0.06 }}
      className={`flex items-start gap-2.5 p-2.5 rounded-lg ${meta.bg}`}
    >
      <Icon className={`h-4 w-4 flex-shrink-0 mt-0.5 ${meta.color}`} aria-hidden />
      <p className="text-[13px] text-[#E2E8F0] leading-snug">{item.text}</p>
    </motion.div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

interface MorningBriefProps {
  orgId:     string;
  compact?:  boolean;  // true = collapsed card; false = full panel
}

export function MorningBrief({ orgId, compact = false }: MorningBriefProps) {
  const reduce = useReducedMotion();
  const { data: brief, isLoading, refetch, dataUpdatedAt } = useMorningBrief(orgId);
  const [expanded, setExpanded] = useState(!compact);

  const greeting = (() => {
    const h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 18) return 'Good afternoon';
    return 'Good evening';
  })();

  return (
    <section
      aria-label="Executive morning brief"
      aria-live="polite"
      className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl overflow-hidden"
    >
      {/* Header */}
      <motion.button
        type="button"
        onClick={() => setExpanded(x => !x)}
        aria-expanded={expanded}
        aria-label={expanded ? 'Collapse morning brief' : 'Expand morning brief'}
        whileTap={reduce ? {} : { scale: 0.99 }}
        transition={SPRING_FAST}
        style={{ touchAction: 'manipulation' }}
        className="w-full flex items-center gap-3 px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-inset"
      >
        <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0">
          <Sun className="h-4 w-4 text-amber-400" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-[#F1F5F9]">
            {isLoading ? 'Loading brief…' : `${greeting}${brief ? `, ${brief.org_name.split(' ')[0]}` : ''}`}
          </p>
          {brief && (
            <p className={`text-[11px] font-medium ${HEALTH_COLORS[brief.overall_health]}`}>
              {brief.overall_health === 'healthy' ? '● HEALTHY' :
               brief.overall_health === 'degraded' ? '⚠ DEGRADED' : '⚠ NEEDS ATTENTION'}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <motion.button
            type="button"
            onClick={(e) => { e.stopPropagation(); refetch(); }}
            aria-label="Refresh brief"
            whileTap={reduce ? {} : { scale: 0.88 }}
            transition={SPRING_FAST}
            style={{ touchAction: 'manipulation' }}
            className="w-7 h-7 rounded-lg flex items-center justify-center text-[#475569] hover:text-[#94A3B8] hover:bg-[#252B3B] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          </motion.button>
          <motion.div animate={{ rotate: expanded ? 180 : 0 }} transition={SPRING_FAST}>
            <ChevronDown className="h-4 w-4 text-[#475569]" aria-hidden />
          </motion.div>
        </div>
      </motion.button>

      {/* Content */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={SPRING_PANEL}
            style={{ overflow: 'hidden' }}
          >
            <div className="px-4 pb-4 space-y-4 border-t border-[#252B3B]">
              {isLoading ? (
                <div className="py-6 flex items-center justify-center gap-2 text-[#475569] text-[13px]">
                  <RefreshCw className="h-4 w-4 animate-spin" aria-hidden />
                  Generating brief…
                </div>
              ) : brief ? (
                <>
                  {/* Stats row */}
                  <div className="grid grid-cols-3 gap-2 pt-4">
                    {[
                      { label: 'Missions', value: brief.active_missions },
                      { label: 'Teams',    value: brief.active_teams },
                      { label: 'Approvals', value: brief.pending_approvals, warn: brief.pending_approvals > 0 },
                    ].map(({ label, value, warn }) => (
                      <div key={label} className="bg-[#252B3B] rounded-lg p-2.5 text-center">
                        <p className={`text-[18px] font-bold tabular-nums ${warn ? 'text-amber-400' : 'text-[#F1F5F9]'}`}>{value}</p>
                        <p className="text-[11px] text-[#64748B]">{label}</p>
                      </div>
                    ))}
                  </div>

                  {/* Priorities */}
                  {brief.priorities.length > 0 && (
                    <div>
                      <p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider mb-2">Today's Priorities</p>
                      <div className="space-y-1.5">
                        {brief.priorities.map((p, i) => <PriorityItem key={i} item={p} index={i} />)}
                      </div>
                    </div>
                  )}

                  {/* Risks */}
                  {brief.risks.length > 0 && (
                    <div>
                      <p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider mb-2">Risks</p>
                      <div className="space-y-1.5">
                        {brief.risks.map((r, i) => (
                          <div key={i} className="flex items-start gap-2 p-2.5 rounded-lg bg-red-500/5 border border-red-500/10">
                            <AlertCircle className="h-4 w-4 text-red-400 flex-shrink-0 mt-0.5" aria-hidden />
                            <p className="text-[13px] text-[#E2E8F0]">{r.text}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Recommendation */}
                  {brief.items_needing_attention === 0 && (
                    <div className="flex items-start gap-2 p-2.5 rounded-lg bg-emerald-500/5 border border-emerald-500/10">
                      <Lightbulb className="h-4 w-4 text-emerald-400 flex-shrink-0 mt-0.5" aria-hidden />
                      <p className="text-[13px] text-[#94A3B8]">Organisation running smoothly. No immediate action required.</p>
                    </div>
                  )}

                  {/* Timestamp */}
                  {dataUpdatedAt > 0 && (
                    <p className="text-[11px] text-[#374151] text-right">
                      Updated{' '}
                      <time dateTime={new Date(dataUpdatedAt).toISOString()}>
                        {new Intl.DateTimeFormat('en', { hour: 'numeric', minute: '2-digit' }).format(dataUpdatedAt)}
                      </time>
                    </p>
                  )}
                </>
              ) : (
                <div className="py-4 flex items-center gap-2 text-[#475569] text-[13px]">
                  <DollarSign className="h-4 w-4" aria-hidden />
                  Brief unavailable — check API connectivity.
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

// ── Compact summary bar for command center header ─────────────────────────────

export function MorningBriefBadge({ orgId }: { orgId: string }) {
  const { data } = useMorningBrief(orgId);
  if (!data) return null;
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <span className={`font-medium ${HEALTH_COLORS[data.overall_health]}`}>
        {data.overall_health === 'healthy' ? '● Healthy' :
         data.overall_health === 'degraded' ? '⚠ Degraded' : '⚠ Needs Attention'}
      </span>
      {data.pending_approvals > 0 && (
        <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-medium tabular-nums">
          {data.pending_approvals} approvals
        </span>
      )}
    </div>
  );
}

// ── WHY Interaction card (N21) ────────────────────────────────────────────────

interface WhyCardProps {
  action:          string;
  trigger:         string;
  evidence:        string[];
  policy?:         string;
  expectedOutcome: string;
  riskLevel?:      'low' | 'medium' | 'high';
  approvalStatus?: string;
  onAccept?:       () => void;
  onReject?:       () => void;
}

export function WhyCard({
  action, trigger, evidence, policy,
  expectedOutcome, riskLevel = 'low',
  approvalStatus, onAccept, onReject,
}: WhyCardProps) {
  const reduce = useReducedMotion();
  const [open, setOpen] = useState(false);

  return (
    <div className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(x => !x)}
        aria-expanded={open}
        className="w-full flex items-center gap-3 px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-inset"
        style={{ touchAction: 'manipulation' }}
      >
        <div className="w-7 h-7 rounded-lg bg-purple-500/10 flex items-center justify-center flex-shrink-0">
          <ChevronRight className={`h-3.5 w-3.5 text-purple-400 transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[12px] font-semibold text-[#94A3B8] uppercase tracking-wider">Why did this happen?</p>
          <p className="text-[13px] text-[#F1F5F9] truncate">{action}</p>
        </div>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }}
            transition={SPRING_PANEL} style={{ overflow: 'hidden' }}
          >
            <div className="px-4 pb-4 border-t border-[#252B3B] space-y-3 pt-3 text-[13px]">
              <div><p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider mb-1">Trigger</p><p className="text-[#94A3B8]">{trigger}</p></div>
              {evidence.length > 0 && (
                <div>
                  <p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider mb-1">Evidence</p>
                  <ul className="space-y-0.5">
                    {evidence.map((e, i) => <li key={i} className="text-[#94A3B8] flex gap-1.5"><span className="text-[#475569]">•</span>{e}</li>)}
                  </ul>
                </div>
              )}
              {policy && <div><p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider mb-1">Policy</p><p className="text-[#94A3B8]">{policy}</p></div>}
              <div><p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider mb-1">Expected Outcome</p><p className="text-[#94A3B8]">{expectedOutcome}</p></div>
              <div className="flex items-center gap-3 text-[11px]">
                <span className={`px-2 py-0.5 rounded font-medium ${riskLevel === 'low' ? 'bg-emerald-500/10 text-emerald-400' : riskLevel === 'medium' ? 'bg-amber-500/10 text-amber-400' : 'bg-red-500/10 text-red-400'}`}>
                  {riskLevel.toUpperCase()} RISK
                </span>
                {approvalStatus && <span className="text-[#64748B]">{approvalStatus}</span>}
              </div>
              {(onAccept || onReject) && (
                <div className="flex gap-2 pt-1">
                  {onAccept && (
                    <motion.button
                      aria-label="Accept this outcome"
                      whileTap={reduce ? {} : { scale: 0.97 }}
                      transition={SPRING_FAST}
                      onClick={onAccept}
                      style={{ touchAction: 'manipulation' }}
                      className="flex-1 py-2 rounded-lg bg-emerald-600/20 text-emerald-400 text-[12px] font-semibold hover:bg-emerald-600/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/70"
                    >
                      Accept outcome
                    </motion.button>
                  )}
                  {onReject && (
                    <motion.button
                      aria-label="Reject and discuss this decision"
                      whileTap={reduce ? {} : { scale: 0.97 }}
                      transition={SPRING_FAST}
                      onClick={onReject}
                      style={{ touchAction: 'manipulation' }}
                      className="flex-1 py-2 rounded-lg bg-red-500/10 text-red-400 text-[12px] font-semibold hover:bg-red-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/70"
                    >
                      Reject &amp; discuss
                    </motion.button>
                  )}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default MorningBrief;
