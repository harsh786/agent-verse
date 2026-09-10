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
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useOrgTasks } from '../hooks/useOrg';
import type { OrgMission, OrgTask, MissionStatus, Priority } from '../types';

interface MissionCardProps {
  mission:    OrgMission;
  /** Org the mission belongs to — needed to fetch real task progress. Omit to skip the progress fetch (e.g. isolated previews). */
  orgId?:     string;
  /**
   * Pre-resolved task progress supplied by the parent. When set, the card
   * renders these counts and skips its own /tasks fetch — this is how the
   * missions list avoids one request per card (a burst that trips the
   * per-tenant rate limit). Omit to let the card fetch its own progress.
   */
  progress?:  { done: number; total: number };
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
  progress,
  isSelected = false,
  onClick,
  className,
}: MissionCardProps) {
  const reduce      = useReducedMotion();
  const cfg         = STATUS[mission.status] ?? STATUS.draft;
  const Icon        = cfg.icon;
  const stripe      = PRIORITY_STRIPE[mission.priority] ?? PRIORITY_STRIPE.medium;
  const labelId     = useId();
  const statusText  =
    mission.status === 'active'    ? 'text-emerald-300'    :
    mission.status === 'completed' ? 'text-emerald-400/70' :
    mission.status === 'failed'    ? 'text-rose-400'       :
    mission.status === 'review'    ? 'text-violet-300'     :
    mission.status === 'paused' || mission.status === 'queued' ? 'text-amber-300' :
    'text-[#94A3B8]';

  // Real task progress — OrgMission carries no task/subtask counts of its own
  // (see app/org/schemas.py MissionResponse), so it's derived from the tasks
  // scoped to this mission. Two supply paths:
  //   1. The parent passes `progress` (the missions list fetches all org tasks
  //      in ONE request and groups them) — no per-card fetch, no request burst.
  //   2. No `progress` (isolated previews / standalone use): the card fetches
  //      its own tasks. Passing orgId=undefined here disables that query.
  const external    = progress !== undefined;
  const { data: tasksResp } = useOrgTasks(external ? undefined : orgId, { mission_id: mission.id });
  const tasks       = (tasksResp as { data?: OrgTask[] } | undefined)?.data;
  const total       = external ? progress.total : (tasks?.length ?? 0);
  const done        = external ? progress.done  : (tasks?.filter(t => t.status === 'completed').length ?? 0);
  const pct         = total > 0 ? Math.round((done / total) * 100) : 0;
  const progressUnknown = !external && !!orgId && tasks === undefined;

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
        'relative flex flex-col gap-1.5 rounded-xl px-3.5 py-3',
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
      {/* Row 1 — status glyph · title · status label · chevron. One scannable line. */}
      <div className="flex items-center gap-2 min-w-0">
        {cfg.pulse
          ? <PulseDot dot={cfg.dot} />
          : <Icon className={cn('h-3 w-3 shrink-0', statusText)} aria-hidden />}
        <h3
          id={labelId}
          className="flex-1 min-w-0 text-[14px] font-medium leading-tight tracking-[-0.01em] text-[#F1F5F9] truncate"
        >
          {mission.title}
        </h3>
        {mission.priority === 'critical' && (
          <span className="shrink-0 text-[9px] font-semibold uppercase tracking-[0.08em] text-rose-400">crit</span>
        )}
        {mission.priority === 'high' && (
          <span className="shrink-0 text-[9px] font-semibold uppercase tracking-[0.08em] text-amber-400">high</span>
        )}
        <span
          className={cn('shrink-0 text-[10px] font-medium uppercase tracking-[0.06em]', statusText)}
          aria-label={`Status: ${cfg.label}`}
        >
          {cfg.label}
        </span>
        <ChevronRight
          className={cn('h-3.5 w-3.5 shrink-0', isSelected ? 'text-blue-400' : 'text-[#475569]')}
          aria-hidden
        />
      </div>

      {/* Row 2 — objective (single line) with an inline progress sliver on the right. */}
      <div className="flex items-center gap-3 min-w-0">
        {mission.objective ? (
          <p className="flex-1 min-w-0 text-[12px] leading-tight text-[#64748B] truncate">
            {mission.objective}
          </p>
        ) : (
          <span className="flex-1" />
        )}
        {(total > 0 || progressUnknown) && (
          <div className="flex items-center gap-1.5 shrink-0">
            <div
              className="w-16 h-1 rounded-full bg-[#252B3B] overflow-hidden"
              role="progressbar"
              aria-valuenow={progressUnknown ? undefined : pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={progressUnknown ? 'Progress: loading' : `Progress: ${pct}%`}
            >
              <motion.div
                className={cn('h-full rounded-full', progressUnknown ? 'bg-[#334155] animate-pulse' : 'bg-[#00D4FF]/70')}
                initial={{ width: 0 }}
                animate={{ width: progressUnknown ? '33%' : `${pct}%` }}
                transition={reduce ? { duration: 0 } : FILL_SPRING}
              />
            </div>
            <span className="text-[10px] text-[#475569] tabular-nums font-mono">
              {progressUnknown ? '—' : `${done}/${total}`}
            </span>
          </div>
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
  prev.progress?.done           === next.progress?.done           &&
  prev.progress?.total          === next.progress?.total          &&
  prev.isSelected               === next.isSelected,
);
