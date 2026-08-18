/**
 * LearningLedger — world-class learning record display.
 */
import { BookOpen, Award, TrendingUp, XCircle, Clock } from 'lucide-react';
import type { LearningRecord } from '../../lib/api/civilizationApi';

const STATUS_CONFIG: Record<string, {
  icon: React.ComponentType<{ className?: string }>;
  bg: string;
  border: string;
  text: string;
  label: string;
}> = {
  candidate: {
    icon: Clock,
    bg: 'rgba(245,158,11,0.08)',
    border: 'rgba(245,158,11,0.2)',
    text: 'text-amber-400',
    label: 'Candidate',
  },
  validated: {
    icon: TrendingUp,
    bg: 'rgba(59,130,246,0.08)',
    border: 'rgba(59,130,246,0.2)',
    text: 'text-blue-400',
    label: 'Validated',
  },
  promoted: {
    icon: Award,
    bg: 'rgba(34,197,94,0.08)',
    border: 'rgba(34,197,94,0.2)',
    text: 'text-green-400',
    label: 'Promoted',
  },
  rejected: {
    icon: XCircle,
    bg: 'rgba(239,68,68,0.06)',
    border: 'rgba(239,68,68,0.15)',
    text: 'text-red-400',
    label: 'Rejected',
  },
};

function getStatusCfg(status: string) {
  return STATUS_CONFIG[status.toLowerCase()] ?? {
    icon: BookOpen,
    bg: 'rgba(99,102,241,0.08)',
    border: 'rgba(99,102,241,0.2)',
    text: 'text-indigo-400',
    label: status,
  };
}

export function LearningLedger({ records }: { records: LearningRecord[] }) {
  if (records.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center space-y-3">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center"
          style={{ background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.2)' }}
        >
          <BookOpen className="h-7 w-7 text-indigo-400" />
        </div>
        <div>
          <p className="text-sm font-medium text-slate-300">No learnings yet</p>
          <p className="text-xs text-slate-600 mt-1">
            The society accumulates learnings from completed goals
          </p>
        </div>
      </div>
    );
  }

  const promoted = records.filter(r => r.status === 'promoted').length;
  const total = records.length;

  return (
    <div className="space-y-3">
      {/* Summary */}
      <div className="flex items-center gap-3 text-xs">
        <span className="text-green-400 font-semibold">{promoted} promoted to LTM</span>
        <span className="w-1 h-1 rounded-full bg-slate-700" />
        <span className="text-slate-500">{total} total</span>
      </div>

      {records.map(r => {
        const cfg = getStatusCfg(r.status ?? 'candidate');
        const StatusIcon = cfg.icon;
        const scorePct = r.eval_score != null ? Math.round(r.eval_score * 100) : null;

        return (
          <div
            key={r.id}
            className="rounded-xl p-3 space-y-2"
            style={{ background: cfg.bg, border: `1px solid ${cfg.border}` }}
          >
            {/* Header */}
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <StatusIcon className={`h-3.5 w-3.5 ${cfg.text}`} />
                <span className={`text-[10px] font-semibold uppercase tracking-wide ${cfg.text}`}>
                  {cfg.label}
                </span>
              </div>
              <div className="flex items-center gap-2">
                {scorePct !== null && (
                  <span
                    className="text-[10px] font-semibold tabular-nums"
                    style={{ color: scorePct > 70 ? '#22c55e' : scorePct > 40 ? '#f59e0b' : '#ef4444' }}
                  >
                    {scorePct}%
                  </span>
                )}
                {r.promoted_memory_id && (
                  <span
                    className="text-[10px] px-1.5 py-px rounded-full"
                    style={{ background: 'rgba(34,197,94,0.15)', color: '#4ade80', border: '1px solid rgba(34,197,94,0.25)' }}
                  >
                    ✓ LTM
                  </span>
                )}
              </div>
            </div>

            {/* Candidate text */}
            <p className="text-xs text-slate-300 leading-relaxed">{r.candidate}</p>

            {/* Score bar */}
            {scorePct !== null && (
              <div className="h-1 bg-white/5 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-700"
                  style={{
                    width: `${scorePct}%`,
                    background: scorePct > 70 ? '#22c55e' : scorePct > 40 ? '#f59e0b' : '#ef4444',
                  }}
                />
              </div>
            )}

            {/* Source */}
            <div className="text-[10px] text-slate-600 font-mono">
              From: {r.source_agent_id?.slice(0, 10)}…
            </div>
          </div>
        );
      })}
    </div>
  );
}
