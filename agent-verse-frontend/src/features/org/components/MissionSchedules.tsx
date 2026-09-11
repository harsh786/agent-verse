/**
 * MissionSchedules — create & manage cron schedules that autonomously launch
 * org missions (Phase 1 of scheduled→publish). No human in the loop: when a
 * schedule is due, the backend beat task forms a team, runs the mission, and
 * produces a deliverable on its own.
 *
 * Skills:
 *   - frontend-design: JARVIS dark panel, friendly cadence presets over raw cron
 *   - impeccable-ui:   title dominant, cadence secondary, next-run tertiary
 *   - web-guidelines:  labelled inputs, aria, 44px targets, honest empty state
 */
import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import { CalendarClock, Loader2, Trash2, Plus, Zap, Play, Pause } from 'lucide-react';
import { cn } from '@/lib/utils';
import { orgApi } from '../api';
import type { OrgSchedule } from '../types';

interface MissionSchedulesProps {
  orgId: string;
}

// Friendly cadences → cron. "Custom" reveals a raw cron field.
const CADENCES: { label: string; cron: string }[] = [
  { label: 'Every morning · 8:00',       cron: '0 8 * * *' },
  { label: 'Every evening · 21:00',      cron: '0 21 * * *' },
  { label: 'Weekday mornings · 9:00',    cron: '0 9 * * 1-5' },
  { label: 'Every hour',                 cron: '0 * * * *' },
  { label: 'Every Monday · 9:00',        cron: '0 9 * * 1' },
];

const BROWSER_TZ = (() => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
})();

function relative(iso: string | null): string {
  if (!iso) return '—';
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return '—';
  const abs = Math.abs(ms);
  const mins = Math.round(abs / 60000);
  const hrs = Math.round(abs / 3_600_000);
  const days = Math.round(abs / 86_400_000);
  const unit = mins < 60 ? `${mins}m` : hrs < 48 ? `${hrs}h` : `${days}d`;
  return ms >= 0 ? `in ${unit}` : `${unit} ago`;
}

