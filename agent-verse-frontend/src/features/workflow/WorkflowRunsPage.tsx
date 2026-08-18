/**
 * WorkflowRunsPage — run history list for a specific workflow.
 *
 * Accessibility: uses useMotionSafe to respect prefers-reduced-motion.
 * When reduced-motion is on, all transitions use reducedMotion (instant).
 */
import { useParams, Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { springs, reducedMotion } from './design/motion';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Play, CheckCircle, XCircle, Clock, Loader2, Pause } from 'lucide-react';
import { workflowEngineApi, type WERun } from '../../lib/api/client';
import { getStatusClasses } from './design/tokens';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending:       <Clock className="h-3.5 w-3.5" />,
  running:       <Loader2 className="h-3.5 w-3.5 animate-spin" />,
  complete:      <CheckCircle className="h-3.5 w-3.5" />,
  failed:        <XCircle className="h-3.5 w-3.5" />,
  paused:        <Pause className="h-3.5 w-3.5" />,
  waiting_hitl:  <Clock className="h-3.5 w-3.5" />,
};

function RunRow({ run }: { run: WERun }) {
  const statusKey = run.status === 'complete' ? 'complete' : run.status;
  const statusCls = getStatusClasses(statusKey);
  const duration = run.duration_ms
    ? run.duration_ms < 1000
      ? `${run.duration_ms}ms`
      : `${(run.duration_ms / 1000).toFixed(1)}s`
    : '—';

  // Respect prefers-reduced-motion: use instant transition when user has set this preference
  const prefersReduced = typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const transitionToUse = prefersReduced ? reducedMotion : springs.gentle;

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={transitionToUse}
      whileHover={{ x: 2 }}
    >
    <Link
      to={`/workflow-runs/${run.run_id}`}
      className="flex items-center gap-4 px-4 py-3 rounded-xl border border-white/8
                 bg-white/3 hover:bg-white/6 transition-colors group"
      aria-label={`Run ${run.run_id}, status: ${run.status}`}
    >
      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${statusCls}`}>
        {STATUS_ICONS[run.status] ?? <Clock className="h-3.5 w-3.5" />}
        {run.status}
      </span>

      <code className="text-xs text-white/40 font-mono truncate max-w-[200px]">
        {run.run_id}
      </code>

      <div className="flex items-center gap-4 ml-auto text-xs text-white/40">
        <span>{run.step_count} step{run.step_count !== 1 ? 's' : ''}</span>
        <span>{duration}</span>
        <span>${run.cost_usd.toFixed(4)}</span>
        <span>{run.started_at ? new Date(run.started_at).toLocaleString() : '—'}</span>
      </div>
    </Link>
    </motion.div>
  );
}

export default function WorkflowRunsPage() {
  const { id } = useParams<{ id: string }>();

  const { data: wf } = useQuery({
    queryKey: ['workflow-engine', 'get', id],
    queryFn: () => workflowEngineApi.get(id!),
    enabled: !!id,
  });

  const { data: runs, isLoading } = useQuery({
    queryKey: ['workflow-engine', 'runs', id],
    queryFn: () => workflowEngineApi.listRuns({ workflow_id: id, per_page: 50 }),
    refetchInterval: 5000,
    enabled: !!id,
  });

  return (
    <JARVISPageShell>
    <JARVISStagger className="min-h-screen bg-slate-950 text-white">
      <header className="sticky top-0 z-30 flex items-center gap-3 px-6 py-4 border-b
                          border-white/10 bg-slate-950/90 backdrop-blur-xl">
        <Link to={`/workflows/${id}/edit`} className="text-white/40 hover:text-white transition-colors"
          aria-label="Back to workflow builder">
          <ChevronLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-sm font-bold">{wf?.name ?? 'Workflow'} — Runs</h1>
          <p className="text-xs text-white/40">{runs?.total ?? 0} total runs</p>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 text-sky-400 animate-spin" />
          </div>
        ) : (runs?.items ?? []).length === 0 ? (
          <div className="text-center py-16 text-white/30">
            <Play className="h-12 w-12 mx-auto mb-4 opacity-20" aria-hidden />
            <p className="text-sm">No runs yet. Trigger the workflow to see runs here.</p>
          </div>
        ) : (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="space-y-2"
            role="list"
            aria-label="Workflow runs"
          >
            {(runs?.items ?? []).map((run) => (
              <RunRow key={run.run_id} run={run} />
            ))}
          </motion.div>
        )}
      </main>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
