import { AlertTriangle, CheckCircle2, CircleDashed, Sparkles } from 'lucide-react';
import type { ResultArtifact } from '../resultArtifact';
import { GoalResultActions } from './GoalResultActions';

const statusTone = {
  success: {
    Icon: CheckCircle2,
    label: 'success',
    shell: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100',
    icon: 'text-emerald-300',
    glow: 'bg-emerald-400/20',
  },
  partial: {
    Icon: AlertTriangle,
    label: 'partial',
    shell: 'border-amber-500/30 bg-amber-500/10 text-amber-100',
    icon: 'text-amber-300',
    glow: 'bg-amber-400/20',
  },
  failed: {
    Icon: AlertTriangle,
    label: 'failed',
    shell: 'border-red-500/30 bg-red-500/10 text-red-100',
    icon: 'text-red-300',
    glow: 'bg-red-400/20',
  },
  empty: {
    Icon: CircleDashed,
    label: 'empty',
    shell: 'border-slate-500/30 bg-slate-500/10 text-slate-100',
    icon: 'text-slate-300',
    glow: 'bg-slate-400/20',
  },
} satisfies Record<
  ResultArtifact['status'],
  {
    Icon: typeof CheckCircle2;
    label: string;
    shell: string;
    icon: string;
    glow: string;
  }
>;

export function GoalOutcomeHero({
  goal,
  status,
  artifact,
  onRerun,
}: {
  goal: string;
  status: string;
  artifact: ResultArtifact;
  onRerun: () => void;
}) {
  const tone = statusTone[artifact.status];
  const Icon = tone.Icon;

  return (
    <section className="relative overflow-hidden rounded-2xl border border-border bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 p-5 text-white shadow-lg">
      <div className={`absolute right-0 top-0 h-48 w-48 rounded-full ${tone.glow} blur-3xl`} />
      <div className="absolute bottom-0 left-10 h-32 w-32 rounded-full bg-blue-500/10 blur-3xl" />

      <div className="relative flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-blue-100 ring-1 ring-white/10">
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              Agent result
            </span>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wide ${tone.shell}`}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              {tone.label}
            </span>
            <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-slate-300 ring-1 ring-white/10">
              Goal status: {status.replace(/_/g, ' ')}
            </span>
          </div>

          <div className="flex items-start gap-3">
            <div className="mt-1 rounded-2xl bg-white/10 p-2 ring-1 ring-white/10">
              <Icon className={`h-6 w-6 ${tone.icon}`} aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">{artifact.title}</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">{artifact.summary}</p>
              <div className="mt-3 rounded-xl bg-white/5 px-3 py-2 text-xs text-slate-300 ring-1 ring-white/10">
                <span className="font-medium text-slate-100">Goal:</span> {goal}
              </div>
            </div>
          </div>

          {artifact.metrics.length > 0 && (
            <dl className="flex flex-wrap gap-2">
              {artifact.metrics.map((metric) => (
                <div key={metric.label} className="rounded-xl bg-white/10 px-4 py-3 ring-1 ring-white/10">
                  <dt className="text-xs font-medium text-slate-300">{metric.label}</dt>
                  <dd className="mt-1 text-xl font-semibold tabular-nums text-white">{metric.value}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>

        <div className="rounded-2xl bg-background/95 p-3 text-foreground shadow-2xl ring-1 ring-white/10 backdrop-blur lg:max-w-sm">
          <GoalResultActions artifact={artifact} onRerun={onRerun} goal={goal} />
        </div>
      </div>
    </section>
  );
}
