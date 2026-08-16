/**
 * RunTimeline — vertical step-by-step execution timeline.
 *
 * Renders each step result with status icon, duration, cost.
 * Clicking a step opens StepOutputInspector.
 * Accessible: uses role="list" + aria-labels.
 */
import { useState } from 'react';
import {
  CheckCircle, XCircle, Clock, Loader2, ChevronDown, ChevronRight,
  UserCheck, Pause, SkipForward,
} from 'lucide-react';
import type { WEStepResult } from '../../../lib/api/client';

// ── Status config ─────────────────────────────────────────────────────────────

const STEP_STATUS = {
  complete: {
    icon: <CheckCircle className="h-4 w-4 text-emerald-400" />,
    lineColor: 'bg-emerald-500/30',
    ringColor: 'ring-emerald-500/30',
  },
  failed: {
    icon: <XCircle className="h-4 w-4 text-red-400" />,
    lineColor: 'bg-red-500/30',
    ringColor: 'ring-red-500/30',
  },
  running: {
    icon: <Loader2 className="h-4 w-4 text-sky-400 animate-spin" />,
    lineColor: 'bg-sky-500/30',
    ringColor: 'ring-sky-500/30',
  },
  waiting_hitl: {
    icon: <UserCheck className="h-4 w-4 text-rose-400" />,
    lineColor: 'bg-rose-500/30',
    ringColor: 'ring-rose-500/30',
  },
  paused: {
    icon: <Pause className="h-4 w-4 text-amber-400" />,
    lineColor: 'bg-amber-500/30',
    ringColor: 'ring-amber-500/30',
  },
  skipped: {
    icon: <SkipForward className="h-4 w-4 text-zinc-500" />,
    lineColor: 'bg-zinc-600/30',
    ringColor: 'ring-zinc-600/30',
  },
  pending: {
    icon: <Clock className="h-4 w-4 text-zinc-500" />,
    lineColor: 'bg-zinc-600/20',
    ringColor: 'ring-zinc-600/20',
  },
};

// ── Step row ──────────────────────────────────────────────────────────────────

function StepRow({
  step,
  isLast,
  onSelect,
  selected,
}: {
  step: WEStepResult;
  isLast: boolean;
  onSelect: (s: WEStepResult) => void;
  selected: boolean;
}) {
  const config = STEP_STATUS[step.status as keyof typeof STEP_STATUS] ?? STEP_STATUS.pending;
  const duration = step.duration_ms
    ? step.duration_ms < 1000
      ? `${step.duration_ms}ms`
      : `${(step.duration_ms / 1000).toFixed(1)}s`
    : null;

  return (
    <li className="relative flex gap-4" role="listitem">
      {/* Vertical line */}
      {!isLast && (
        <div className="absolute left-[15px] top-8 bottom-0 w-0.5 bg-white/10" aria-hidden />
      )}

      {/* Status dot */}
      <div className={`relative z-10 shrink-0 w-8 h-8 rounded-full flex items-center
                       justify-center ring-1 ${config.ringColor} bg-slate-900`}>
        {config.icon}
      </div>

      {/* Content */}
      <button
        className={`flex-1 flex items-start justify-between gap-3 py-1.5 pb-4
                    text-left transition-colors group ${selected ? 'opacity-100' : 'opacity-90 hover:opacity-100'}`}
        onClick={() => onSelect(step)}
        aria-expanded={selected}
        aria-label={`Step ${step.step_id}, status: ${step.status}`}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-white truncate">{step.step_id}</span>
            <span className="text-xs text-white/40">{step.step_type}</span>
          </div>
          {step.error && (
            <p className="text-xs text-red-400 mt-0.5 line-clamp-2 leading-tight">
              {step.error}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-white/30 shrink-0">
          {duration && <span>{duration}</span>}
          {selected
            ? <ChevronDown className="h-3.5 w-3.5" />
            : <ChevronRight className="h-3.5 w-3.5" />}
        </div>
      </button>
    </li>
  );
}

// ── Output inspector (inline) ─────────────────────────────────────────────────

function InlineOutput({ step }: { step: WEStepResult }) {
  return (
    <div className="ml-12 mb-4 rounded-xl border border-white/8 bg-slate-900/60 overflow-hidden">
      {step.error && (
        <div className="p-3 bg-red-950/40 border-b border-red-500/20">
          <p className="text-xs font-semibold text-red-400 mb-1">Error</p>
          <p className="text-xs text-red-300 font-mono leading-relaxed">{step.error}</p>
        </div>
      )}
      {step.output && (
        <div className="p-3">
          <p className="text-xs font-semibold text-white/40 mb-2 uppercase tracking-wide">Output</p>
          <pre className="text-xs font-mono text-slate-300 overflow-auto max-h-48 leading-relaxed">
            {JSON.stringify(step.output, null, 2)}
          </pre>
        </div>
      )}
      {!step.error && !step.output && (
        <div className="p-3 text-xs text-white/30">No output data</div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function RunTimeline({ steps }: { steps: WEStepResult[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (!steps.length) {
    return (
      <p className="text-xs text-white/30 py-4" role="status">
        No steps recorded yet.
      </p>
    );
  }

  return (
    <ul className="space-y-0" role="list" aria-label="Step execution timeline">
      {steps.map((step, idx) => {
        const isLast = idx === steps.length - 1;
        const isSelected = selectedId === step.step_id;
        return (
          <div key={step.step_id}>
            <StepRow
              step={step}
              isLast={isLast && !isSelected}
              onSelect={(s) => setSelectedId(isSelected ? null : s.step_id)}
              selected={isSelected}
            />
            {isSelected && <InlineOutput step={step} />}
          </div>
        );
      })}
    </ul>
  );
}
