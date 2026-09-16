import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Workflow, Play, CheckCircle2, XCircle, Clock, BarChart2, Pause } from 'lucide-react';
import { apiFetch } from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';

interface WorkflowDef {
  id: string;
  name: string;
  description?: string;
  status: 'active' | 'paused' | 'draft';
  trigger_type: string;
  run_count: number;
  success_rate?: number;
  last_run_at?: string;
  created_at: string;
}

interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  started_at: string;
  completed_at?: string;
  duration_ms?: number;
}

const STATUS_STYLES: Record<string, { label: string; color: string; dot: string }> = {
  active:    { label: 'Active',    color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20', dot: 'bg-emerald-400' },
  paused:    { label: 'Paused',    color: 'text-amber-400 bg-amber-400/10 border-amber-400/20',       dot: 'bg-amber-400' },
  draft:     { label: 'Draft',     color: 'text-[#64748B] bg-[#64748B]/10 border-[#64748B]/20',       dot: 'bg-[#64748B]' },
  running:   { label: 'Running',   color: 'text-[#00D4FF] bg-[#00D4FF]/10 border-[#00D4FF]/20',      dot: 'bg-[#00D4FF] animate-pulse' },
  completed: { label: 'Completed', color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20', dot: 'bg-emerald-400' },
  failed:    { label: 'Failed',    color: 'text-rose-400 bg-rose-400/10 border-rose-400/20',          dot: 'bg-rose-400' },
  cancelled: { label: 'Cancelled', color: 'text-[#64748B] bg-[#64748B]/10 border-[#64748B]/20',       dot: 'bg-[#64748B]' },
};

function WorkflowCard({ wf, onRun, onToggle }: {
  wf: WorkflowDef;
  onRun: (id: string) => void;
  onToggle: (id: string, paused: boolean) => void;
}) {
  const st = STATUS_STYLES[wf.status];
  return (
    <JARVISStaggerItem interactive>
      <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E] p-4 hover:border-[#2D3748] transition-colors">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <Workflow className="h-4 w-4 text-[#00D4FF] shrink-0" />
              <h3 className="font-semibold text-[#F1F5F9] text-sm truncate">{wf.name}</h3>
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border ${st.color}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
                {st.label}
              </span>
            </div>
            {wf.description && (
              <p className="text-xs text-[#64748B] line-clamp-1 mb-2">{wf.description}</p>
            )}
            <div className="flex items-center gap-3 text-xs text-[#475569]">
              <span>{wf.trigger_type}</span>
              <span>{wf.run_count.toLocaleString()} runs</span>
              {wf.success_rate !== undefined && (
                <span className={wf.success_rate > 0.8 ? 'text-emerald-400' : 'text-amber-400'}>
                  {Math.round(wf.success_rate * 100)}% success
                </span>
              )}
              {wf.last_run_at && (
                <span>Last: {new Date(wf.last_run_at).toLocaleDateString()}</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={() => onToggle(wf.id, wf.status === 'active')}
              className="p-1.5 rounded-lg text-[#64748B] hover:text-amber-400 hover:bg-amber-400/10 transition-colors"
              title={wf.status === 'active' ? 'Pause' : 'Resume'}
            >
              {wf.status === 'active' ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            </button>
            <button
              onClick={() => onRun(wf.id)}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#00D4FF]/10 border border-[#00D4FF]/20 text-[#00D4FF] hover:bg-[#00D4FF]/20 text-xs font-medium transition-colors"
              title="Trigger run"
            >
              <Play className="h-3 w-3" /> Run
            </button>
          </div>
        </div>
      </div>
    </JARVISStaggerItem>
  );
}

// The workflow-engine list/runs endpoints may answer with either a bare array or
// a paginated `{ items, total }` envelope depending on backend version — normalise
// both to a list + total so the page and its "Load more" control work regardless.
function normalizeList<T>(raw: unknown): { items: T[]; total: number } {
  if (Array.isArray(raw)) return { items: raw as T[], total: raw.length };
  const obj = raw as { items?: T[]; total?: number } | null;
  const items = obj?.items ?? [];
  return { items, total: obj?.total ?? items.length };
}

export function WorkflowEnginePage() {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<'workflows' | 'runs'>('workflows');

  // Bounded server-side windows (were: no limit on workflows, fixed ?limit=20 on
  // runs with no way to see more). "Load more" grows the requested page size.
  const WF_PAGE = 30;
  const RUNS_PAGE = 20;
  const [wfLimit, setWfLimit] = useState(WF_PAGE);
  const [runsLimit, setRunsLimit] = useState(RUNS_PAGE);

  const { data: wfData, isLoading: wfLoading, isFetching: wfFetching } = useQuery({
    queryKey: ['workflow-engine-list', wfLimit],
    queryFn: () =>
      apiFetch<unknown>(`/api/v1/workflows?per_page=${wfLimit}`).then(normalizeList<WorkflowDef>),
    refetchInterval: 15_000,
  });
  const workflows = wfData?.items ?? [];
  const totalWorkflows = wfData?.total ?? workflows.length;
  const hasMoreWorkflows = workflows.length < totalWorkflows;

  const { data: runsData, isLoading: runsLoading, isFetching: runsFetching } = useQuery({
    queryKey: ['workflow-engine-runs', runsLimit],
    queryFn: () =>
      apiFetch<unknown>(`/api/v1/workflows/runs?limit=${runsLimit}`).then(normalizeList<WorkflowRun>),
    refetchInterval: 5_000,
    enabled: activeTab === 'runs',
  });
  const runs = runsData?.items ?? [];
  const totalRuns = runsData?.total ?? runs.length;
  const hasMoreRuns = runs.length < totalRuns;

  const triggerRun = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/api/v1/workflows/${id}/trigger`, { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workflow-engine-runs'] });
      setActiveTab('runs');
    },
  });

  const toggleWorkflow = useMutation({
    mutationFn: ({ id, pause }: { id: string; pause: boolean }) =>
      apiFetch(`/api/v1/workflows/${id}/${pause ? 'pause' : 'resume'}`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workflow-engine-list'] }),
  });

  const activeCount = workflows.filter((w) => w.status === 'active').length;
  const runningRuns = runs.filter((r) => r.status === 'running').length;
  const failedRuns = runs.filter((r) => r.status === 'failed').length;

  return (
    <JARVISPageShell>
      <div className="p-6 max-w-6xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#F1F5F9] flex items-center gap-2.5">
              <Workflow className="h-6 w-6 text-[#00D4FF]" />
              Workflow Engine
            </h1>
            <p className="text-sm text-[#64748B] mt-0.5">
              Event-driven workflow automation with full run history.
            </p>
          </div>
        </div>

        {/* KPI row */}
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Active Workflows', value: activeCount, color: 'text-emerald-400' },
            { label: 'Running Now',      value: runningRuns,  color: 'text-[#00D4FF]' },
            { label: 'Failed (24h)',     value: failedRuns,   color: 'text-rose-400' },
          ].map(({ label, value, color }) => (
            <div key={label} className="rounded-xl border border-[#1E2535] bg-[#1A1F2E] p-4">
              <p className="text-xs text-[#64748B] mb-1">{label}</p>
              <p className={`text-2xl font-bold tabular-nums ${color}`}>{wfLoading || runsLoading ? '—' : value}</p>
            </div>
          ))}
        </div>

        {/* Tabs */}
        <div className="flex border-b border-[#1E2535] gap-1">
          {(['workflows', 'runs'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 text-sm font-medium capitalize transition-colors border-b-2 -mb-px ${
                activeTab === tab
                  ? 'text-[#00D4FF] border-[#00D4FF]'
                  : 'text-[#64748B] border-transparent hover:text-[#94A3B8]'
              }`}
            >
              {tab === 'runs' ? 'Run History' : 'Workflows'}
            </button>
          ))}
        </div>

        {/* Workflows tab */}
        {activeTab === 'workflows' && (
          wfLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => <div key={i} className="h-20 rounded-xl bg-[#1A1F2E] animate-pulse border border-[#1E2535]" />)}
            </div>
          ) : workflows.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-[#475569] border border-[#1E2535] rounded-xl">
              <Workflow className="h-12 w-12 mb-3 opacity-20" />
              <p className="font-medium text-[#94A3B8]">No workflows configured</p>
              <p className="text-sm mt-1">Create workflows in the Workflow Builder.</p>
            </div>
          ) : (
            <>
              <JARVISStagger className="space-y-3">
                {workflows.map((wf) => (
                  <WorkflowCard
                    key={wf.id}
                    wf={wf}
                    onRun={(id) => triggerRun.mutate(id)}
                    onToggle={(id, pause) => toggleWorkflow.mutate({ id, pause })}
                  />
                ))}
              </JARVISStagger>
              {hasMoreWorkflows && (
                <div className="flex justify-center pt-2">
                  <button
                    onClick={() => setWfLimit((n) => n + WF_PAGE)}
                    disabled={wfFetching}
                    className="px-4 py-2 rounded-xl border border-[#1E2535] text-sm text-[#94A3B8]
                               hover:text-[#E2E8F0] hover:border-[#3D4D6A] transition-colors disabled:opacity-50"
                  >
                    {wfFetching ? 'Loading…' : `Load more (${workflows.length} of ${totalWorkflows})`}
                  </button>
                </div>
              )}
            </>
          )
        )}

        {/* Runs tab */}
        {activeTab === 'runs' && (
          runsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4].map((i) => <div key={i} className="h-14 rounded-xl bg-[#1A1F2E] animate-pulse border border-[#1E2535]" />)}
            </div>
          ) : runs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-[#475569] border border-[#1E2535] rounded-xl">
              <BarChart2 className="h-12 w-12 mb-3 opacity-20" />
              <p className="font-medium text-[#94A3B8]">No run history yet</p>
              <p className="text-sm mt-1">Trigger a workflow to see runs here.</p>
            </div>
          ) : (
            <>
            <JARVISStagger className="space-y-2">
              {runs.map((run) => {
                const st = STATUS_STYLES[run.status];
                const duration = run.duration_ms
                  ? run.duration_ms < 1000
                    ? `${run.duration_ms}ms`
                    : `${(run.duration_ms / 1000).toFixed(1)}s`
                  : null;
                return (
                  <JARVISStaggerItem key={run.run_id}>
                    <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E] px-4 py-3 flex items-center gap-3">
                      <span className={`h-2 w-2 rounded-full shrink-0 ${st.dot}`} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <code className="text-xs font-mono text-[#64748B]">{run.run_id.slice(0, 12)}…</code>
                          <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full border ${st.color}`}>{st.label}</span>
                        </div>
                        <p className="text-[11px] text-[#475569] mt-0.5">
                          {new Date(run.started_at).toLocaleString()}
                          {duration && <span className="ml-2">· {duration}</span>}
                        </p>
                      </div>
                      {run.status === 'running' ? (
                        <Clock className="h-4 w-4 text-[#00D4FF] animate-spin" />
                      ) : run.status === 'completed' ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                      ) : run.status === 'failed' ? (
                        <XCircle className="h-4 w-4 text-rose-400" />
                      ) : null}
                    </div>
                  </JARVISStaggerItem>
                );
              })}
            </JARVISStagger>
            {hasMoreRuns && (
              <div className="flex justify-center pt-2">
                <button
                  onClick={() => setRunsLimit((n) => n + RUNS_PAGE)}
                  disabled={runsFetching}
                  className="px-4 py-2 rounded-xl border border-[#1E2535] text-sm text-[#94A3B8]
                             hover:text-[#E2E8F0] hover:border-[#3D4D6A] transition-colors disabled:opacity-50"
                >
                  {runsFetching ? 'Loading…' : `Load more (${runs.length} of ${totalRuns})`}
                </button>
              </div>
            )}
            </>
          )
        )}
      </div>
    </JARVISPageShell>
  );
}

export default WorkflowEnginePage;
