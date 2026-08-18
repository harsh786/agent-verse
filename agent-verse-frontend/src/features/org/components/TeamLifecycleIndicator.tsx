/**
 * TeamLifecycleIndicator — P13: Visual lifecycle progression for a team.
 *
 * Shows the current stage (create→staff→brief→execute→review→complete→archive)
 * as an animated horizontal timeline with advance/view actions.
 *
 * Skills:
 *   frontend-design:   JARVIS dark, stage dots + connector lines, glow on active
 *   emil-design-eng:   spring 600/35 advance, pulse on active stage
 *   impeccable-ui:     stage name dominant, description secondary, actions tertiary
 *   web-guidelines:    role=list, aria-current, time[datetime]
 *   ui-ux-pro-max:     44px advance button, useReducedMotion, tooltip on hover
 */
import { useCallback } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { CheckCircle2, ChevronRight, Loader2 } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

type LifecycleState = 'create' | 'staff' | 'brief' | 'execute' | 'review' | 'complete' | 'archive';

interface LifecycleData {
  team_id:      string;
  current_state: LifecycleState;
  next_allowed:  LifecycleState[];
  all_states:    LifecycleState[];
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STAGE_ORDER: LifecycleState[] = ['create', 'staff', 'brief', 'execute', 'review', 'complete', 'archive'];

const STAGE_META: Record<LifecycleState, { label: string; color: string; desc: string }> = {
  create:   { label: 'Create',   color: 'text-[#64748B]',   desc: 'Team entity created' },
  staff:    { label: 'Staff',    color: 'text-blue-400',    desc: 'Agents assigned' },
  brief:    { label: 'Brief',    color: 'text-purple-400',  desc: 'Context loaded' },
  execute:  { label: 'Execute',  color: 'text-amber-400',   desc: 'Mission in progress' },
  review:   { label: 'Review',   color: 'text-orange-400',  desc: 'Outputs reviewed' },
  complete: { label: 'Complete', color: 'text-emerald-400', desc: 'Mission accomplished' },
  archive:  { label: 'Archive',  color: 'text-[#475569]',   desc: 'Team disbanded' },
};

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useTeamLifecycle(orgId: string, teamId: string) {
  return useQuery<LifecycleData>({
    queryKey: ['team-lifecycle', orgId, teamId],
    queryFn:  () => apiRequest<LifecycleData>('GET', `/v1/org/${orgId}/teams/${teamId}/lifecycle`),
    staleTime: 10_000,
    enabled: !!teamId,
  });
}

function useAdvanceLifecycle(orgId: string, teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiRequest('POST', `/v1/org/${orgId}/teams/${teamId}/lifecycle`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['team-lifecycle', orgId, teamId] }),
  });
}

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST = { type: 'spring', stiffness: 600, damping: 35 } as const;

// ── Main Component ─────────────────────────────────────────────────────────────

interface TeamLifecycleIndicatorProps {
  orgId:   string;
  teamId:  string;
  compact?: boolean;
}

export function TeamLifecycleIndicator({ orgId, teamId, compact = false }: TeamLifecycleIndicatorProps) {
  const reduce  = useReducedMotion();
  const { data, isLoading } = useTeamLifecycle(orgId, teamId);
  const advance = useAdvanceLifecycle(orgId, teamId);

  const currentIdx = STAGE_ORDER.indexOf(data?.current_state ?? 'create');
  const canAdvance = (data?.next_allowed ?? []).length > 0;

  const handleAdvance = useCallback(() => advance.mutate(), [advance]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-[#475569] text-[12px]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />Loading lifecycle…
      </div>
    );
  }

  if (!data) return null;

  const current = STAGE_ORDER[currentIdx];
  const meta    = STAGE_META[current];

  if (compact) {
    return (
      <div className="flex items-center gap-2">
        <span className={`text-[11px] font-medium ${meta.color}`}>{meta.label}</span>
        {canAdvance && (
          <motion.button
            whileTap={reduce ? {} : { scale: 0.93 }} transition={SPRING_FAST}
            onClick={handleAdvance}
            disabled={advance.isPending}
            aria-label={`Advance team to ${data.next_allowed[0]}`}
            style={{ touchAction: 'manipulation' }}
            className="flex items-center gap-1 text-[10px] text-blue-400 hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded"
          >
            {advance.isPending ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : <ChevronRight className="h-3 w-3" aria-hidden />}
            {data.next_allowed[0]}
          </motion.button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3" aria-label={`Team lifecycle: currently at ${current}`}>
      {/* Stage timeline */}
      <div role="list" className="flex items-center gap-0">
        {STAGE_ORDER.map((stage, i) => {
          const done   = i < currentIdx;
          const active = i === currentIdx;
          const future = i > currentIdx;
          const sm     = STAGE_META[stage];

          return (
            <div key={stage} role="listitem" aria-current={active ? 'step' : undefined}
              className="flex items-center">
              {/* Stage dot */}
              <div className="flex flex-col items-center gap-1">
                <motion.div
                  animate={active && !reduce ? { scale: [1, 1.15, 1] } : {}}
                  transition={{ duration: 2, repeat: Infinity }}
                  className={`w-6 h-6 rounded-full flex items-center justify-center border-2 flex-shrink-0 ${
                    done   ? 'bg-emerald-500 border-emerald-500' :
                    active ? `border-current ${sm.color} bg-transparent` :
                             'bg-transparent border-[#2D3748]'
                  }`}
                  title={`${sm.label}: ${sm.desc}`}
                >
                  {done ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-white" aria-hidden />
                  ) : (
                    <span className={`text-[9px] font-bold ${active ? sm.color : 'text-[#374151]'}`}>{i + 1}</span>
                  )}
                </motion.div>
                {!compact && (
                  <span className={`text-[9px] font-medium ${active ? sm.color : future ? 'text-[#374151]' : 'text-[#475569]'}`}>
                    {sm.label}
                  </span>
                )}
              </div>

              {/* Connector */}
              {i < STAGE_ORDER.length - 1 && (
                <div className={`h-0.5 w-4 flex-shrink-0 ${done ? 'bg-emerald-500' : 'bg-[#2D3748]'}`} />
              )}
            </div>
          );
        })}
      </div>

      {/* Current stage info */}
      <div className="flex items-center justify-between">
        <div>
          <p className={`text-[13px] font-semibold ${meta.color}`}>{meta.label}</p>
          <p className="text-[11px] text-[#64748B]">{meta.desc}</p>
        </div>
        {canAdvance && (
          <motion.button
            whileTap={reduce ? {} : { scale: 0.97 }} transition={SPRING_FAST}
            onClick={handleAdvance}
            disabled={advance.isPending}
            aria-label={`Advance to ${data.next_allowed[0]}`}
            style={{ touchAction: 'manipulation' }}
            className={[
              'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 min-h-[34px]',
              'bg-blue-500/10 text-blue-400 hover:bg-blue-500/20',
            ].join(' ')}
          >
            {advance.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-label="Advancing" />
            ) : (
              <><ChevronRight className="h-3.5 w-3.5" aria-hidden />→ {data.next_allowed[0]}</>
            )}
          </motion.button>
        )}
        {!canAdvance && current === 'archive' && (
          <span className="text-[11px] text-[#475569]">Terminal state</span>
        )}
      </div>
    </div>
  );
}

export default TeamLifecycleIndicator;
