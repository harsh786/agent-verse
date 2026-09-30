import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Workflow, Play, CheckCircle2, XCircle, Clock, BarChart2, Pause, X, ExternalLink, History, Loader2,
} from 'lucide-react';
import { apiFetch } from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';

interface WorkflowDef {
  id: string;
  name: string;
  description?: string;
  // Backend workflow lifecycle (app/workflow/service.py). 'active'/'paused' are
  // accepted for older payloads.
  status: 'published' | 'draft' | 'archived' | 'active' | 'paused';
  // null when the definition has no trigger (the backend's WorkflowResponse).
  trigger_type?: string | null;
  // Not always populated by the backend's WorkflowResponse (e.g. right after
  // creation), so callers must not assume it's present.
  run_count?: number;
  success_rate?: number;
  last_run_at?: string;
  created_at: string;
}

interface WorkflowStepDef {
  id: string;
  type?: string;
  depends_on?: string[];
}

/** GET /api/v1/workflows/{id} — WorkflowDetailResponse. */
interface WorkflowDetail extends WorkflowDef {
  version?: number;
  updated_at?: string;
  access?: string | null;
  definition?: {
    trigger?: { type?: string; schedule?: { cron?: string } };
    steps?: WorkflowStepDef[];
  };
}

/** GET /api/v1/runs items — RunDetailResponse (app/workflow/router_runs.py). */
interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  workflow_name?: string | null;
  // WorkflowRunStatus (app/workflow/state.py): pending, running, waiting_hitl,
  // waiting_timer, paused, complete, failed, cancelled, timed_out ('completed'
  // from older payloads) — treated as open-ended.
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  // Older payloads.
  completed_at?: string | null;
  duration_ms?: number | null;
  error?: string | null;
}

/** POST /api/v1/workflows/{id}/trigger → 202 RunResponse. */
interface TriggeredRun {
  run_id?: string;
  workflow_id?: string;
  status?: string;
}

interface StatusStyle { label: string; color: string; dot: string }

