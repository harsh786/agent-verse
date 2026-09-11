/**
 * WorkflowRunDetailPage — step-by-step run timeline with outputs.
 */
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ChevronLeft, CheckCircle, XCircle, Clock, Loader2,
  DollarSign, Cpu, Timer, ChevronDown, ChevronRight,
} from 'lucide-react';
import { useState } from 'react';
import { workflowEngineApi, type WEStepResult } from '../../lib/api/client';
import { getStatusClasses } from './design/tokens';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

// ── Step result row ───────────────────────────────────────────────────────────

function StepResultRow({ step }: { step: WEStepResult }) {
  const [expanded, setExpanded] = useState(false);
  const statusCls = getStatusClasses(step.status === 'complete' ? 'complete' : step.status);
  const duration = step.duration_ms
    ? step.duration_ms < 1000 ? `${step.duration_ms}ms` : `${(step.duration_ms / 1000).toFixed(1)}s`
    : null;

  return (
    <div className="border border-white/8 rounded-xl overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-[#0A0D14]/4 transition-colors"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label={`Step ${step.step_id} — ${step.status}`}
      >
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusCls}`}>
          {step.status === 'complete' ? <CheckCircle className="h-3 w-3" />
            : step.status === 'failed' ? <XCircle className="h-3 w-3" />
            : <Clock className="h-3 w-3" />}
          {step.status}
        </span>

        <span className="text-sm text-[#F1F5F9] font-medium">{step.step_id}</span>
        <span className="text-xs text-[#F1F5F9]/40">{step.step_type}</span>

        {duration && <span className="ml-auto text-xs text-[#F1F5F9]/30">{duration}</span>}
        {expanded
          ? <ChevronDown className="h-4 w-4 text-[#F1F5F9]/30 ml-2" />
          : <ChevronRight className="h-4 w-4 text-[#F1F5F9]/30 ml-2" />}
      </button>

      {expanded && (
        <div className="border-t border-white/8 px-4 py-3 bg-[#0F1826]/2">
          {step.error && (
            <div className="mb-3 p-3 rounded-lg bg-red-500/10 border border-red-500/20">
              <p className="text-xs text-red-400 font-mono leading-relaxed">{step.error}</p>
            </div>
          )}
          {step.output && (
            <div>
              <p className="text-xs text-[#F1F5F9]/40 font-semibold uppercase tracking-wide mb-2">Output</p>
              <pre className="text-xs font-mono text-[#CBD5E1] bg-[#0F1117]/60 rounded-lg p-3
                              overflow-auto max-h-60 leading-relaxed">
                {JSON.stringify(step.output, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function WorkflowRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const qc = useQueryClient();

  const { data: run, isLoading } = useQuery({
    queryKey: ['workflow-engine', 'run', runId],
    queryFn: () => workflowEngineApi.getRun(runId!),
    enabled: !!runId,
    refetchInterval: (d) => {
      const status = d.state.data?.status;
      return status && ['running', 'pending', 'waiting_hitl'].includes(status) ? 3000 : false;
    },
  });

  const { data: steps } = useQuery({
    queryKey: ['workflow-engine', 'run-steps', runId],
    queryFn: () => workflowEngineApi.getRunSteps(runId!),
    enabled: !!runId,
    refetchInterval: run?.status === 'running' ? 2000 : false,
  });

  const cancelMutation = useMutation({
    mutationFn: () => workflowEngineApi.cancelRun(runId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workflow-engine', 'run', runId] }),
  });

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#060810] flex items-center justify-center">
        <Loader2 className="h-8 w-8 text-sky-400 animate-spin" />
      </div>
    );
  }

  if (!run) {
    return (
      <div className="min-h-screen bg-[#060810] flex items-center justify-center text-red-400"
        role="alert">
        Run not found.
      </div>
    );
  }

  const statusCls = getStatusClasses(run.status === 'complete' ? 'complete' : run.status);
  const durationMs = run.started_at && run.finished_at
    ? new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
    : null;

  return (
    <JARVISPageShell>

      {/* Accessibility: announce loading state */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">{isLoading ? "Loading…" : ""}</div>
    <JARVISStagger className="min-h-screen bg-[#060810] text-[#F1F5F9]">
      <header className="sticky top-0 z-30 flex items-center gap-3 px-6 py-4 border-b
                          border-white/10 bg-[#060810]/90 backdrop-blur-xl">
        <Link
          to={`/workflows/${run.workflow_id}/runs`}
          className="text-[#F1F5F9]/40 hover:text-[#F1F5F9] transition-colors"
          aria-label="Back to runs"
        >
          <ChevronLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-sm font-bold flex items-center gap-2">
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusCls}`}>
              {run.status}
            </span>
            Run Detail
          </h1>
          <code className="text-xs text-[#F1F5F9]/30 font-mono">{run.run_id}</code>
        </div>

        {run.status === 'running' && (
          <button
            onClick={() => cancelMutation.mutate()}
            className="ml-auto px-3 py-1.5 rounded-xl bg-red-500/15 hover:bg-red-500/25
                       text-red-400 text-xs font-medium transition-colors"
            aria-label="Cancel run"
          >
            Cancel
          </button>
        )}
      </header>

      <main
        className="jarvis-rise-in max-w-3xl mx-auto px-6 py-8 space-y-6"
      >
        {/* Summary cards */}
        <div className="grid grid-cols-3 gap-3">
          {[
            {
              icon: <Timer className="h-4 w-4" />,
              label: 'Duration',
              value: durationMs != null
                ? durationMs < 1000 ? `${durationMs}ms` : `${(durationMs / 1000).toFixed(1)}s`
                : '—',
            },
            {
              icon: <DollarSign className="h-4 w-4" />,
              label: 'Cost',
              value: `$${run.cost_usd.toFixed(4)}`,
            },
            {
              icon: <Cpu className="h-4 w-4" />,
              label: 'Steps',
              value: String(run.step_count),
            },
          ].map(({ icon, label, value }) => (
            <div key={label}
              className="rounded-xl border border-white/8 bg-[#0F1826]/3 px-4 py-3
                         flex items-center gap-3">
              <span className="text-[#F1F5F9]/30">{icon}</span>
              <div>
                <p className="text-xs text-[#F1F5F9]/40">{label}</p>
                <p className="text-sm font-semibold text-[#F1F5F9]">{value}</p>
              </div>
            </div>
          ))}
        </div>

        {/* Outputs */}
        {Object.keys(run.outputs ?? {}).length > 0 && (
          <section aria-labelledby="outputs-heading">
            <h2 id="outputs-heading" className="text-sm font-semibold text-[#F1F5F9] mb-3">
              Outputs
            </h2>
            <pre className="text-xs font-mono text-[#CBD5E1] bg-[#0F1117]/60 rounded-xl p-4
                            overflow-auto max-h-48 leading-relaxed border border-white/8">
              {JSON.stringify(run.outputs, null, 2)}
            </pre>
          </section>
        )}

        {/* Error */}
        {run.error && (
          <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20" role="alert">
            <p className="text-xs font-semibold text-red-400 mb-1">Error</p>
            <p className="text-xs text-red-300 font-mono">{run.error}</p>
          </div>
        )}

        {/* Step timeline */}
        <section aria-labelledby="steps-heading">
          <h2 id="steps-heading" className="text-sm font-semibold text-[#F1F5F9] mb-3">
            Step Timeline
          </h2>
          {(steps ?? []).length === 0 ? (
            <p className="text-xs text-[#F1F5F9]/30">No step results yet.</p>
          ) : (
            <div className="space-y-2" role="list" aria-label="Step results">
              {(steps ?? []).map((step) => (
                <StepResultRow key={step.step_id} step={step} />
              ))}
            </div>
          )}
        </section>
      </main>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
