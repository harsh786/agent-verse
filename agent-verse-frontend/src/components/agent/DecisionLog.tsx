/**
 * DecisionLog — shows an agent's reasoning decisions with type, outcome, and confidence.
 * Used in governance, audit, and agent detail pages.
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Brain, CheckCircle2, XCircle, AlertTriangle, ChevronRight, Filter } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';
import { JARVISStagger, JARVISStaggerItem, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { EmptyState } from '@/components/ui/EmptyState';

type DecisionOutcome = 'approved' | 'rejected' | 'overridden' | 'pending';

interface Decision {
  id: string;
  goal_id: string;
  step: string;
  reasoning: string;
  outcome: DecisionOutcome;
  confidence: number;      // 0–1
  risk_level: 'low' | 'medium' | 'high';
  created_at: string;
  overridden_by?: string;
}

interface DecisionLogProps {
  agentId?: string;
  goalId?: string;
  limit?: number;
  className?: string;
}

const OUTCOME_CONFIG: Record<DecisionOutcome, { color: string; icon: typeof CheckCircle2; label: string }> = {
  approved:   { color: '#00E676', icon: CheckCircle2,  label: 'Approved' },
  rejected:   { color: '#FF3366', icon: XCircle,       label: 'Rejected' },
  overridden: { color: '#FFB300', icon: AlertTriangle, label: 'Overridden' },
  pending:    { color: '#00D4FF', icon: Brain,         label: 'Pending' },
};

const RISK_BADGE: Record<string, string> = {
  low:    'bg-[#00E676]/10 text-[#00E676]',
  medium: 'bg-[#FFB300]/10 text-[#FFB300]',
  high:   'bg-[#FF3366]/10 text-[#FF3366]',
};

function useDecisions(agentId?: string, goalId?: string, limit = 20) {
  const params = new URLSearchParams();
  if (agentId) params.set('agent_id', agentId);
  if (goalId)  params.set('goal_id', goalId);
  params.set('limit', String(limit));

  return useQuery<Decision[]>({
    queryKey: ['decisions', agentId, goalId, limit],
    queryFn: () => apiRequest<Decision[]>('GET', `/v1/governance/decisions?${params}`),
    staleTime: 30_000,
  });
}

export function DecisionLog({ agentId, goalId, limit = 20, className = '' }: DecisionLogProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [outcomeFilter, setOutcomeFilter] = useState<DecisionOutcome | 'all'>('all');

  const { data: decisions = [], isLoading } = useDecisions(agentId, goalId, limit);

  const filtered = outcomeFilter === 'all' ? decisions : decisions.filter(d => d.outcome === outcomeFilter);

  return (
    <div className={`bg-[#0F1826] border border-white/[0.08] rounded-xl overflow-hidden ${className}`} role="region" aria-label="Decision log">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.06]">
        <Brain className="h-4 w-4 text-[#6366F1]" aria-hidden />
        <span className="text-[13px] font-semibold text-[#F0F6FF]">Decision Log</span>

        {/* Filter */}
        <div className="ml-auto flex items-center gap-1.5">
          <Filter size={11} className="text-[#5A7494]" aria-hidden />
          <select
            value={outcomeFilter}
            onChange={e => setOutcomeFilter(e.target.value as DecisionOutcome | 'all')}
            aria-label="Filter by outcome"
            className="bg-transparent text-[11px] text-[#A0B4CC] focus:outline-none cursor-pointer"
          >
            <option value="all">All outcomes</option>
            {(Object.keys(OUTCOME_CONFIG) as DecisionOutcome[]).map(o => (
              <option key={o} value={o}>{OUTCOME_CONFIG[o].label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* List */}
      <div className="overflow-y-auto max-h-80">
        {isLoading ? (
          <div className="flex flex-col gap-2 p-4">
            {[1,2,3].map(i => <div key={i} className="h-12 rounded-lg bg-white/[0.04] animate-pulse" />)}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<Brain size={32} />}
            title="No decisions logged"
            description="Decisions appear as the agent executes goals"
            variant="float"
          />
        ) : (
          <JARVISStagger className="divide-y divide-white/[0.04]">
            {filtered.map(d => {
              const cfg = OUTCOME_CONFIG[d.outcome];
              const OutcomeIcon = cfg.icon;
              const isExpanded = expandedId === d.id;
              return (
                <JARVISStaggerItem key={d.id} interactive>
                  <button
                    className="w-full text-left px-4 py-3 hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-inset focus-visible:ring-1 focus-visible:ring-[#00D4FF]/40"
                    onClick={() => setExpandedId(isExpanded ? null : d.id)}
                    aria-expanded={isExpanded}
                  >
                    <div className="flex items-center gap-2">
                      <OutcomeIcon size={13} style={{ color: cfg.color }} aria-hidden />
                      <span className="flex-1 text-[12px] text-[#F0F6FF] truncate">{d.step}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${RISK_BADGE[d.risk_level]}`}>
                        {d.risk_level}
                      </span>
                      <span className="text-[10px] text-[#5A7494] tabular-nums">{Math.round(d.confidence * 100)}%</span>
                      <ChevronRight
                        size={12}
                        className={`text-[#5A7494] transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                        aria-hidden
                      />
                    </div>

                    <AnimatePresence>
                      {isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={SPRING_FAST}
                          className="overflow-hidden"
                        >
                          <p className="mt-2 text-[11px] text-[#A0B4CC] leading-relaxed">
                            {d.reasoning}
                          </p>
                          {d.overridden_by && (
                            <p className="mt-1 text-[11px] text-[#FFB300]">
                              Overridden by: {d.overridden_by}
                            </p>
                          )}
                          <time dateTime={d.created_at} className="mt-1 block text-[10px] text-[#5A7494]">
                            {new Intl.DateTimeFormat('en', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(d.created_at))}
                          </time>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </button>
                </JARVISStaggerItem>
              );
            })}
          </JARVISStagger>
        )}
      </div>
    </div>
  );
}