const STATUS_STYLES: Record<string, StatusStyle> = {
  active:    { label: 'Active',    color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20', dot: 'bg-emerald-400' },
  paused:    { label: 'Paused',    color: 'text-amber-400 bg-amber-400/10 border-amber-400/20',       dot: 'bg-amber-400' },
  draft:     { label: 'Draft',     color: 'text-[#64748B] bg-[#64748B]/10 border-[#64748B]/20',       dot: 'bg-[#64748B]' },
  published: { label: 'Active',    color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20', dot: 'bg-emerald-400' },
  archived:  { label: 'Archived',  color: 'text-[#64748B] bg-[#64748B]/10 border-[#64748B]/20',       dot: 'bg-[#64748B]' },
  pending:   { label: 'Pending',   color: 'text-sky-300 bg-sky-300/10 border-sky-300/20',             dot: 'bg-sky-300 animate-pulse' },
  queued:    { label: 'Queued',    color: 'text-sky-300 bg-sky-300/10 border-sky-300/20',             dot: 'bg-sky-300 animate-pulse' },
  running:   { label: 'Running',   color: 'text-[#00D4FF] bg-[#00D4FF]/10 border-[#00D4FF]/20',      dot: 'bg-[#00D4FF] animate-pulse' },
  waiting_hitl:  { label: 'Awaiting approval', color: 'text-amber-400 bg-amber-400/10 border-amber-400/20', dot: 'bg-amber-400' },
  waiting_timer: { label: 'Waiting (timer)',   color: 'text-amber-400 bg-amber-400/10 border-amber-400/20', dot: 'bg-amber-400' },
  complete:  { label: 'Completed', color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20', dot: 'bg-emerald-400' },
  completed: { label: 'Completed', color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20', dot: 'bg-emerald-400' },
  failed:    { label: 'Failed',    color: 'text-rose-400 bg-rose-400/10 border-rose-400/20',          dot: 'bg-rose-400' },
  timed_out: { label: 'Timed out', color: 'text-rose-400 bg-rose-400/10 border-rose-400/20',          dot: 'bg-rose-400' },
  cancelled: { label: 'Cancelled', color: 'text-[#64748B] bg-[#64748B]/10 border-[#64748B]/20',       dot: 'bg-[#64748B]' },
};

// A status the page has no style for (the engine grows new ones) must still
// render — an unknown key used to crash the whole page.
function statusStyle(status: string | undefined | null): StatusStyle {
  const key = (status ?? '').toLowerCase();
  const known = STATUS_STYLES[key];
  if (known) return known;
  const label = key ? key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, ' ') : 'Unknown';
  return { label, color: 'text-[#94A3B8] bg-[#94A3B8]/10 border-[#94A3B8]/20', dot: 'bg-[#94A3B8]' };
}

const TERMINAL_RUN_STATUSES = new Set(['complete', 'completed', 'failed', 'cancelled', 'timed_out']);
const isSucceeded = (status: string) => status === 'complete' || status === 'completed';
const isFailed = (status: string) => status === 'failed' || status === 'timed_out';
const isOpenRun = (r: WorkflowRun) => !TERMINAL_RUN_STATUSES.has(r.status);

// A workflow is "active" (its schedule/webhook triggers fire) when published.
function isActive(wf: WorkflowDef): boolean {
  return wf.status === 'published' || wf.status === 'active';
}

const runDetailPath = (r: { workflow_id: string; run_id: string }) =>
  `/workflows/${encodeURIComponent(r.workflow_id)}/runs/${encodeURIComponent(r.run_id)}`;

function formatDuration(ms: number | null | undefined): string | null {
  if (!ms) return null;
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function errorText(err: unknown): string {
  return err instanceof Error && err.message ? err.message : 'Request failed';
}

// The list/runs endpoints answer a paginated `{ items, total }` envelope; older
// backends sent a bare array — normalise both to a list + total so the page and
// its "Load more" control work regardless.
function normalizeList<T>(raw: unknown): { items: T[]; total: number } {
  if (Array.isArray(raw)) return { items: raw as T[], total: raw.length };
  const obj = raw as { items?: T[]; total?: number } | null;
  const items = obj?.items ?? [];
  return { items, total: obj?.total ?? items.length };
}

function RunRow({ run, showWorkflow }: { run: WorkflowRun; showWorkflow?: boolean }) {
  const st = statusStyle(run.status);
  const duration = formatDuration(run.duration_ms);
  return (
    <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E] px-4 py-3 flex items-center gap-3">
      <span className={`h-2 w-2 rounded-full shrink-0 ${st.dot}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <Link
            to={runDetailPath(run)}
            className="text-xs font-mono text-[#94A3B8] hover:text-[#00D4FF] hover:underline"
            title="Open run detail"
          >
            {run.run_id.slice(0, 12)}…
          </Link>
          <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full border ${st.color}`}>{st.label}</span>
          {showWorkflow && run.workflow_name && (
            <span className="text-[11px] text-[#64748B] truncate">{run.workflow_name}</span>
          )}
        </div>
        <p className="text-[11px] text-[#475569] mt-0.5">
          {run.started_at ? new Date(run.started_at).toLocaleString() : 'Not started yet'}
          {duration && <span className="ml-2">· {duration}</span>}
        </p>
        {run.error && <p className="text-[11px] text-rose-400/80 mt-0.5 line-clamp-1">{run.error}</p>}
      </div>
      {run.status === 'running' || run.status === 'pending' ? (
        <Clock className="h-4 w-4 text-[#00D4FF] animate-spin" />
      ) : isSucceeded(run.status) ? (
        <CheckCircle2 className="h-4 w-4 text-emerald-400" />
      ) : isFailed(run.status) ? (
        <XCircle className="h-4 w-4 text-rose-400" />
      ) : null}
    </div>
  );
}

function WorkflowCard({ wf, selected, onSelect, onRun, onToggle, running }: {
  wf: WorkflowDef;
  selected: boolean;
  onSelect: (id: string) => void;
  onRun: (id: string) => void;
  onToggle: (id: string, deactivate: boolean) => void;
  running: boolean;
}) {
  const st = statusStyle(wf.status);
  const active = isActive(wf);
  return (
    <JARVISStaggerItem interactive>
      <div
        className={`rounded-xl border bg-[#1A1F2E] p-4 transition-colors ${
          selected ? 'border-[#00D4FF]/50' : 'border-[#1E2535] hover:border-[#2D3748]'
        }`}
      >
        <div className="flex items-start justify-between gap-3">
          <button
            type="button"
            onClick={() => onSelect(wf.id)}
            aria-label={`View ${wf.name}`}
            aria-pressed={selected}
            className="flex-1 min-w-0 text-left"
          >
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
              {wf.trigger_type && <span>{wf.trigger_type}</span>}
              <span>{(wf.run_count ?? 0).toLocaleString()} runs</span>
              {wf.success_rate !== undefined && (
                <span className={wf.success_rate > 0.8 ? 'text-emerald-400' : 'text-amber-400'}>
                  {Math.round(wf.success_rate * 100)}% success
                </span>
              )}
              {wf.last_run_at && (
                <span>Last: {new Date(wf.last_run_at).toLocaleDateString()}</span>
              )}
            </div>
          </button>
          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={() => onToggle(wf.id, active)}
              className="p-1.5 rounded-lg text-[#64748B] hover:text-amber-400 hover:bg-amber-400/10 transition-colors"
              title={active ? 'Pause' : 'Resume'}
            >
              {active ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            </button>
            <button
              onClick={() => onRun(wf.id)}
              disabled={running}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#00D4FF]/10 border border-[#00D4FF]/20 text-[#00D4FF] hover:bg-[#00D4FF]/20 text-xs font-medium transition-colors disabled:opacity-50"
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

function WorkflowDetailPanel({ workflowId, onClose, onRun, running }: {
  workflowId: string;
  onClose: () => void;
  onRun: (id: string) => void;
  running: boolean;
}) {
  const { data: wf, isLoading, error } = useQuery({
    queryKey: ['workflow-engine-detail', workflowId],
    queryFn: () => apiFetch<WorkflowDetail>(`/api/v1/workflows/${encodeURIComponent(workflowId)}`),
  });

  const { data: runsData, isLoading: runsLoading } = useQuery({
    queryKey: ['workflow-engine-runs', 'workflow', workflowId],
    queryFn: () =>
      apiFetch<unknown>(
        `/api/v1/runs?workflow_id=${encodeURIComponent(workflowId)}&per_page=10`,
      ).then(normalizeList<WorkflowRun>),
    // Poll quickly while a run is still in flight, slowly otherwise.
    refetchInterval: (q) =>
      (q.state.data?.items ?? []).some(isOpenRun) ? 3_000 : 15_000,
  });
  const runs = runsData?.items ?? [];

  const name = wf?.name ?? 'Workflow';
  const steps = wf?.definition?.steps ?? [];
  const trigger = wf?.definition?.trigger;
  const st = statusStyle(wf?.status);
  const base = `/workflows/${encodeURIComponent(workflowId)}`;

  return (
    <section
      aria-label={`${name} details`}
      className="rounded-xl border border-[#00D4FF]/30 bg-[#12172A] p-5 space-y-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-[#F1F5F9] truncate">{name}</h2>
          {wf && (
            <p className="text-xs text-[#64748B] mt-0.5 flex items-center gap-2 flex-wrap">
              <span className={`px-2 py-0.5 rounded-full border ${st.color}`}>{st.label}</span>
              {wf.version != null && <span>v{wf.version}</span>}
              <span>Trigger: {trigger?.type ?? wf.trigger_type ?? 'none'}</span>
              {trigger?.schedule?.cron && <code className="font-mono">{trigger.schedule.cron}</code>}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => onRun(workflowId)}
            disabled={running}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#00D4FF] text-[#060810] text-xs font-semibold hover:opacity-90 disabled:opacity-50"
          >
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            Run now
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#64748B] hover:text-[#F1F5F9]"
            aria-label="Close workflow details"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="h-16 rounded-lg bg-[#1A1F2E] animate-pulse" />
      ) : error ? (
        <p role="alert" className="text-sm text-rose-400">Could not load this workflow: {errorText(error)}</p>
      ) : (
        <>
          {wf?.description && <p className="text-sm text-[#94A3B8]">{wf.description}</p>}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-2">
              Steps ({steps.length})
            </h3>
            {steps.length === 0 ? (
              <p className="text-sm text-[#64748B]">This workflow has no steps yet.</p>
            ) : (
              <ol className="space-y-1.5">
                {steps.map((s, i) => (
                  <li
                    key={s.id ?? i}
                    className="flex items-center gap-2 text-sm rounded-lg border border-[#1E2535] bg-[#1A1F2E] px-3 py-1.5"
                  >
                    <span className="text-[10px] text-[#475569] w-5 tabular-nums">{i + 1}.</span>
                    <code className="font-mono text-[#E2E8F0]">{s.id}</code>
                    {s.type && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#00D4FF]/10 text-[#00D4FF]">{s.type}</span>
                    )}
                    {s.depends_on && s.depends_on.length > 0 && (
                      <span className="text-[11px] text-[#475569] ml-auto">after {s.depends_on.join(', ')}</span>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </>
      )}

      <div className="flex items-center gap-3 text-xs">
        <Link to={`${base}/edit`} className="inline-flex items-center gap-1 text-[#00D4FF] hover:underline">
          <ExternalLink className="h-3.5 w-3.5" /> Open in builder
        </Link>
        <Link to={`${base}/runs`} className="inline-flex items-center gap-1 text-[#00D4FF] hover:underline">
          <History className="h-3.5 w-3.5" /> All runs
        </Link>
      </div>

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-2">Recent runs</h3>
        {runsLoading ? (
          <div className="h-12 rounded-lg bg-[#1A1F2E] animate-pulse" />
        ) : runs.length === 0 ? (
          <p className="text-sm text-[#64748B]">No runs yet — press Run now to start one.</p>
        ) : (
          <div className="space-y-2">
            {runs.map((r) => <RunRow key={r.run_id} run={r} />)}
          </div>
        )}
      </div>
    </section>
  );
}

export function WorkflowEnginePage() {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<'workflows' | 'runs'>('workflows');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<{ workflow_id: string; run_id: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Bounded server-side windows. "Load more" grows the requested page size
  // (the routes cap per_page at 100).
  const WF_PAGE = 30;
  const RUNS_PAGE = 20;
  const PER_PAGE_MAX = 100;
  const [wfLimit, setWfLimit] = useState(WF_PAGE);
  const [runsLimit, setRunsLimit] = useState(RUNS_PAGE);

  const { data: wfData, isLoading: wfLoading, isFetching: wfFetching } = useQuery({
    queryKey: ['workflow-engine-list', wfLimit],
    queryFn: () =>
      apiFetch<unknown>(`/api/v1/workflows?per_page=${Math.min(wfLimit, PER_PAGE_MAX)}`)
        .then(normalizeList<WorkflowDef>),
    refetchInterval: 15_000,
  });
  const workflows = wfData?.items ?? [];
  const totalWorkflows = wfData?.total ?? workflows.length;
  const hasMoreWorkflows = workflows.length < totalWorkflows && wfLimit < PER_PAGE_MAX;

  const { data: runsData, isLoading: runsLoading, isFetching: runsFetching } = useQuery({
    queryKey: ['workflow-engine-runs', 'all', runsLimit],
    // GET /api/v1/runs takes page/per_page (a `limit` param was silently ignored).
    queryFn: () =>
      apiFetch<unknown>(`/api/v1/runs?per_page=${Math.min(runsLimit, PER_PAGE_MAX)}`)
        .then(normalizeList<WorkflowRun>),
    refetchInterval: 5_000,
    enabled: activeTab === 'runs',
  });
  const runs = runsData?.items ?? [];
  const totalRuns = runsData?.total ?? runs.length;
  const hasMoreRuns = runs.length < totalRuns && runsLimit < PER_PAGE_MAX;

  const triggerRun = useMutation({
    mutationFn: (id: string) =>
      // TriggerRequest is a required JSON body; an empty POST was a 422.
      apiFetch<TriggeredRun>(`/api/v1/workflows/${encodeURIComponent(id)}/trigger`, {
        method: 'POST',
        body: JSON.stringify({ inputs: {} }),
      }),
    onMutate: () => {
      setActionError(null);
      setLastRun(null);
    },
    onSuccess: (run, id) => {
      // 202 {run_id, workflow_id, status: 'pending'} — the run executes async;
      // the run lists poll until it reaches a terminal status.
      if (run?.run_id) setLastRun({ workflow_id: run.workflow_id ?? id, run_id: run.run_id });
      qc.invalidateQueries({ queryKey: ['workflow-engine-runs'] });
      qc.invalidateQueries({ queryKey: ['workflow-engine-list'] });
      // With a workflow open, keep the user on it (its panel shows the new run);
      // otherwise show the global run history.
      if (selectedId !== id) setActiveTab('runs');
    },
    onError: (err) => setActionError(`Could not start the run: ${errorText(err)}`),
  });

  // Workflow-level pause/resume = unpublish/publish (stops/starts its schedule
  // and webhook triggers). There is no /workflows/{id}/pause|resume route — the
  // old calls 404'd; run-level pause/resume lives at /api/v1/runs/{id}/pause|resume.
  const toggleWorkflow = useMutation({
    mutationFn: ({ id, pause }: { id: string; pause: boolean }) =>
      apiFetch(`/api/v1/workflows/${encodeURIComponent(id)}/${pause ? 'unpublish' : 'publish'}`, {
        method: 'POST',
        body: '{}',
      }),
    onMutate: () => setActionError(null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workflow-engine-list'] });
      qc.invalidateQueries({ queryKey: ['workflow-engine-detail'] });
    },
    onError: (err, vars) =>
      setActionError(`Could not ${vars.pause ? 'pause' : 'resume'} the workflow: ${errorText(err)}`),
  });

  const activeCount = workflows.filter(isActive).length;
  const runningRuns = runs.filter((r) => r.status === 'running' || r.status === 'pending').length;
  const failedRuns = runs.filter((r) => isFailed(r.status)).length;
  const running = triggerRun.isPending;

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

        {actionError && (
          <div role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
            {actionError}
          </div>
        )}
        {lastRun && (
          <div className="rounded-xl border border-[#00D4FF]/30 bg-[#00D4FF]/10 px-4 py-2 text-sm text-[#BAE6FD] flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-[#00D4FF]" />
            <span>Run started ({lastRun.run_id.slice(0, 12)}…).</span>
            <Link to={runDetailPath(lastRun)} className="text-[#00D4FF] font-medium hover:underline">
              View run
            </Link>
          </div>
        )}

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
              {selectedId && (
                <WorkflowDetailPanel
                  workflowId={selectedId}
                  onClose={() => setSelectedId(null)}
                  onRun={(id) => triggerRun.mutate(id)}
                  running={running}
                />
              )}
              <JARVISStagger className="space-y-3">
                {workflows.map((wf) => (
                  <WorkflowCard
                    key={wf.id}
                    wf={wf}
                    selected={selectedId === wf.id}
                    onSelect={(id) => setSelectedId((cur) => (cur === id ? null : id))}
                    onRun={(id) => triggerRun.mutate(id)}
                    onToggle={(id, pause) => toggleWorkflow.mutate({ id, pause })}
                    running={running}
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
              {runs.map((run) => (
                <JARVISStaggerItem key={run.run_id}>
                  <RunRow run={run} showWorkflow />
                </JARVISStaggerItem>
              ))}
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