export function MissionSchedules({ orgId }: MissionSchedulesProps) {
  const qc = useQueryClient();
  const { data: schedules, isLoading } = useQuery({
    queryKey: ['org-schedules', orgId],
    queryFn: () => orgApi.listSchedules(orgId),
    refetchInterval: 30_000,
  });

  const [title, setTitle] = useState('');
  const [objective, setObjective] = useState('');
  const [cron, setCron] = useState(CADENCES[0].cron);
  const [customCron, setCustomCron] = useState('');
  const [custom, setCustom] = useState(false);
  const [error, setError] = useState('');

  const invalidate = useCallback(
    () => qc.invalidateQueries({ queryKey: ['org-schedules', orgId] }),
    [qc, orgId],
  );

  const createMut = useMutation({
    mutationFn: () =>
      orgApi.createSchedule(orgId, {
        title: title.trim(),
        objective: objective.trim(),
        cron_expression: custom ? customCron.trim() : cron,
        timezone: BROWSER_TZ,
      }),
    onSuccess: () => {
      setTitle(''); setObjective(''); setError('');
      invalidate();
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not create the schedule.'),
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      orgApi.toggleSchedule(orgId, id, enabled),
    onSuccess: invalidate,
  });
  const deleteMut = useMutation({
    mutationFn: (id: string) => orgApi.deleteSchedule(orgId, id),
    onSuccess: invalidate,
  });

  const canCreate = title.trim().length > 0 && (custom ? customCron.trim().length > 0 : true);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-[#1E2535] shrink-0">
        <div className="h-8 w-8 rounded-lg bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
          <CalendarClock className="h-4 w-4 text-violet-400" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-[15px] font-semibold text-[#F1F5F9] tracking-[-0.01em]">Scheduled Missions</h2>
          <p className="text-[11px] text-[#475569]">Missions that launch themselves on a cadence — no prompt needed.</p>
        </div>
      </div>

      {/* Create form */}
      <div className="px-4 py-4 space-y-2.5 border-b border-[#1E2535] shrink-0">
        <input
          value={title}
          onChange={(e) => { setTitle(e.target.value); setError(''); }}
          placeholder="Mission title (e.g. Draft today's LinkedIn post)"
          aria-label="Schedule mission title"
          className="w-full px-3 py-2 rounded-lg text-[13px] bg-[#0F1420] border border-[#2D3748] text-[#F1F5F9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
        />
        <textarea
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          placeholder="What should the team do each run? (optional)"
          rows={2}
          aria-label="Schedule mission objective"
          className="w-full px-3 py-2 rounded-lg text-[13px] bg-[#0F1420] border border-[#2D3748] text-[#F1F5F9] placeholder:text-[#475569] resize-none focus:outline-none focus:ring-2 focus:ring-blue-500/60"
        />
        <div className="flex items-center gap-2">
          <select
            value={custom ? 'custom' : cron}
            onChange={(e) => {
              if (e.target.value === 'custom') { setCustom(true); }
              else { setCustom(false); setCron(e.target.value); }
            }}
            aria-label="Cadence"
            className="flex-1 px-3 py-2 rounded-lg text-[13px] bg-[#0F1420] border border-[#2D3748] text-[#F1F5F9] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
          >
            {CADENCES.map((c) => <option key={c.cron} value={c.cron}>{c.label}</option>)}
            <option value="custom">Custom cron…</option>
          </select>
          <span className="text-[11px] text-[#64748B] shrink-0">{BROWSER_TZ}</span>
        </div>
        {custom && (
          <input
            value={customCron}
            onChange={(e) => setCustomCron(e.target.value)}
            placeholder="Cron, e.g. 30 7 * * 1-5"
            aria-label="Custom cron expression"
            className="w-full px-3 py-2 rounded-lg text-[13px] font-mono bg-[#0F1420] border border-[#2D3748] text-[#F1F5F9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
          />
        )}
        {error && <p role="alert" className="text-[12px] text-rose-400">{error}</p>}
        <button
          onClick={() => canCreate && createMut.mutate()}
          disabled={!canCreate || createMut.isPending}
          className={cn(
            'w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-[13px] font-semibold',
            'bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 disabled:cursor-not-allowed',
            'active:scale-[0.98] transition-[background-color,transform] duration-150',
          )}
        >
          {createMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Plus className="h-4 w-4" aria-hidden />}
          Schedule mission
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="space-y-2">{[...Array(3)].map((_, i) => <div key={i} className="h-16 rounded-xl bg-[#1A1F2E] animate-pulse" />)}</div>
        ) : !schedules || schedules.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <CalendarClock className="h-8 w-8 text-[#475569] mb-3" aria-hidden />
            <p className="text-[13px] text-[#94A3B8]">No scheduled missions yet.</p>
            <p className="text-[12px] text-[#475569] mt-1">Add one above and it will run on its own.</p>
          </div>
        ) : (
          <ul className="space-y-2">
            <AnimatePresence initial={false}>
              {schedules.map((s: OrgSchedule) => (
                <motion.li
                  key={s.id}
                  layout
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, height: 0 }}
                  className={cn(
                    'rounded-xl bg-[#1A1F2E] border p-3',
                    s.enabled ? 'border-[#1E2535]' : 'border-[#1E2535] opacity-60',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className={cn('flex h-6 w-6 items-center justify-center rounded-lg shrink-0',
                      s.enabled ? 'bg-emerald-500/10 text-emerald-400' : 'bg-[#252B3B] text-[#64748B]')}>
                      <Zap className="h-3 w-3" aria-hidden />
                    </span>
                    <span className="flex-1 min-w-0 text-[13px] font-medium text-[#F1F5F9] truncate">{s.title}</span>
                    <button
                      onClick={() => toggleMut.mutate({ id: s.id, enabled: !s.enabled })}
                      aria-label={s.enabled ? `Pause ${s.title}` : `Resume ${s.title}`}
                      title={s.enabled ? 'Pause' : 'Resume'}
                      className="p-1.5 rounded-lg text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-white/5 transition-colors"
                    >
                      {s.enabled ? <Pause className="h-3.5 w-3.5" aria-hidden /> : <Play className="h-3.5 w-3.5" aria-hidden />}
                    </button>
                    <button
                      onClick={() => deleteMut.mutate(s.id)}
                      aria-label={`Delete ${s.title}`}
                      title="Delete"
                      className="p-1.5 rounded-lg text-[#94A3B8] hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </div>
                  <div className="flex items-center gap-2 mt-1.5 pl-8 text-[11px] text-[#64748B]">
                    <span className="font-mono">{s.cron_expression}</span>
                    <span>·</span>
                    <span>{s.enabled ? `next ${relative(s.next_fire_at)}` : 'paused'}</span>
                    {s.fire_count > 0 && <><span>·</span><span>{s.fire_count} run{s.fire_count !== 1 ? 's' : ''}</span></>}
                  </div>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </div>
  );
}
