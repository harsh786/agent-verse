/**
 * MissionGantt — horizontal phase ribbon showing a mission's execution timing.
 *
 * Each phase renders as a segment sized proportional to its duration. The
 * still-open phase (the one with `duration_ms == null`) grows live — a light
 * interval recomputes "elapsed since phase start" every second so the ribbon
 * visibly advances while the mission runs — gated by prefers-reduced-motion
 * (no interval, no pulse) and cleaned up on unmount / phase change.
 */
import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useReducedMotion } from 'framer-motion';
import { Clock3, AlertTriangle, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { situationApi } from '@/features/org/api';

interface MissionGanttProps {
  orgId:      string;
  missionId:  string;
  className?: string;
}

const QUERY_KEY = (orgId: string, missionId: string) =>
  ['org-mission-timeline', orgId, missionId] as const;

/** Per-phase-name color (JARVIS palette). Any phase name the backend doesn't
 *  name explicitly falls back to a sequential ramp keyed by position, so a
 *  future phase name renders sensibly without a frontend change. */
const PHASE_COLOR: Record<string, string> = {
  created:   '#475569',
  queued:    '#64748B',
  planning:  '#00D4FF',
  planned:   '#00D4FF',
  executing: '#F59E0B',
  execution: '#F59E0B',
  review:    '#818CF8',
  done:      '#10B981',
  completed: '#10B981',
  failed:    '#EF4444',
};
const RAMP = ['#00D4FF', '#818CF8', '#F59E0B', '#10B981', '#EC4899', '#64748B'];

function colorFor(name: string, index: number): string {
  return PHASE_COLOR[name.toLowerCase()] ?? RAMP[index % RAMP.length];
}

/** "1h 2m 3s" / "5m 10s" / "42s" — omits leading zero units. */
function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || ms < 0) return '—';
  const totalSeconds = Math.floor(ms / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const parts: string[] = [];
  if (h > 0) parts.push(`${h}h`);
  if (h > 0 || m > 0) parts.push(`${m}m`);
  parts.push(`${s}s`);
  return parts.join(' ');
}

export function MissionGantt({ orgId, missionId, className }: MissionGanttProps) {
  const reduce = useReducedMotion();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: QUERY_KEY(orgId, missionId),
    queryFn:  () => situationApi.missionTimeline(orgId, missionId),
  });

  const openPhaseAt = data?.phases.find(p => p.duration_ms == null)?.at ?? null;

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!openPhaseAt || reduce) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [openPhaseAt, reduce]);

  const rows = useMemo(() => {
    if (!data) return [];
    return data.phases.map((phase) => {
      const isOpen = phase.duration_ms == null;
      const durationMs = isOpen
        ? Math.max(0, now - new Date(phase.at).getTime())
        : (phase.duration_ms as number);
      return { phase, durationMs, isOpen };
    });
  }, [data, now]);

  const totalForWidths = useMemo(
    () => rows.reduce((sum, r) => sum + Math.max(r.durationMs, 0), 0) || 1,
    [rows],
  );

  return (
    <section className={cn('flex flex-col gap-2', className)} aria-label="Mission timing">
      <div className="flex items-center gap-2">
        <Clock3 className="h-3.5 w-3.5 text-[#00D4FF]" aria-hidden />
        <h3 className="text-[11px] font-medium uppercase tracking-[0.08em] text-[#475569]">
          Timeline
        </h3>
        {data?.total_ms != null && (
          <span className="ml-auto text-[11px] font-mono text-[#94A3B8] tabular-nums">
            {formatDuration(data.total_ms)}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-2 text-[12px] text-[#475569]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Loading timeline…
        </div>
      ) : isError ? (
        <div className="flex items-center gap-2 py-2 text-[12px] text-red-400">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          Failed to load timeline{error instanceof Error ? `: ${error.message}` : '.'}
        </div>
      ) : !data || data.phases.length === 0 ? (
        <p className="py-2 text-[12px] text-[#475569]">No timeline yet</p>
      ) : (
        <>
          <div
            role="img"
            aria-label={`Mission phases: ${data.phases.map(p => p.name).join(', ')}`}
            className="flex h-6 w-full overflow-hidden rounded-md ring-1 ring-[#1E2535]"
          >
            {rows.map(({ phase, durationMs, isOpen }, i) => {
              const widthPct = Math.max((durationMs / totalForWidths) * 100, 2);
              const color = colorFor(phase.name, i);
              const label = [phase.name, formatDuration(durationMs), phase.agent]
                .filter(Boolean)
                .join(' · ');
              return (
                <div
                  key={`${phase.name}-${phase.at}-${i}`}
                  title={label}
                  style={{ width: `${widthPct}%`, backgroundColor: color }}
                  className={cn(
                    'relative flex items-center justify-center overflow-hidden',
                    i > 0 && 'border-l border-[#0B0E14]/60',
                    isOpen && !reduce && 'animate-pulse',
                  )}
                >
                  <span className="truncate px-1 text-[9px] font-medium text-[#0B0E14]/80">
                    {phase.name}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Legend: name + duration + agent, spelled out (segments can be too
              narrow to read their own label, and hover-only info fails touch). */}
          <ul className="flex flex-wrap gap-x-3 gap-y-1">
            {rows.map(({ phase, durationMs, isOpen }, i) => (
              <li
                key={`${phase.name}-${i}-legend`}
                className="flex items-center gap-1.5 text-[10px] text-[#64748B]"
              >
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: colorFor(phase.name, i) }}
                  aria-hidden
                />
                <span className="capitalize">{phase.name}</span>
                <span className="font-mono tabular-nums text-[#475569]">
                  {formatDuration(durationMs)}
                </span>
                {phase.agent && <span className="text-[#475569]">· {phase.agent}</span>}
                {isOpen && <span className="text-[#00D4FF]">· live</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export default MissionGantt;
