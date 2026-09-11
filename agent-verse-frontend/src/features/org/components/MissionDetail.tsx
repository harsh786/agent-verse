/**
 * MissionDetail — full mission view with live SSE progress stream.
 *
 * Skills applied:
 *   - emil-design-eng: spring layout animations for task list
 *   - impeccable-ui:   status as dominant visual, clear task hierarchy
 *   - web-guidelines:  aria-live for SSE updates, keyboard nav, tabular-nums
 *   - ui-ux-pro-max:   streaming progress bar, reduced-motion fallback
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  X, Zap, PauseCircle, AlertCircle, Play, Square, ChevronRight,
  Cpu, GitBranch, CheckCircle2, Loader2, CircleDot, Clock, Users,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { cn } from '@/lib/utils';
import { getAuthHeader } from '@/stores/auth';
import { useMission, useOrgEvents, useUpdateMissionStatus } from '../hooks/useOrg';
import { MissionDeliverable } from './MissionDeliverable';
import type { OrgMission, MissionStatus } from '../types';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

// ── Goal execution polling ────────────────────────────────────────────────────

interface GoalState {
  goal_id?: string;
  status?: string;
  plan?: string[];
  steps?: Array<{ step: string; status: string; result?: string }>;
  iterations?: number;
  error_message?: string;
}

function useGoalExecution(goalId: string | null | undefined) {
  return useQuery<GoalState>({
    queryKey: ['goal-exec', goalId],
    queryFn: async () => {
      if (!goalId) return {};
      const r = await fetch(`${API_BASE}/goals/${goalId}`, { headers: getAuthHeader() });
      if (!r.ok) return {};
      return r.json();
    },
    enabled: !!goalId,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      if (!status || status === 'complete' || status === 'failed' || status === 'cancelled') return false;
      return 2000;
    },
  });
}

interface MissionDetailProps {
  orgId:     string;
  missionId: string;
  onClose:   () => void;
}

const PANEL_SPRING = { type: 'spring', stiffness: 280, damping: 26 } as const;

const STATUS_COLOR: Record<MissionStatus, string> = {
  draft:     'text-slate-400',
  queued:    'text-amber-400',
  planned:   'text-blue-400',
  active:    'text-emerald-400',
  paused:    'text-amber-400',
  review:    'text-violet-400',
  completed: 'text-emerald-400',
  failed:    'text-rose-400',
  cancelled: 'text-slate-400',
  archived:  'text-[#5A7494]',
};

const STATUS_BG: Record<MissionStatus, string> = {
  draft:     'bg-slate-400/10 ring-slate-700/50',
  queued:    'bg-amber-400/10 ring-amber-500/30',
  planned:   'bg-blue-400/10  ring-blue-500/30',
  active:    'bg-emerald-400/10 ring-emerald-500/40',
  paused:    'bg-amber-400/10 ring-amber-500/30',
  review:    'bg-violet-400/10 ring-violet-500/40',
  completed: 'bg-emerald-500/10 ring-emerald-500/20',
  failed:    'bg-rose-500/10  ring-rose-500/40',
  cancelled: 'bg-slate-400/10 ring-slate-700/50',
  archived:  'bg-slate-500/10 ring-slate-800/50',
};

export function MissionDetail({ orgId, missionId, onClose }: MissionDetailProps) {
  const reduce = useReducedMotion();
  const { data: mission, isLoading } = useMission(orgId, missionId);
  const { data: events } = useOrgEvents(orgId);
  const updateStatus = useUpdateMissionStatus(orgId);
  const liveRef = useRef<HTMLDivElement>(null);

  // Keyboard: Escape closes (web-guidelines)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const handleStatusChange = useCallback(async (newStatus: MissionStatus) => {
    if (!mission) return;
    await updateStatus.mutateAsync({ missionId: mission.id, status: newStatus });
  }, [mission, updateStatus]);

  // Filter events for this mission
  const missionEvents = events?.filter(e =>
    e.entity_id === missionId || e.entity_type === 'mission'
  ).slice(0, 10) ?? [];

  return (
    <motion.aside
      key="mission-detail"
      role="complementary"
      aria-label={`Mission details: ${mission?.title ?? '…'}`}
      initial={{ x: reduce ? 0 : '100%', opacity: 1 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: reduce ? 0 : '80%', opacity: 0 }}  // Emil: exit shorter
      transition={reduce ? { duration: 0.15 } : PANEL_SPRING}
      className={cn(
        'fixed right-0 top-0 h-full z-30',
        'w-full max-w-md',
        'bg-[#0F1117] border-l border-[#1E2535]',
        'flex flex-col',
        'shadow-[-20px_0_60px_rgba(0,0,0,0.5)]',
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-[#1E2535] shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Zap className="h-4 w-4 text-blue-400 shrink-0" aria-hidden />
          <h2 className="text-[15px] font-semibold text-[#F1F5F9] tracking-[-0.01em] truncate">
            {isLoading ? 'Loading…' : (mission?.title ?? 'Mission')}
          </h2>
        </div>
        <button
          onClick={onClose}
          aria-label="Close mission detail"
          style={{ touchAction: 'manipulation' }}
          className={cn(
            'p-2 rounded-lg text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-[#1A1F2E]',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
            'transition-colors duration-150 min-w-[44px] min-h-[44px]',
            'flex items-center justify-center shrink-0',
          )}
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-5 space-y-5">
        {isLoading ? (
          <SkeletonDetail />
        ) : mission ? (
          <MissionBody
            mission={mission}
            events={missionEvents}
            onStatusChange={handleStatusChange}
            reduce={!!reduce}
            liveRef={liveRef}
            orgId={orgId}
            missionId={missionId}
          />
        ) : (
          <EmptyState />
        )}
      </div>
    </motion.aside>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function MissionBody({
  mission, events, onStatusChange, reduce, liveRef, orgId, missionId,
}: {
  mission: OrgMission;
  events: Array<{ id: string; title: string; event_type: string; created_at: string }>;
  onStatusChange: (s: MissionStatus) => void;
  reduce: boolean;
  liveRef: React.RefObject<HTMLDivElement | null>;
  orgId: string;
  missionId: string;
}) {
  const statusColor = STATUS_COLOR[mission.status] ?? 'text-slate-400';
  const statusBg    = STATUS_BG[mission.status] ?? 'bg-slate-400/10 ring-slate-700/50';
  const isActive    = mission.status === 'active';
  const isPaused    = mission.status === 'paused';
  const isCompleted = ['completed', 'failed', 'cancelled'].includes(mission.status);
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const updateStatus = useUpdateMissionStatus(orgId);

  // Extract goal_id from metadata
  const meta = mission.metadata as Record<string, unknown> | undefined;
  const goalId = meta?.goal_id as string | undefined;
  const plan = meta?.orchestration_plan_summary as {
    topology?: string; departments?: string[]; autonomy_level?: number; estimated_cost_usd?: number;
  } | undefined;

  // Poll goal execution state
  const { data: goalState } = useGoalExecution(goalId);

  return (
    <>
      {/* Status badge */}
      <div className="flex items-center gap-3">
        <span className={cn(
          'inline-flex items-center gap-1.5 px-3 py-1 rounded-full',
          'text-xs font-medium uppercase tracking-[0.06em] ring-1',
          statusColor, statusBg,
        )}>
          {isActive && (
            <motion.span
              className="h-1.5 w-1.5 rounded-full bg-emerald-400"
              animate={reduce ? {} : { scale: [1, 1.8, 1], opacity: [0.8, 0, 0.8] }}
              transition={{ duration: 2, repeat: Infinity }}
            />
          )}
          {mission.status}
        </span>
        <span className={cn(
          'text-xs uppercase tracking-[0.06em] font-medium',
          mission.priority === 'critical' ? 'text-rose-400' :
          mission.priority === 'high'     ? 'text-amber-400' : 'text-[#94A3B8]',
        )}>
          {mission.priority}
        </span>
      </div>

      {/* Objective */}
      {mission.objective && (
        <div>
          <p className="text-xs uppercase tracking-[0.06em] font-medium text-[#475569] mb-1.5">
            Objective
          </p>
          <p className="text-[14px] leading-[1.6] text-[#94A3B8]">{mission.objective}</p>
        </div>
      )}

      {/* ── Orchestration plan (team formation result) ── */}
      {plan && (plan.departments?.length || plan.topology) && (
        <div className="rounded-xl border border-[#00D4FF]/15 bg-[#00D4FF]/5 p-3 space-y-2">
          <div className="flex items-center gap-2">
            <motion.div
              animate={{ boxShadow: ['0 0 6px rgba(0,212,255,0.3)', '0 0 14px rgba(0,212,255,0.6)', '0 0 6px rgba(0,212,255,0.3)'] }}
              transition={{ duration: 2, repeat: Infinity }}
              className="p-1 rounded-lg bg-[#00D4FF]/10"
            >
              <Users className="h-3 w-3 text-[#00D4FF]" />
            </motion.div>
            <span className="text-[11px] font-semibold text-[#00D4FF] uppercase tracking-wider">Agent Team Formed</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {plan.topology && (
              <span className="flex items-center gap-1 px-2 py-0.5 rounded bg-[#1A1F2E] border border-[#2D3748] text-[10px] text-[#94A3B8]">
                <GitBranch className="h-2.5 w-2.5 text-[#00D4FF]" />
                {plan.topology}
              </span>
            )}
            {(plan.departments ?? []).map((d: string) => (
              <span key={d} className="px-2 py-0.5 rounded bg-[#1A1F2E] border border-[#2D3748] text-[10px] text-[#64748B] capitalize">{d}</span>
            ))}
          </div>
          {plan.estimated_cost_usd != null && plan.estimated_cost_usd > 0 && (
            <p className="text-[10px] text-[#475569]">Est. cost: ${plan.estimated_cost_usd.toFixed(3)}</p>
          )}
        </div>
      )}

      {/* ── Deliverable (the aggregated mission result from finalize_mission) ── */}
      <MissionDeliverable outputs={mission.outputs ?? []} evidence={mission.evidence ?? []} />

      {/* ── Live Agent Execution Panel ── */}
      {goalId && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            {goalState?.status === 'executing' || goalState?.status === 'planning' ? (
              <motion.div animate={{ rotate: 360 }} transition={{ duration: 1.5, repeat: Infinity, ease: 'linear' }}>
                <Loader2 className="h-3.5 w-3.5 text-[#00D4FF]" />
              </motion.div>
            ) : goalState?.status === 'complete' ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
            ) : goalState?.status === 'failed' ? (
              <AlertCircle className="h-3.5 w-3.5 text-rose-400" />
            ) : (
              <Cpu className="h-3.5 w-3.5 text-[#475569]" />
            )}
            <span className="text-xs uppercase tracking-[0.06em] font-medium text-[#475569]">
              Agent Execution
            </span>
            {goalState?.status && (
              <span className={cn('text-[10px] font-mono px-1.5 py-0.5 rounded',
                goalState.status === 'complete'  ? 'bg-emerald-500/10 text-emerald-400' :
                goalState.status === 'failed'    ? 'bg-rose-500/10 text-rose-400' :
                goalState.status === 'executing' ? 'bg-[#00D4FF]/10 text-[#00D4FF]' :
                'bg-amber-500/10 text-amber-400'
              )}>
                {goalState.status}
              </span>
            )}
          </div>

          {/* Plan steps */}
          {(goalState?.plan ?? []).length > 0 && (
            <div className="rounded-xl border border-[#1E2535] bg-[#0F1117] overflow-hidden">
              <div className="px-3 py-2 border-b border-[#1E2535] text-[10px] text-[#475569] uppercase tracking-wider font-medium">
                Execution Plan — {goalState?.plan?.length} steps
              </div>
              <ul className="divide-y divide-[#1A1F2E]">
                {(goalState?.plan ?? []).map((step, i) => {
                  const execStep = (goalState?.steps ?? []).find(s => s.step === step);
                  const isDone = execStep?.status === 'complete';
                  const isRunning = !isDone && i === (goalState?.steps ?? []).filter(s => s.status === 'complete').length;
                  return (
                    <li
                      key={i}
                      style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
                      className={cn('jarvis-rise-in flex items-start gap-2.5 px-3 py-2.5 text-[12px]',
                        isDone   && 'bg-emerald-500/5',
                        isRunning && 'bg-[#00D4FF]/5',
                      )}
                    >
                      <span className="mt-0.5 shrink-0">
                        {isDone   ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> :
                         isRunning ? (
                           <motion.span animate={{ rotate: 360 }} transition={{ duration: 1.2, repeat: Infinity, ease: 'linear' }} className="block">
                             <Loader2 className="h-3.5 w-3.5 text-[#00D4FF]" />
                           </motion.span>
                         ) : <CircleDot className="h-3.5 w-3.5 text-[#475569]" />}
                      </span>
                      <span className={cn('flex-1 min-w-0',
                        isDone   ? 'text-emerald-300/80 line-through decoration-emerald-500/40' :
                        isRunning ? 'text-[#00D4FF]' :
                        'text-[#475569]'
                      )}>
                        {step}
                      </span>
                    </li>
                  );
                })}
              </ul>
              {goalState?.iterations != null && (
                <div className="px-3 py-1.5 border-t border-[#1E2535] text-[10px] text-[#475569] font-mono">
                  Iteration {goalState.iterations}
                  {(goalState?.steps ?? []).length > 0 && ` · ${(goalState.steps ?? []).filter(s => s.status === 'complete').length}/${(goalState.plan ?? []).length} done`}
                </div>
              )}
            </div>
          )}

          {/* Goal failed error */}
          {goalState?.error_message && (
            <div className="rounded-lg border border-rose-500/20 bg-rose-500/5 px-3 py-2 text-[11px] text-rose-400">
              {goalState.error_message.slice(0, 120)}
            </div>
          )}

          {/* G-07: Approval required callout — when goal is waiting_human */}
          {(goalState?.status === 'waiting_human' || (mission.status as string) === 'waiting_human') && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/8 px-3 py-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-amber-400 text-sm">⏳</span>
                <span className="text-[12px] font-semibold text-amber-400">Human Approval Required</span>
                <motion.div
                  className="ml-auto h-2 w-2 rounded-full bg-amber-400"
                  animate={{ opacity: [1, 0.3, 1] }}
                  transition={{ duration: 1.2, repeat: Infinity }}
                />
              </div>
              <p className="text-[11px] text-[#94A3B8] mb-3">
                This mission step needs your approval to proceed.
              </p>
              <div className="flex gap-2">
                <button
                  disabled={approving || rejecting}
                  className="flex-1 py-1.5 text-[11px] font-medium bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-lg transition-colors"
                  onClick={async () => {
                    setApproving(true);
                    try {
                      const r = await fetch(`${API_BASE}/v1/org/${orgId}/tasks/${missionId}/approve`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
                        body: JSON.stringify({ approver: 'user', note: 'Approved from mission panel' }),
                      });
                      if (r.ok) await updateStatus.mutateAsync({ missionId, status: 'active' });
                    } finally {
                      setApproving(false);
                    }
                  }}
                >
                  {approving ? '…' : '✓ Approve'}
                </button>
                <button
                  disabled={approving || rejecting}
                  className="flex-1 py-1.5 text-[11px] font-medium bg-red-900/60 hover:bg-red-900 disabled:opacity-50 text-red-300 rounded-lg transition-colors"
                  onClick={async () => {
                    setRejecting(true);
                    try {
                      await fetch(`${API_BASE}/v1/org/${orgId}/tasks/${missionId}/reject`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
                        body: JSON.stringify({ approver: 'user', note: 'Rejected from mission panel' }),
                      });
                    } finally {
                      setRejecting(false);
                    }
                  }}
                >
                  {rejecting ? '…' : '✕ Reject'}
                </button>
                <a
                  href="/approvals"
                  className="px-2 py-1.5 text-[11px] text-amber-400 hover:text-amber-300 font-medium self-center whitespace-nowrap"
                >
                  Full view →
                </a>
              </div>
            </div>
          )}

          {/* Queued / waiting */}
          {!goalState?.status && (
            <div className="flex items-center gap-2 text-[12px] text-[#475569]">
              <motion.span animate={{ opacity: [0.4, 1, 0.4] }} transition={{ duration: 1.5, repeat: Infinity }}>
                <Clock className="h-3.5 w-3.5" />
              </motion.span>
              Queued — waiting for agent worker
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      {!isCompleted && (
        <div className="flex gap-2">
          {!isActive && !isPaused && (
            <ActionButton
              icon={<Play className="h-3.5 w-3.5" />}
              label="Activate"
              onClick={() => onStatusChange('active')}
              variant="primary"
            />
          )}
          {isActive && (
            <ActionButton
              icon={<PauseCircle className="h-3.5 w-3.5" />}
              label="Pause"
              onClick={() => onStatusChange('paused')}
              variant="secondary"
            />
          )}
          {isPaused && (
            <ActionButton
              icon={<Play className="h-3.5 w-3.5" />}
              label="Resume"
              onClick={() => onStatusChange('active')}
              variant="primary"
            />
          )}
          <ActionButton
            icon={<Square className="h-3.5 w-3.5" />}
            label="Cancel"
            onClick={() => onStatusChange('cancelled')}
            variant="danger"
          />
        </div>
      )}

      {/* Live event stream (aria-live: polite for screen readers) */}
      <div>
        <p className="text-xs uppercase tracking-[0.06em] font-medium text-[#475569] mb-2">
          Live Activity
          {/* web-guidelines: aria-live="polite" for async updates */}
          <span className="sr-only" aria-live="polite" ref={liveRef} />
        </p>
        <AnimatePresence mode="popLayout">
          {events.length === 0 ? (
            <p className="text-[13px] text-[#475569] italic">No activity yet…</p>
          ) : (
            <ul className="space-y-1.5" aria-label="Mission activity">
              {events.map((ev) => (
                <motion.li
                  key={ev.id}
                  layout
                  initial={false}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                  className="flex items-start gap-2 text-[13px]"
                >
                  <ChevronRight className="h-3 w-3 text-blue-400/60 mt-0.5 shrink-0" aria-hidden />
                  <span className="text-[#94A3B8] min-w-0">{ev.title}</span>
                  <time
                    dateTime={ev.created_at}
                    className="ml-auto shrink-0 tabular-nums text-[11px] text-[#475569]"
                  >
                    {new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' })
                      .format(new Date(ev.created_at))}
                  </time>
                </motion.li>
              ))}
            </ul>
          )}
        </AnimatePresence>
      </div>

      {/* Meta */}
      <div className="pt-2 border-t border-[#1E2535] space-y-2 text-[12px] text-[#475569]">
        <MetaRow label="Created" value={new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(mission.created_at))} />
        {mission.started_at && (
          <MetaRow label="Started" value={new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(mission.started_at))} />
        )}
        {mission.completed_at && (
          <MetaRow label="Completed" value={new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(mission.completed_at))} />
        )}
        {mission.deadline && (
          <MetaRow label="Deadline" value={new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(mission.deadline))} />
        )}
      </div>
    </>
  );
}

