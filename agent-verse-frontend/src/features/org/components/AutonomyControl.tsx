/**
 * AutonomyControl — embeddable org-brain autonomy panel.
 *
 * NOT the same component as the top-level `src/features/org/AutonomyControl.tsx`
 * (a full-page L0–L5 selector built on JARVISPageShell + useUpdateOrganization,
 * which only persists `autonomy_level`). This is a self-contained panel meant to
 * be mounted inline (e.g. into OrgPage) that reads/writes the full org-brain
 * AutonomySettings via `orgAutonomyApi` — level, pause, caps, and collaboration.
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Pause, Play, AlertTriangle, Loader2, Users, Gauge,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { orgAutonomyApi } from '@/features/org/api';
import type { AutonomySettings } from '@/features/org/types';

const LEVELS = [1, 2, 3, 4, 5] as const;

interface AutonomyControlProps {
  orgId: string;
  className?: string;
}

/** Caps that are edited locally and committed as a batch. */
interface CapsForm {
  daily_budget_usd:     string;
  max_concurrent:       string;
  max_missions_per_day: string;
  cadence_seconds:      string;
}

interface CollabForm {
  collaboration_daily_budget_usd: string;
}

function toCapsForm(s: AutonomySettings): CapsForm {
  return {
    daily_budget_usd:     String(s.daily_budget_usd),
    max_concurrent:       String(s.max_concurrent),
    max_missions_per_day: String(s.max_missions_per_day),
    cadence_seconds:      String(s.cadence_seconds),
  };
}

function toCollabForm(s: AutonomySettings): CollabForm {
  return { collaboration_daily_budget_usd: String(s.collaboration_daily_budget_usd) };
}

/**
 * Parses a numeric form field, guarding against the two silent-corruption
 * cases: an emptied field (`Number('') === 0`) and non-numeric input
 * (`Number('abc') === NaN`, which JSON-serializes to `null`). Returns
 * `undefined` for either case so the caller can omit the field from the
 * patch rather than committing an unintended 0/null.
 */
function parseNumericField(raw: string): number | undefined {
  if (raw.trim() === '') return undefined;
  const n = Number(raw);
  return Number.isNaN(n) ? undefined : n;
}

