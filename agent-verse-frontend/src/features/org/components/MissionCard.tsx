/**
 * MissionCard — JARVIS-style mission entry.
 *
 * Skills applied:
 *   - emil-design-eng:   spring physics (not cubic-bezier), press state 0.98
 *   - impeccable-ui:     one dominant element, 15px body, -0.01em tracking
 *   - web-guidelines:    aria-label, role=button, keyboard nav, tabular-nums,
 *                        touch-manipulation, min-w-0, text-balance
 *   - ui-ux-pro-max:     44×44px targets, prefers-reduced-motion, hover+active
 *   - frontend-design:   JARVIS palette, breathing pulse (signature element)
 */
import React, { useCallback, useId } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import {
  Clock, AlertCircle, CheckCircle2, Zap,
  PauseCircle, XCircle, Circle, ChevronRight,
  Cpu, GitBranch, Loader2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useOrgTasks } from '../hooks/useOrg';
import type { OrgMission, OrgTask, MissionStatus, Priority } from '../types';

interface MissionCardProps {
  mission:    OrgMission;
  /** Org the mission belongs to — needed to fetch real task progress. Omit to skip the progress fetch (e.g. isolated previews). */
  orgId?:     string;
  isSelected?: boolean;
  onClick?:   (id: string) => void;
  className?: string;
}

// ─── Design tokens (frontend-design: JARVIS identity) ───────────────────────

const STATUS: Record<MissionStatus, {
  icon: React.ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;
  label: string;
  ring: string;
  dot: string;
  pulse: boolean;
}> = {
  draft:     { icon: Circle,       label: 'Draft',     ring: 'ring-slate-700/50',   dot: 'bg-slate-500',   pulse: false },
  queued:    { icon: Clock,        label: 'Queued',    ring: 'ring-amber-500/30',   dot: 'bg-amber-400',   pulse: false },
  planned:   { icon: Clock,        label: 'Planned',   ring: 'ring-blue-500/30',    dot: 'bg-blue-400',    pulse: false },
  active:    { icon: Zap,          label: 'Active',    ring: 'ring-emerald-500/40', dot: 'bg-emerald-400', pulse: true  },
  paused:    { icon: PauseCircle,  label: 'Paused',    ring: 'ring-amber-500/30',   dot: 'bg-amber-400',   pulse: false },
  review:    { icon: AlertCircle,  label: 'Review',    ring: 'ring-violet-500/40',  dot: 'bg-violet-400',  pulse: true  },
  completed: { icon: CheckCircle2, label: 'Completed', ring: 'ring-emerald-500/20', dot: 'bg-emerald-500', pulse: false },
  failed:    { icon: XCircle,      label: 'Failed',    ring: 'ring-rose-500/40',    dot: 'bg-rose-500',    pulse: false },
  cancelled: { icon: XCircle,      label: 'Cancelled', ring: 'ring-slate-700/50',   dot: 'bg-slate-500',   pulse: false },
  archived:  { icon: Circle,       label: 'Archived',  ring: 'ring-slate-800/50',   dot: 'bg-slate-600',   pulse: false },
};

const PRIORITY_STRIPE: Record<Priority, string> = {
  low:      'border-l-slate-600',
  medium:   'border-l-blue-500/60',
  high:     'border-l-amber-500',
  critical: 'border-l-rose-500',
};

// ─── Spring configs (emil-design-eng cheat sheet) ───────────────────────────

const PRESS_SPRING  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const FILL_SPRING   = { type: 'spring', stiffness: 200, damping: 25 } as const;

// ─── Pulse dot (frontend-design signature — active missions breathe) ─────────