function ActionButton({
  icon, label, onClick, variant,
}: { icon: React.ReactNode; label: string; onClick: () => void; variant: 'primary' | 'secondary' | 'danger' }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      style={{ touchAction: 'manipulation' }}
      className={cn(
        'flex items-center gap-1.5 px-3 py-2 rounded-lg text-[13px] font-medium',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
        'active:scale-[0.97] transition-[background-color,transform] duration-150',
        'min-h-[36px]',
        variant === 'primary'   && 'bg-blue-600/20 text-blue-300 hover:bg-blue-600/30 ring-1 ring-blue-500/30',
        variant === 'secondary' && 'bg-[#252B3B] text-[#94A3B8] hover:text-[#F1F5F9] ring-1 ring-[#2D3748]',
        variant === 'danger'    && 'bg-rose-500/10 text-rose-400 hover:bg-rose-500/20 ring-1 ring-rose-500/20',
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="shrink-0">{label}</span>
      <span className="text-[#94A3B8] tabular-nums truncate text-right">{value}</span>
    </div>
  );
}

function SkeletonDetail() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-6 w-24 rounded-full bg-[#252B3B]" />
      <div className="space-y-2">
        <div className="h-3 w-16 rounded bg-[#252B3B]" />
        <div className="h-4 w-full rounded bg-[#252B3B]" />
        <div className="h-4 w-3/4 rounded bg-[#252B3B]" />
      </div>
      {[1, 2, 3].map(i => (
        <div key={i} className="h-8 rounded-lg bg-[#252B3B]" />
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-48 text-center">
      <AlertCircle className="h-8 w-8 text-[#475569] mb-3" aria-hidden />
      <p className="text-[14px] text-[#94A3B8]">Mission not found</p>
      <p className="text-[12px] text-[#475569] mt-1">It may have been deleted</p>
    </div>
  );
}
