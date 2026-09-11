/**
 * RunCostSummary — cost, token count, and duration breakdown for a workflow run.
 */
import { DollarSign, Cpu, Timer, TrendingUp } from 'lucide-react';
import type { WERun } from '../../../lib/api/client';

interface Props {
  run: WERun;
  stepCount?: number;
}

export function RunCostSummary({ run, stepCount }: Props) {
  const durationMs = run.started_at && run.finished_at
    ? new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
    : null;

  const formatDuration = (ms: number) =>
    ms < 1000 ? `${ms}ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`;

  const stats = [
    {
      icon: <DollarSign className="h-4 w-4 text-amber-400" />,
      label: 'Total Cost',
      value: `$${run.cost_usd.toFixed(4)}`,
      sub: 'USD',
    },
    {
      icon: <Timer className="h-4 w-4 text-sky-400" />,
      label: 'Duration',
      value: durationMs != null ? formatDuration(durationMs) : '—',
      sub: 'wall clock',
    },
    {
      icon: <Cpu className="h-4 w-4 text-violet-400" />,
      label: 'Steps',
      value: String(stepCount ?? run.step_count),
      sub: 'executed',
    },
    {
      icon: <TrendingUp className="h-4 w-4 text-emerald-400" />,
      label: 'Status',
      value: run.status,
      sub: run.error ? 'with error' : 'clean',
    },
  ];

  return (
    <div
      className="grid grid-cols-2 lg:grid-cols-4 gap-3"
      role="list"
      aria-label="Run cost summary"
    >
      {stats.map(({ icon, label, value, sub }, idx) => (
        <div
          key={label}
          className="jarvis-pop-in rounded-xl border border-white/8 bg-[#0F1826]/3 px-4 py-3 flex items-center gap-3"
          style={{ animationDelay: `${Math.min(idx, 8) * 0.04}s` }}
          role="listitem"
        >
          <span aria-hidden>{icon}</span>
          <div>
            <p className="text-xs text-white/40">{label}</p>
            <p className="text-sm font-semibold text-white">{value}</p>
            <p className="text-xs text-white/25">{sub}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
