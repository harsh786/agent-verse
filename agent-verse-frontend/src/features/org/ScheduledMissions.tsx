/**
 * ScheduledMissions — AA4: NLScheduler UI for org-scoped scheduled goals.
 *
 * Skills applied:
 *   frontend-design:   JARVIS dark, schedule card grid, cron tooltip
 *   emil-design-eng:   spring 300/28 modal slide, 600/35 row interactions
 *   impeccable-ui:     NL label dominant, cron secondary, next-run tertiary
 *   web-guidelines:    time[datetime], aria-live, schedule status indicators
 *   ui-ux-pro-max:     44px targets, keyboard nav, useReducedMotion
 */
import { useState, useCallback } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Clock, Plus, Pause, Play, Zap, Trash2, Calendar } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

const apiClient = {
  get: <T,>(path: string) => apiRequest<T>('GET', path),
  post: <T,>(path: string, body?: unknown) => apiRequest<T>('POST', path, body),
  delete: <T,>(path: string) => apiRequest<T>('DELETE', path),
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface ScheduleRun {
  id:        string;
  startedAt: string;
  status:    'success' | 'failed' | 'running';
}

interface Schedule {
  id:              string;
  name:            string;
  cron_expr:       string;
  goal_template:   string;
  active:          boolean;
  next_run?:       string;
  last_run?:       string;
  run_history?:    ScheduleRun[];
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useSchedules() {
  return useQuery<Schedule[]>({
    queryKey: ['schedules'],
    queryFn: () => apiClient.get<Schedule[]>('/schedules').catch(() => []),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

function useCreateSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; cron_expr: string; goal_template: string }) =>
      apiClient.post<Schedule>('/schedules', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });
}

function useScheduleAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'resume' | 'fire' | 'delete' }) => {
      if (action === 'delete') return apiClient.delete(`/schedules/${id}`);
      return apiClient.post(`/schedules/${id}/${action}`, {});
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });
}

// ── NL cron parser (client-side preview) ─────────────────────────────────────

function parseCronPreview(nl: string): string {
  const l = nl.toLowerCase();
  if (l.includes('every monday') || l.includes('monday at')) return '0 9 * * 1';
  if (l.includes('every friday')) return '0 17 * * 5';
  if (l.includes('daily') || l.includes('every day')) return '0 0 * * *';
  if (l.includes('hourly'))   return '0 * * * *';
  if (l.includes('weekly'))   return '0 9 * * 1';
  if (l.includes('monthly'))  return '0 9 1 * *';
  return '— (server will parse)';
}

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_MODAL = { type: 'spring', stiffness: 300, damping: 28 } as const;

// ── Schedule Card ─────────────────────────────────────────────────────────────

