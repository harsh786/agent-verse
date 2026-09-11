/**
 * MissionInspectorPanel — Deep-dive slide-over for selected mission/agent.
 * Spec §3.3: Thought stream, tool timeline, budget burn, subagent tree.
 */
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X, Activity, DollarSign, GitBranch, Brain } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';
import { cn } from '@/lib/utils';

interface MissionInspectorPanelProps {
  orgId:       string;
  missionId?:  string | null;
  agentId?:    string | null;
  onClose:     () => void;
  className?:  string;
}

function useMissionDetail(orgId: string, missionId?: string | null) {
  return useQuery({
    queryKey: ['mission-detail', orgId, missionId],
    queryFn: () => apiFetch<Record<string, unknown>>(`/v1/org/${orgId}/missions/${missionId}`),
    enabled: !!missionId,
    staleTime: 15_000,
  });
}

export function MissionInspectorPanel({ orgId, missionId, agentId, onClose, className }: MissionInspectorPanelProps) {
  const reduce = useReducedMotion();
  const { data: mission } = useMissionDetail(orgId, missionId);

  const isOpen = !!(missionId || agentId);

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.aside
          initial={false}
          animate={{ opacity: 1, x: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, x: '100%' }}
          transition={{ type: 'spring', stiffness: 300, damping: 28 }}
          className={cn(
            'flex flex-col h-full bg-[#0A0F1A] border-l border-white/[0.07] overflow-hidden',
            className,
          )}
          role="complementary"
          aria-label="Mission inspector"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] shrink-0">
            <div className="flex items-center gap-2 min-w-0">
              <Activity className="h-3.5 w-3.5 text-[#00D4FF] shrink-0" aria-hidden />
              <span className="text-[12px] font-semibold text-[#F0F6FF] truncate">
                {mission ? String((mission as any).title ?? 'Mission').slice(0, 40) : agentId ? `Agent ${agentId.slice(-8)}` : 'Inspector'}
              </span>
            </div>
            <button
              onClick={onClose}
              className="p-1 rounded text-[#5A7494] hover:text-[#F0F6FF] hover:bg-white/[0.06] transition-colors"
              aria-label="Close inspector"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
            {/* Status */}
            {mission && (
              <section aria-labelledby="inspector-status">
                <h3 id="inspector-status" className="text-[10px] font-medium text-[#5A7494] uppercase tracking-wide mb-2">Status</h3>
                <div className="flex items-center gap-2">
                  <span className={cn(
                    'px-2 py-0.5 rounded-full text-[10px] font-medium capitalize',
                    String((mission as any).status) === 'active' ? 'bg-[#00D4FF]/10 text-[#00D4FF]' :
                    String((mission as any).status) === 'completed' ? 'bg-[#00E676]/10 text-[#00E676]' :
                    'bg-white/[0.06] text-[#A0B4CC]',
                  )}>
                    {String((mission as any).status ?? 'unknown')}
                  </span>
                  <span className="text-[10px] text-[#5A7494]">{String((mission as any).priority ?? 'medium')} priority</span>
                </div>
              </section>
            )}

            {/* Budget bar */}
            {mission && (mission as any).budget_usd != null && (
              <section aria-labelledby="inspector-budget">
                <h3 id="inspector-budget" className="text-[10px] font-medium text-[#5A7494] uppercase tracking-wide mb-2 flex items-center gap-1">
                  <DollarSign className="h-3 w-3" aria-hidden /> Budget
                </h3>
                <div className="flex items-center justify-between text-[10px] mb-1">
                  <span className="text-[#A0B4CC]">Used</span>
                  <span className="font-mono text-[#F0F6FF] tabular-nums">
                    ${Number((mission as any).actual_cost_usd ?? 0).toFixed(3)} / ${Number((mission as any).budget_usd).toFixed(2)}
                  </span>
                </div>
                <div className="h-1.5 bg-[#162035] rounded-full overflow-hidden">
                  <motion.div
                    className="h-full rounded-full bg-[#00D4FF]"
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.min(100, (Number((mission as any).actual_cost_usd ?? 0) / Math.max(0.001, Number((mission as any).budget_usd))) * 100)}%` }}
                    transition={{ type: 'spring', stiffness: 100, damping: 20 }}
                  />
                </div>
              </section>
            )}

            {/* Objective */}
            {mission && (mission as any).objective && (
              <section aria-labelledby="inspector-obj">
                <h3 id="inspector-obj" className="text-[10px] font-medium text-[#5A7494] uppercase tracking-wide mb-1.5 flex items-center gap-1">
                  <Brain className="h-3 w-3" aria-hidden /> Objective
                </h3>
                <p className="text-[11px] text-[#A0B4CC] leading-relaxed">{String((mission as any).objective).slice(0, 200)}</p>
              </section>
            )}

            {/* Tags */}
            {mission && Array.isArray((mission as any).tags) && (mission as any).tags.length > 0 && (
              <section aria-labelledby="inspector-tags">
                <h3 id="inspector-tags" className="text-[10px] font-medium text-[#5A7494] uppercase tracking-wide mb-1.5 flex items-center gap-1">
                  <GitBranch className="h-3 w-3" aria-hidden /> Departments
                </h3>
                <div className="flex flex-wrap gap-1">
                  {((mission as any).tags as string[]).map(tag => (
                    <span key={tag} className="px-2 py-0.5 rounded-full bg-[#6366F1]/10 text-[#6366F1] text-[9px] font-medium">{tag}</span>
                  ))}
                </div>
              </section>
            )}

            {/* Empty state for agent */}
            {!missionId && agentId && (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <Activity className="h-8 w-8 text-[#334155]" aria-hidden />
                <p className="text-[11px] text-[#5A7494]">Agent {agentId.slice(-8)}</p>
                <p className="text-[10px] text-[#334155]">Select a mission to inspect details</p>
              </div>
            )}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