function PulseDot({ dot }: { dot: string }) {
  const reduce = useReducedMotion();
  return (
    <span className="relative flex h-2 w-2 shrink-0" aria-hidden>
      {!reduce && (
        <motion.span
          className={cn('absolute inset-0 rounded-full', dot)}
          animate={{ scale: [1, 1.9, 1], opacity: [0.8, 0, 0.8] }}
          transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
      <span className={cn('relative rounded-full h-2 w-2', dot)} />
    </span>
  );
}

// ─── MissionCard ─────────────────────────────────────────────────────────────

export const MissionCard = React.memo(function MissionCard({
  mission,
  orgId,
  isSelected = false,
  onClick,
  className,
}: MissionCardProps) {
  const reduce      = useReducedMotion();
  const cfg         = STATUS[mission.status] ?? STATUS.draft;
  const Icon        = cfg.icon;
  const stripe      = PRIORITY_STRIPE[mission.priority] ?? PRIORITY_STRIPE.medium;
  const labelId     = useId();

  // Real task progress — OrgMission carries no task/subtask counts of its own
  // (see app/org/schemas.py MissionResponse), so derive it from the tasks
  // actually scoped to this mission via the tasks endpoint. When orgId isn't
  // supplied, or the fetch hasn't resolved yet, show an honest unknown state
  // rather than a fabricated 0%.
  const { data: tasksResp } = useOrgTasks(orgId, { mission_id: mission.id });
  const tasks       = (tasksResp as { data?: OrgTask[] } | undefined)?.data;
  const total       = tasks?.length ?? 0;
  const done        = tasks?.filter(t => t.status === 'completed').length ?? 0;
  const pct         = total > 0 ? Math.round((done / total) * 100) : 0;
  const progressUnknown = !!orgId && tasks === undefined;

  const handleClick = useCallback(() => onClick?.(mission.id), [mission.id, onClick]);
  const handleKey   = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick?.(mission.id); }
  }, [mission.id, onClick]);

  return (
    <motion.article
      // web-guidelines: semantic article, role button for custom interactive
      role="button"
      tabIndex={0}
      aria-labelledby={labelId}
      aria-pressed={isSelected}
      // emil-design-eng: spring physics — never cubic-bezier for interactions
      whileHover={reduce ? {} : { scale: 1.01 }}
      whileTap={reduce  ? {} : { scale: 0.98 }}   // 0.98 = Emil's press feel
      transition={PRESS_SPRING}
      onClick={handleClick}
      onKeyDown={handleKey}
      // web-guidelines: touch-manipulation prevents 300ms double-tap delay
      style={{ touchAction: 'manipulation' }}
      data-testid="mission-card"
      className={cn(
        'relative flex flex-col gap-2.5 rounded-xl p-4',
        'min-w-0',              // web-guidelines: flex child min-w-0
        'bg-[#1A1F2E] border border-[#1E2535]',
        'border-l-2', stripe,
        isSelected && 'ring-2 ring-blue-500/40 ring-offset-2 ring-offset-[#0F1117]',
        // impeccable-ui: visible hover + focus states
        'hover:bg-[#1E2535] hover:border-[#2D3748]',
        // web-guidelines: focus-visible not :focus (no ring on click)
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
        'cursor-pointer select-none',
        // web-guidelines: list explicit props, no transition:all
        'transition-[background-color,border-color,box-shadow] duration-150',
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-2 min-w-0">
        {/* impeccable-ui: ONE dominant element — title is primary */}
        <h3
          id={labelId}
          className={cn(
            'flex-1 min-w-0',
            // impeccable-ui: body ≥15px, -0.01em tracking for headings
            'text-[15px] font-semibold leading-snug tracking-[-0.01em]',
            'text-[#F1F5F9] truncate',
            // web-guidelines: text-balance prevents orphans on headings
            '[text-wrap:balance]',
          )}
        >
          {mission.title}
        </h3>

        <div className="flex items-center gap-1 shrink-0 mt-0.5">
          <span
            className={cn(
              'flex items-center gap-1 px-1.5 py-0.5 rounded-full',
              'text-[10px] font-medium uppercase tracking-[0.07em]',
              'ring-1', cfg.ring,
              cfg.pulse ? 'text-emerald-300' : 'text-[#94A3B8]',
            )}
            // a11y: status conveyed both visually and via aria
            aria-label={`Status: ${cfg.label}`}
          >
            {cfg.pulse
              ? <PulseDot dot={cfg.dot} />
              : <Icon className="h-2.5 w-2.5" aria-hidden />
            }
            {cfg.label}
          </span>

          <ChevronRight
            className={cn(
              'h-3.5 w-3.5 shrink-0',
              isSelected ? 'text-blue-400' : 'text-[#475569]',
              'transition-colors duration-150',
            )}
            aria-hidden
          />
        </div>
      </div>

      {/* Objective — secondary hierarchy (impeccable-ui) */}
      {mission.objective && (
        <p className="text-[13px] leading-[1.55] text-[#94A3B8] line-clamp-2 min-w-0">
          {mission.objective}
        </p>
      )}

      {/* Progress bar — real task counts when known; honest "—" while unresolved.
          Hidden entirely once resolved with zero tasks (nothing to show progress on). */}
      {(total > 0 || progressUnknown) && (
        <div className="flex items-center gap-2 min-w-0">
          {progressUnknown ? (
            <div
              className="flex-1 h-1 rounded-full bg-[#252B3B] overflow-hidden"
              role="progressbar"
              aria-label="Progress: unknown, loading task data"
            >
              <div className="h-full w-1/3 rounded-full bg-[#334155] animate-pulse" />
            </div>
          ) : (
            <div
              className="flex-1 h-1 rounded-full bg-[#252B3B] overflow-hidden"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`Progress: ${pct}%`}
            >
              <motion.div
                className="h-full rounded-full bg-gradient-to-r from-blue-600 to-blue-400"
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={reduce ? { duration: 0 } : FILL_SPRING}
              />
            </div>
          )}
          {/* web-guidelines: tabular-nums for numbers in comparison context */}
          <span className="text-[11px] text-[#475569] tabular-nums shrink-0 font-mono">
            {progressUnknown ? '—' : `${done}/${total}`}
          </span>
        </div>
      )}

      {/* ── Execution metadata (team formation + dispatch status) ── */}
      {(() => {
        const meta = mission.metadata as Record<string, unknown> | undefined;
        const goalId = meta?.goal_id as string | undefined;
        const dispatched = meta?.dispatched as boolean | undefined;
        const plan = meta?.orchestration_plan_summary as {
          topology?: string; departments?: string[]; autonomy_level?: number;
        } | undefined;
        const depts = plan?.departments ?? [];
        if (!goalId && !dispatched && depts.length === 0) return null;
        return (
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            {/* Dispatched / executing indicator */}
            {goalId && (
              <span className="flex items-center gap-1 text-[10px] font-medium text-[#00D4FF]/80">
                {mission.status === 'active'
                  ? <motion.span animate={{ rotate: 360 }} transition={{ duration: 1.5, repeat: Infinity, ease: 'linear' }}>
                      <Loader2 className="h-3 w-3 text-[#00D4FF]" />
                    </motion.span>
                  : <Cpu className="h-3 w-3" />
                }
                {mission.status === 'active' ? 'Executing' : 'Agent dispatched'}
              </span>
            )}
            {/* Topology badge */}
            {plan?.topology && (
              <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#00D4FF]/8 text-[10px] text-[#00D4FF]/70 border border-[#00D4FF]/15">
                <GitBranch className="h-2.5 w-2.5" />
                {plan.topology}
              </span>
            )}
            {/* Department tags */}
            {depts.slice(0, 3).map((d: string) => (
              <span key={d} className="px-1.5 py-0.5 rounded bg-[#1E2535] text-[10px] text-[#64748B] border border-[#252B3B] capitalize">
                {d}
              </span>
            ))}
            {depts.length > 3 && (
              <span className="text-[10px] text-[#475569]">+{depts.length - 3}</span>
            )}
          </div>
        );
      })()}

      {/* Footer meta — tertiary (impeccable-ui: clearly de-emphasized) */}
      <div className="flex items-center justify-between text-[11px] text-[#475569] min-w-0">
        <span
          className={cn(
            'uppercase tracking-[0.06em] font-medium',
            mission.priority === 'critical' && 'text-rose-400',
            mission.priority === 'high'     && 'text-amber-400',
          )}
          aria-label={`Priority: ${mission.priority}`}
        >
          {mission.priority}
        </span>

        {mission.deadline && (
          <time
            dateTime={mission.deadline}
            className="tabular-nums"
            aria-label={`Due: ${new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(mission.deadline))}`}
          >
            {/* web-guidelines: use Intl.DateTimeFormat not hardcoded formats */}
            {new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(mission.deadline))}
          </time>
        )}
      </div>
    </motion.article>
  );
}, (prev, next) =>
  prev.mission.id               === next.mission.id               &&
  prev.mission.status           === next.mission.status           &&
  prev.mission.title            === next.mission.title            &&
  prev.mission.completed_at     === next.mission.completed_at     &&
  prev.orgId                    === next.orgId                    &&
  prev.isSelected               === next.isSelected,
);