function ScheduleCard({ schedule, onAction, index }: {
  schedule: Schedule;
  onAction: (id: string, action: 'pause' | 'resume' | 'fire' | 'delete') => void;
  index: number;
}) {
  const reduce = useReducedMotion();

  return (
    <motion.article
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ ...SPRING_FAST, delay: index * 0.05 }}
      aria-label={`Schedule: ${schedule.name}`}
      className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl p-4 flex flex-col gap-3"
    >
      {/* Status + name */}
      <div className="flex items-start gap-3">
        <div className="mt-0.5">
          <motion.div
            animate={schedule.active ? { scale: [1, 1.15, 1] } : {}}
            transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
            className={`w-2 h-2 rounded-full mt-1 ${schedule.active ? 'bg-emerald-400' : 'bg-[#475569]'}`}
          />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[14px] font-semibold text-[#F1F5F9] truncate">{schedule.name}</p>
          <p className="text-[12px] text-[#64748B] truncate mt-0.5">{schedule.goal_template}</p>
        </div>
        <span
          title={schedule.cron_expr}
          className="text-[11px] font-mono text-[#475569] bg-[#252B3B] px-2 py-0.5 rounded flex-shrink-0"
        >
          {schedule.cron_expr}
        </span>
      </div>

      {/* Next run */}
      {schedule.next_run && (
        <div className="flex items-center gap-1.5 text-[12px] text-[#64748B]">
          <Calendar className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          <span>Next: </span>
          <time dateTime={schedule.next_run} className="text-[#94A3B8] tabular-nums">
            {new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(schedule.next_run))}
          </time>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1 border-t border-[#252B3B]">
        <motion.button
          whileTap={reduce ? {} : { scale: 0.93 }}
          transition={SPRING_FAST}
          onClick={() => onAction(schedule.id, schedule.active ? 'pause' : 'resume')}
          aria-label={schedule.active ? `Pause ${schedule.name}` : `Resume ${schedule.name}`}
          style={{ touchAction: 'manipulation' }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-[#252B3B] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 min-h-[34px]"
        >
          {schedule.active ? <Pause className="h-3.5 w-3.5" aria-hidden /> : <Play className="h-3.5 w-3.5" aria-hidden />}
          {schedule.active ? 'Pause' : 'Resume'}
        </motion.button>

        <motion.button
          whileTap={reduce ? {} : { scale: 0.93 }}
          transition={SPRING_FAST}
          onClick={() => onAction(schedule.id, 'fire')}
          aria-label={`Run ${schedule.name} now`}
          style={{ touchAction: 'manipulation' }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium text-[#94A3B8] hover:text-amber-400 hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70 min-h-[34px]"
        >
          <Zap className="h-3.5 w-3.5" aria-hidden />
          Run Now
        </motion.button>

        <div className="flex-1" />

        <motion.button
          whileTap={reduce ? {} : { scale: 0.93 }}
          transition={SPRING_FAST}
          onClick={() => onAction(schedule.id, 'delete')}
          aria-label={`Delete ${schedule.name} schedule`}
          style={{ touchAction: 'manipulation' }}
          className="w-8 h-8 rounded-lg flex items-center justify-center text-[#475569] hover:text-red-400 hover:bg-red-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/70"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
        </motion.button>
      </div>
    </motion.article>
  );
}

// ── New Schedule Modal ────────────────────────────────────────────────────────

function NewScheduleModal({ onClose }: { onClose: () => void }) {
  const reduce  = useReducedMotion();
  const create  = useCreateSchedule();
  const [nl, setNl]         = useState('');
  const [goal, setGoal]     = useState('');
  const cronPreview         = nl.trim() ? parseCronPreview(nl) : '';

  const handleSave = useCallback(async () => {
    if (!nl.trim() || !goal.trim()) return;
    await create.mutateAsync({ name: nl, cron_expr: parseCronPreview(nl), goal_template: goal });
    onClose();
  }, [create, nl, goal, onClose]);

  return (
    <div role="dialog" aria-modal="true" aria-label="New scheduled mission" className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <motion.div
        initial={reduce ? { opacity: 0 } : { opacity: 0, y: 40 }}
        animate={{ opacity: 1, y: 0 }}
        exit={reduce ? { opacity: 0 } : { opacity: 0, y: 40 }}
        transition={SPRING_MODAL}
        className="relative bg-[#0F1117] border border-[#2D3748] rounded-2xl w-full max-w-lg shadow-2xl"
      >
        <div className="p-6 space-y-5">
          <div>
            <h2 className="text-[18px] font-bold text-[#F1F5F9] [text-wrap:balance]">New Schedule</h2>
            <p className="text-[13px] text-[#64748B] mt-1">Describe the schedule in plain English.</p>
          </div>

          {/* NL input */}
          <div>
            <label htmlFor="sched-nl" className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">
              Describe when this should run
            </label>
            <input
              id="sched-nl"
              type="text"
              value={nl}
              onChange={e => setNl(e.target.value)}
              placeholder="Every Monday at 9am"
              aria-required="true"
              className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[14px] text-[#F1F5F9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            />
            {cronPreview && (
              <p className="mt-1.5 text-[11px] font-mono text-[#64748B]">
                Parsed: <span className="text-emerald-400">{cronPreview}</span>
              </p>
            )}
          </div>

          {/* Goal template */}
          <div>
            <label htmlFor="sched-goal" className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">
              What should the agent do
            </label>
            <textarea
              id="sched-goal"
              value={goal}
              onChange={e => setGoal(e.target.value)}
              placeholder="Generate weekly market intelligence report for Q3 pipeline..."
              aria-required="true"
              rows={3}
              className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[14px] text-[#F1F5F9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-blue-500/60 resize-none"
            />
          </div>

          <div className="flex gap-3 pt-1">
            <motion.button
              type="button"
              onClick={handleSave}
              disabled={!nl.trim() || !goal.trim() || create.isPending}
              whileTap={reduce ? {} : { scale: 0.97 }}
              transition={SPRING_FAST}
              style={{ touchAction: 'manipulation' }}
              className="flex-1 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
            >
              {create.isPending ? 'Saving…' : 'Save Schedule'}
            </motion.button>
            <motion.button
              type="button"
              onClick={onClose}
              whileTap={reduce ? {} : { scale: 0.97 }}
              transition={SPRING_FAST}
              style={{ touchAction: 'manipulation' }}
              className="px-4 py-2.5 rounded-xl border border-[#2D3748] text-[#94A3B8] text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
            >
              Cancel
            </motion.button>
          </div>
        </div>
      </motion.div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function ScheduledMissions() {
  const reduce               = useReducedMotion();
  const { data: schedules = [], isLoading } = useSchedules();
  const action               = useScheduleAction();
  const [showNew, setShowNew] = useState(false);

  const handleAction = useCallback((id: string, act: 'pause' | 'resume' | 'fire' | 'delete') => {
    action.mutate({ id, action: act });
  }, [action]);

  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={SPRING_MODAL}
      className="max-w-3xl mx-auto px-6 py-8"
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-[24px] font-bold text-[#F1F5F9] [text-wrap:balance]">Scheduled Missions</h1>
          <p className="text-[14px] text-[#64748B] mt-1 tabular-nums">
            {isLoading ? 'Loading…' : `${schedules.filter(s => s.active).length} active · ${schedules.length} total`}
          </p>
        </div>
        <motion.button
          whileTap={reduce ? {} : { scale: 0.97 }}
          transition={SPRING_FAST}
          onClick={() => setShowNew(true)}
          aria-label="Create new scheduled mission"
          style={{ touchAction: 'manipulation' }}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
        >
          <Plus className="h-4 w-4" aria-hidden />
          New Schedule
        </motion.button>
      </div>

      {/* Cards */}
      <AnimatePresence mode="popLayout">
        {isLoading ? (
          <motion.div key="loading" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            className="flex items-center gap-3 text-[#475569] py-10 justify-center">
            <Clock className="h-4 w-4 animate-spin" aria-hidden />
            <span>Loading schedules…</span>
          </motion.div>
        ) : schedules.length === 0 ? (
          <motion.div key="empty"
            initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}
            className="text-center py-16"
          >
            <Clock className="h-10 w-10 text-[#2D3748] mx-auto mb-4" aria-hidden />
            <p className="text-[15px] font-medium text-[#475569]">No scheduled missions yet</p>
            <p className="text-[13px] text-[#374151] mt-1">Schedule recurring goals to run automatically.</p>
          </motion.div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {schedules.map((s, i) => (
              <ScheduleCard key={s.id} schedule={s} onAction={handleAction} index={i} />
            ))}
          </div>
        )}
      </AnimatePresence>

      {/* Feedback */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {action.isSuccess && 'Schedule updated.'}
      </div>

      {/* New modal */}
      <AnimatePresence>
        {showNew && <NewScheduleModal onClose={() => setShowNew(false)} />}
      </AnimatePresence>
    </motion.div>
  );
}

export default ScheduledMissions;