export function AutonomyControl({ orgId, className }: AutonomyControlProps) {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['org-autonomy', orgId],
    queryFn: () => orgAutonomyApi.get(orgId),
  });

  const mutation = useMutation({
    mutationFn: (body: { autonomy_level?: number; settings?: Partial<AutonomySettings> }) =>
      orgAutonomyApi.patch(orgId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-autonomy', orgId] });
    },
  });

  const [caps, setCaps] = useState<CapsForm | null>(null);
  const [collab, setCollab] = useState<CollabForm | null>(null);

  // Seed the local edit buffers only once per org — on initial load, or when
  // `orgId` changes. Deliberately NOT on every `data` identity change: a
  // refetch triggered by an unrelated mutation (Pause, level select,
  // collaboration toggle) must not clobber in-progress, unsaved edits in the
  // Caps/Collaboration inputs. Each section resyncs itself from the server
  // response right after its OWN save succeeds (see handleSaveCaps /
  // handleSaveCollabBudget), so the buffers still reflect saved values.
  const initializedOrgIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (data?.settings && initializedOrgIdRef.current !== orgId) {
      setCaps(toCapsForm(data.settings));
      setCollab(toCollabForm(data.settings));
      initializedOrgIdRef.current = orgId;
    }
  }, [data, orgId]);

  if (isLoading) {
    return (
      <div className={cn('flex items-center gap-2 p-4 rounded-xl border border-[#1E2535] bg-[#0F1623] text-[#94A3B8] text-sm', className)}>
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading autonomy settings…
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className={cn('flex items-center gap-2 p-4 rounded-xl border border-red-500/30 bg-red-500/5 text-red-400 text-sm', className)}>
        <AlertTriangle className="h-4 w-4" aria-hidden />
        Failed to load autonomy settings{error instanceof Error ? `: ${error.message}` : '.'}
      </div>
    );
  }

  const { autonomy_level, settings } = data;
  const paused = settings.paused;

  const handleLevelSelect = (level: number) => {
    if (level === autonomy_level) return;
    mutation.mutate({ autonomy_level: level });
  };

  const handleTogglePause = () => {
    mutation.mutate({ settings: { paused: !paused } });
  };

  const handleToggleCollaboration = () => {
    mutation.mutate({ settings: { collaboration_enabled: !settings.collaboration_enabled } });
  };

  const handleSaveCaps = () => {
    if (!caps) return;
    const patch: Partial<AutonomySettings> = {};
    const dailyBudget = parseNumericField(caps.daily_budget_usd);
    if (dailyBudget !== undefined) patch.daily_budget_usd = dailyBudget;
    const maxConcurrent = parseNumericField(caps.max_concurrent);
    if (maxConcurrent !== undefined) patch.max_concurrent = maxConcurrent;
    const maxMissionsPerDay = parseNumericField(caps.max_missions_per_day);
    if (maxMissionsPerDay !== undefined) patch.max_missions_per_day = maxMissionsPerDay;
    const cadenceSeconds = parseNumericField(caps.cadence_seconds);
    if (cadenceSeconds !== undefined) patch.cadence_seconds = cadenceSeconds;

    // Every field was empty/non-numeric — nothing valid to save.
    if (Object.keys(patch).length === 0) return;

    mutation.mutate({ settings: patch }, {
      onSuccess: (result) => {
        // Resync the Caps buffer from the just-saved server state, not from
        // the generic query-data effect (which is intentionally skipped
        // after the initial load — see the effect above).
        if (result?.settings) setCaps(toCapsForm(result.settings));
      },
    });
  };

  const handleSaveCollabBudget = () => {
    if (!collab) return;
    const collaborationDailyBudget = parseNumericField(collab.collaboration_daily_budget_usd);

    // Empty/non-numeric — do not commit 0/null for a budget the user didn't intend.
    if (collaborationDailyBudget === undefined) return;

    mutation.mutate({
      settings: { collaboration_daily_budget_usd: collaborationDailyBudget },
    }, {
      onSuccess: (result) => {
        if (result?.settings) setCollab(toCollabForm(result.settings));
      },
    });
  };

  const capsDirty = caps !== null && (
    caps.daily_budget_usd     !== String(settings.daily_budget_usd) ||
    caps.max_concurrent       !== String(settings.max_concurrent) ||
    caps.max_missions_per_day !== String(settings.max_missions_per_day) ||
    caps.cadence_seconds      !== String(settings.cadence_seconds)
  );

  const collabBudgetDirty = collab !== null &&
    collab.collaboration_daily_budget_usd !== String(settings.collaboration_daily_budget_usd);

  return (
    <div className={cn('flex flex-col gap-5 rounded-xl border border-[#1E2535] bg-[#0B0F19] p-4', className)}>
      {/* ── Header ── */}
      <div>
        <h3 className="text-sm font-semibold text-[#F1F5F9] flex items-center gap-2">
          <Gauge className="h-4 w-4 text-[#00D4FF]" aria-hidden />
          Org Brain Autonomy
        </h3>
        <p className="text-[11px] text-[#475569] mt-0.5">
          Controls the autonomous tick loop: level, pause, spend/rate caps, and collaboration.
        </p>
      </div>

      {/* ── Prominent Pause toggle (kill-switch) ── */}
      <button
        type="button"
        onClick={handleTogglePause}
        disabled={mutation.isPending}
        aria-pressed={paused}
        style={{ touchAction: 'manipulation' }}
        className={cn(
          'w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold transition-all',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
          'min-h-[44px] border-2',
          paused
            ? 'bg-red-500/10 border-red-500 text-red-400 hover:bg-red-500/20'
            : 'bg-emerald-500/10 border-emerald-500 text-emerald-400 hover:bg-emerald-500/20',
          mutation.isPending && 'opacity-60 cursor-wait',
        )}
      >
        {paused ? (
          <><Play className="h-4 w-4" aria-hidden /> Resume Autonomy</>
        ) : (
          <><Pause className="h-4 w-4" aria-hidden /> Pause Autonomy</>
        )}
      </button>
      {paused && (
        <div className="flex items-center gap-2 -mt-3 text-[11px] text-red-400">
          <AlertTriangle className="h-3 w-3 flex-shrink-0" aria-hidden />
          The org brain is paused. No new autonomous ticks will run until resumed.
        </div>
      )}

      {/* ── Level selector L1–L5 ── */}
      <div>
        <p className="text-[10px] text-[#475569] uppercase tracking-wider mb-1.5">Autonomy level</p>
        <div className="grid grid-cols-5 gap-2" role="radiogroup" aria-label="Autonomy level">
          {LEVELS.map((level) => {
            const active = autonomy_level === level;
            return (
              <button
                key={level}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => handleLevelSelect(level)}
                disabled={mutation.isPending}
                style={{ touchAction: 'manipulation' }}
                className={cn(
                  'flex flex-col items-center gap-1 px-2 py-2.5 rounded-lg border text-xs font-bold transition-all',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
                  'min-h-[40px]',
                  active
                    ? 'bg-[#00D4FF]/10 border-[#00D4FF]/40 text-[#00D4FF]'
                    : 'border-[#1E2535] text-[#475569] hover:border-[#2E3545] hover:text-[#94A3B8]',
                )}
              >
                {`L${level}`}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Caps ── */}
      <div className="space-y-2">
        <p className="text-[10px] text-[#475569] uppercase tracking-wider">Caps</p>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-[11px] text-[#94A3B8]">
            Daily budget (USD)
            <input
              type="number"
              min={0}
              step="0.01"
              value={caps?.daily_budget_usd ?? ''}
              onChange={(e) => setCaps((prev) => prev && { ...prev, daily_budget_usd: e.target.value })}
              className="px-2 py-1.5 rounded-lg bg-[#0F1623] border border-[#1E2535] text-[#F1F5F9] text-sm focus:outline-none focus:ring-2 focus:ring-[#00D4FF]/50"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-[#94A3B8]">
            Max concurrent
            <input
              type="number"
              min={0}
              step="1"
              value={caps?.max_concurrent ?? ''}
              onChange={(e) => setCaps((prev) => prev && { ...prev, max_concurrent: e.target.value })}
              className="px-2 py-1.5 rounded-lg bg-[#0F1623] border border-[#1E2535] text-[#F1F5F9] text-sm focus:outline-none focus:ring-2 focus:ring-[#00D4FF]/50"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-[#94A3B8]">
            Max missions / day
            <input
              type="number"
              min={0}
              step="1"
              value={caps?.max_missions_per_day ?? ''}
              onChange={(e) => setCaps((prev) => prev && { ...prev, max_missions_per_day: e.target.value })}
              className="px-2 py-1.5 rounded-lg bg-[#0F1623] border border-[#1E2535] text-[#F1F5F9] text-sm focus:outline-none focus:ring-2 focus:ring-[#00D4FF]/50"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-[#94A3B8]">
            Cadence (seconds)
            <input
              type="number"
              min={0}
              step="1"
              value={caps?.cadence_seconds ?? ''}
              onChange={(e) => setCaps((prev) => prev && { ...prev, cadence_seconds: e.target.value })}
              className="px-2 py-1.5 rounded-lg bg-[#0F1623] border border-[#1E2535] text-[#F1F5F9] text-sm focus:outline-none focus:ring-2 focus:ring-[#00D4FF]/50"
            />
          </label>
        </div>
        <AnimatePresence>
          {capsDirty && (
            <motion.button
              type="button"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={handleSaveCaps}
              disabled={mutation.isPending}
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'w-full px-3 py-2 rounded-lg text-xs font-semibold transition-all',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
                'bg-[#00D4FF] text-[#0A0D14] hover:bg-[#00D4FF]/90',
                mutation.isPending && 'opacity-60 cursor-wait',
              )}
            >
              Save caps
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      {/* ── Collaboration ── */}
      <div className="space-y-2">
        <p className="text-[10px] text-[#475569] uppercase tracking-wider flex items-center gap-1.5">
          <Users className="h-3 w-3" aria-hidden /> Collaboration
        </p>
        <div className="flex items-center justify-between p-2.5 rounded-lg bg-[#0F1623] border border-[#1E2535]">
          <span className="text-sm text-[#94A3B8]">Enable cross-agent collaboration</span>
          <button
            type="button"
            role="switch"
            aria-checked={settings.collaboration_enabled}
            aria-label="Enable cross-agent collaboration"
            onClick={handleToggleCollaboration}
            disabled={mutation.isPending}
            style={{ touchAction: 'manipulation' }}
            className={cn(
              'relative w-9 h-5 rounded-full transition-colors flex-shrink-0',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
              settings.collaboration_enabled ? 'bg-[#00D4FF]' : 'bg-[#1E2535]',
            )}
          >
            <span
              className={cn(
                'absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform',
                settings.collaboration_enabled ? 'translate-x-4' : 'translate-x-0.5',
              )}
            />
          </button>
        </div>
        <label className="flex flex-col gap-1 text-[11px] text-[#94A3B8]">
          Collaboration daily budget (USD)
          <input
            type="number"
            min={0}
            step="0.01"
            value={collab?.collaboration_daily_budget_usd ?? ''}
            onChange={(e) => setCollab((prev) => prev && { ...prev, collaboration_daily_budget_usd: e.target.value })}
            className="px-2 py-1.5 rounded-lg bg-[#0F1623] border border-[#1E2535] text-[#F1F5F9] text-sm focus:outline-none focus:ring-2 focus:ring-[#00D4FF]/50"
          />
        </label>
        <AnimatePresence>
          {collabBudgetDirty && (
            <motion.button
              type="button"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={handleSaveCollabBudget}
              disabled={mutation.isPending}
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'w-full px-3 py-2 rounded-lg text-xs font-semibold transition-all',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
                'bg-[#00D4FF] text-[#0A0D14] hover:bg-[#00D4FF]/90',
                mutation.isPending && 'opacity-60 cursor-wait',
              )}
            >
              Save collaboration budget
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      {mutation.isError && (
        <div className="flex items-center gap-2 p-2.5 rounded-lg bg-red-500/5 border border-red-500/30 text-[11px] text-red-400">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          Failed to save changes{mutation.error instanceof Error ? `: ${mutation.error.message}` : '.'}
        </div>
      )}
    </div>
  );
}

export default AutonomyControl;
