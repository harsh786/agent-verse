/**
 * Workflow List Page — entry point for the Workflow Automation Engine.
 *
 * Features:
 * - Searchable, filterable grid of workflow definitions
 * - Status badges (draft / published / archived)
 * - One-click create from template or blank
 * - Empty state with CTA
 * - WCAG 2.2 AA accessible
 */
import { useState, useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Plus, Search, Zap, LayoutTemplate, Play, Edit2, Trash2,
  CheckCircle, Clock, Archive, AlertCircle, Filter, RefreshCw, FileCode2,
  Pause, Square, Loader2, RotateCcw,
} from 'lucide-react';
import { workflowEngineApi, type WEWorkflow } from '../../lib/api/client';
import { getStatusClasses } from './design/tokens';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';
import { WorkflowYamlCreateModal } from './builder/WorkflowYamlCreateModal';

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_ICONS: Record<string, React.ReactNode> = {
  draft:     <Clock className="h-3 w-3" />,
  published: <CheckCircle className="h-3 w-3" />,
  archived:  <Archive className="h-3 w-3" />,
};

function StatusBadge({ status }: { status: string }) {
  const cls = getStatusClasses(status === 'published' ? 'complete'
    : status === 'archived' ? 'cancelled' : 'pending');
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {STATUS_ICONS[status] ?? <AlertCircle className="h-3 w-3" />}
      {status}
    </span>
  );
}

// ── Workflow card ─────────────────────────────────────────────────────────────

function shortCron(cron: string): string {
  const p = cron.trim().split(/\s+/);
  if (p.length < 5) return cron;
  const [min, hr, dom, , dow] = p;
  const at = (h: string, m: string) => {
    const hh = Number(h);
    if (Number.isNaN(hh)) return `${h}:${m}`;
    const ampm = hh < 12 ? 'am' : 'pm';
    return `${hh % 12 === 0 ? 12 : hh % 12}${m === '0' || m === '00' ? '' : ':' + m.padStart(2, '0')}${ampm}`;
  };
  if (min.startsWith('*/') && hr === '*') return `every ${min.slice(2)}m`;
  if (min === '0' && hr === '*') return 'hourly';
  if (dom === '*' && dow === '*') return `daily ${at(hr, min)}`;
  if (dow !== '*') return `weekly ${at(hr, min)}`;
  return `monthly ${at(hr, min)}`;
}

function TriggerBadge({ type, cron }: { type: string; cron?: string }) {
  if (type === 'schedule') {
    return (
      <span className="flex items-center gap-1" title={cron ? `cron: ${cron}` : 'scheduled'}>
        <Clock className="h-3 w-3" />
        {cron ? shortCron(cron) : 'schedule'}
      </span>
    );
  }
  if (type === 'webhook') {
    return (
      <span className="flex items-center gap-1" title="Started by an inbound webhook URL">
        <Zap className="h-3 w-3" /> webhook
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1" title="Started manually or via API">
      <Play className="h-3 w-3" /> manual
    </span>
  );
}

type ActiveRun = { run_id: string; status: string };

const TERMINAL_RUN = ['cancelled', 'failed', 'error', 'timed_out'];

// Run controls for a workflow's latest run, shown inline on the card so an
// operator can control an execution without opening it:
//   running/pending → Pause + Stop
//   paused          → Resume + Stop
//   cancelled/failed→ status + Re-run  (a stopped run is terminal and cannot be
//                     resumed, so instead of leaving the card blank we offer a
//                     clear next action)
function CardRunControls({ run, workflowId }: { run: ActiveRun; workflowId: string }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ['workflow-engine', 'active-runs'] });
  const pause = useMutation({ mutationFn: () => workflowEngineApi.pauseRun(run.run_id), onSuccess: refresh });
  const resume = useMutation({ mutationFn: () => workflowEngineApi.resumeRun(run.run_id), onSuccess: refresh });
  const cancel = useMutation({ mutationFn: () => workflowEngineApi.cancelRun(run.run_id), onSuccess: refresh });
  const rerun = useMutation({ mutationFn: () => workflowEngineApi.trigger(workflowId, {}), onSuccess: refresh });
  const busy = pause.isPending || resume.isPending || cancel.isPending || rerun.isPending;
  const running = run.status === 'running' || run.status === 'pending';
  const terminal = TERMINAL_RUN.includes(run.status);
  const dotClass = run.status === 'paused' ? 'bg-amber-400'
    : terminal ? 'bg-red-400'
    : 'bg-emerald-400 animate-pulse';
  const label = run.status === 'cancelled' ? 'stopped' : run.status;
  return (
    <div className="relative z-10 flex items-center gap-2 text-xs pt-1">
      <span className="inline-flex items-center gap-1.5 text-[#F1F5F9]/50">
        <span className={`h-1.5 w-1.5 rounded-full ${dotClass}`} />
        {label}
      </span>
      {busy && <Loader2 className="h-3 w-3 animate-spin text-[#F1F5F9]/40" />}
      {running && (
        <button
          onClick={() => pause.mutate()} disabled={busy} aria-label={`Pause run of ${run.run_id}`}
          className="ml-auto flex items-center gap-1 px-2 py-1 rounded-lg bg-amber-500/15
                     hover:bg-amber-500/25 text-amber-400 font-medium disabled:opacity-50 transition-colors"
        >
          <Pause className="h-3 w-3" /> Pause
        </button>
      )}
      {run.status === 'paused' && (
        <button
          onClick={() => resume.mutate()} disabled={busy} aria-label={`Resume run of ${run.run_id}`}
          className="ml-auto flex items-center gap-1 px-2 py-1 rounded-lg bg-emerald-500/15
                     hover:bg-emerald-500/25 text-emerald-400 font-medium disabled:opacity-50 transition-colors"
        >
          <Play className="h-3 w-3" /> Resume
        </button>
      )}
      {(running || run.status === 'paused') && (
        <button
          onClick={() => cancel.mutate()} disabled={busy} aria-label={`Stop run of ${run.run_id}`}
          className="flex items-center gap-1 px-2 py-1 rounded-lg bg-red-500/15 hover:bg-red-500/25
                     text-red-400 font-medium disabled:opacity-50 transition-colors"
        >
          <Square className="h-3 w-3" /> Stop
        </button>
      )}
      {terminal && (
        <button
          onClick={() => rerun.mutate()} disabled={busy} aria-label={`Re-run ${workflowId}`}
          className="ml-auto flex items-center gap-1 px-2 py-1 rounded-lg bg-sky-500/15
                     hover:bg-sky-500/25 text-sky-400 font-medium disabled:opacity-50 transition-colors"
        >
          <RotateCcw className="h-3 w-3" /> Re-run
        </button>
      )}
    </div>
  );
}

function WorkflowCard({
  wf,
  onDelete,
  activeRun,
  index = 0,
}: {
  wf: WEWorkflow;
  onDelete: (id: string) => void;
  activeRun?: ActiveRun | null;
  index?: number;
}) {

  return (
    <article
      style={{ animationDelay: `${Math.min(index, 8) * 0.04}s` }}
      className="jarvis-pop-in group relative rounded-2xl border border-white/10 bg-white/[0.03]
                 hover:bg-white/[0.06] hover:border-white/20 backdrop-blur-sm p-5 flex flex-col gap-3
                 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-sky-500/5
                 focus-within:ring-2 focus-within:ring-sky-500"
      role="listitem"
      aria-label={`Workflow: ${wf.name}`}
    >
      {/* Stretched link — the whole card opens the builder. Action buttons below
          sit above it (relative z-10) and handle their own clicks. */}
      <Link
        to={`/workflows/${wf.id}/edit`}
        className="absolute inset-0 z-0 rounded-2xl"
        aria-label={`Open workflow ${wf.name}`}
      />

      {/* Header */}
      <div className="relative z-10 pointer-events-none flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <h3 className="text-[#F1F5F9] font-semibold truncate text-sm leading-tight">
            {wf.name}
          </h3>
          {wf.description && (
            <p className="text-[#F1F5F9]/50 text-xs mt-1 line-clamp-2 leading-relaxed">
              {wf.description}
            </p>
          )}
        </div>
        <StatusBadge status={wf.status} />
      </div>

      {/* Meta */}
      <div className="flex items-center gap-3 text-xs text-[#F1F5F9]/40">
        {wf.trigger_type && <TriggerBadge type={wf.trigger_type} cron={wf.schedule_cron} />}
        <span>v{wf.version}</span>
        <span className="ml-auto">
          {new Date(wf.updated_at).toLocaleDateString()}
        </span>
      </div>

      {/* Run controls — pause/resume/stop an active run, or re-run a stopped one.
          'complete' shows nothing (keeps the card clean). */}
      {activeRun && activeRun.status !== 'complete' && activeRun.status !== 'completed' && (
        <CardRunControls run={activeRun} workflowId={wf.id} />
      )}

      {/* Actions — above the stretched link so they capture their own clicks */}
      <div className="relative z-10 flex items-center gap-2 pt-1 border-t border-white/5">
        <Link
          to={`/workflows/${wf.id}/edit`}
          className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium
                     py-1.5 rounded-lg bg-[#0F1826]/5 hover:bg-[#0A0D14]/10 text-[#F1F5F9]/70
                     hover:text-[#F1F5F9] transition-colors"
          aria-label={`Edit workflow ${wf.name}`}
        >
          <Edit2 className="h-3.5 w-3.5" /> Edit
        </Link>

        {wf.status === 'published' && (
          <Link
            to={`/workflows/${wf.id}/runs`}
            className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium
                       py-1.5 rounded-lg bg-sky-500/15 hover:bg-sky-500/25 text-sky-400
                       transition-colors"
            aria-label={`View runs for ${wf.name}`}
          >
            <Play className="h-3.5 w-3.5" /> Runs
          </Link>
        )}

        <button
          onClick={() => onDelete(wf.id)}
          className="p-1.5 rounded-lg text-[#F1F5F9]/30 hover:text-red-400
                     hover:bg-red-500/10 transition-colors"
          aria-label={`Delete workflow ${wf.name}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </article>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ onCreateBlank, onBrowseTemplates, onCreateYaml }: {
  onCreateBlank: () => void;
  onBrowseTemplates: () => void;
  onCreateYaml: () => void;
}) {
  return (
    <div
      className="jarvis-rise-in flex flex-col items-center justify-center py-24 px-8 text-center"
      role="status"
      aria-label="No workflows found"
    >
      {/* Illustration */}
      <div className="relative mb-6">
        <div className="w-24 h-24 rounded-3xl bg-[#0F1826]/5 border border-white/10 flex items-center
                        justify-center">
          <Zap className="h-10 w-10 text-[#F1F5F9]/20" />
        </div>
        <div className="absolute -right-2 -top-2 w-8 h-8 rounded-xl bg-sky-500/20 border
                        border-sky-500/30 flex items-center justify-center">
          <Plus className="h-4 w-4 text-sky-400" />
        </div>
      </div>

      <h2 className="text-xl font-semibold text-[#F1F5F9] mb-2">No workflows yet</h2>
      <p className="text-[#F1F5F9]/50 text-sm mb-8 max-w-xs leading-relaxed">
        Build repeatable business processes that run autonomously with AI, tools, and
        human approval checkpoints.
      </p>

      <div className="flex items-center gap-3 flex-wrap justify-center">
        <button
          onClick={onCreateBlank}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-sky-600 hover:bg-sky-500
                     text-[#F1F5F9] text-sm font-medium transition-colors"
        >
          <Plus className="h-4 w-4" /> Create blank workflow
        </button>
        <button
          onClick={onCreateYaml}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl border border-white/15
                     hover:border-white/25 text-[#F1F5F9]/70 hover:text-[#F1F5F9] text-sm font-medium
                     transition-colors"
        >
          <FileCode2 className="h-4 w-4" /> Create from YAML
        </button>
        <button
          onClick={onBrowseTemplates}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl border border-white/15
                     hover:border-white/25 text-[#F1F5F9]/70 hover:text-[#F1F5F9] text-sm font-medium
                     transition-colors"
        >
          <LayoutTemplate className="h-4 w-4" /> Browse templates
        </button>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function WorkflowListPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [search, setSearch] = useState('');
  // Default to the Published tab: it's the set of workflows that actually run,
  // and it excludes archived (soft-deleted) rows — so deleting feels like deleting.
  const [statusFilter, setStatusFilter] = useState<string>('published');
  const [showYamlCreate, setShowYamlCreate] = useState(false);

  // Server-side pagination via GET /api/v1/workflows (status filter + per_page).
  // "Load more" grows the requested window; `total` gates it. The status filter
  // is server-side; the free-text search below refines the loaded page only
  // (the list endpoint has no text-search param).
  const WF_PAGE = 60;
  const [perPage, setPerPage] = useState(WF_PAGE);
  // Reset the window whenever the server-side status filter changes.
  const resetWindow = (next: string) => { setStatusFilter(next); setPerPage(WF_PAGE); };

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['workflow-engine', 'list', statusFilter, perPage],
    queryFn: () => workflowEngineApi.list({ per_page: perPage, status: statusFilter || undefined }),
    staleTime: 30_000,
  });
  const totalWorkflows = data?.total ?? (data?.items?.length ?? 0);
  const hasMoreWorkflows = (data?.items?.length ?? 0) < totalWorkflows;

  // Latest run per workflow (runs come back created_at DESC), polled so cards can
  // offer pause/resume/stop on whichever run is currently active.
  const { data: runsData } = useQuery({
    queryKey: ['workflow-engine', 'active-runs'],
    queryFn: () => workflowEngineApi.listRuns({ per_page: 100 }),
    refetchInterval: 5000,
    staleTime: 2000,
  });
  const activeRunByWorkflow = useMemo(() => {
    const m = new Map<string, ActiveRun>();
    for (const r of runsData?.items ?? []) {
      if (!m.has(r.workflow_id)) m.set(r.workflow_id, { run_id: r.run_id, status: r.status });
    }
    return m;
  }, [runsData]);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => workflowEngineApi.delete(id),
    // Optimistically drop the row from every cached list so a deleted workflow
    // vanishes immediately instead of lingering until the refetch resolves.
    onMutate: async (id: string) => {
      await qc.cancelQueries({ queryKey: ['workflow-engine', 'list'] });
      const snapshots = qc.getQueriesData<{ items?: WEWorkflow[] }>({
        queryKey: ['workflow-engine', 'list'],
      });
      for (const [key, value] of snapshots) {
        if (value?.items) {
          qc.setQueryData(key, { ...value, items: value.items.filter((w) => w.id !== id) });
        }
      }
      return { snapshots };
    },
    onError: (_e, _id, ctx) => {
      // Roll back on failure.
      ctx?.snapshots?.forEach(([key, value]) => qc.setQueryData(key, value));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['workflow-engine', 'list'] }),
  });

  const createBlank = async () => {
    try {
      const wf = await workflowEngineApi.create({
        name: 'Untitled Workflow',
        definition: { name: 'Untitled Workflow', steps: [] },
      });
      navigate(`/workflows/${wf.id}/edit`);
    } catch {
      // error handled by toast
    }
  };

  const filtered = (data?.items ?? [])
    // The "All" tab shows active workflows only — archived (soft-deleted) rows
    // are reachable via the dedicated Archived tab, never mixed into All.
    .filter((w) => (statusFilter === '' ? w.status !== 'archived' : true))
    .filter((w) =>
      !search || w.name.toLowerCase().includes(search.toLowerCase())
        || w.description?.toLowerCase().includes(search.toLowerCase())
    );

  return (
    <JARVISPageShell>
    <JARVISStagger className="min-h-screen bg-gradient-to-br from-[#060810] via-[#0F1117] to-[#060810]
                    text-[#F1F5F9]">
      {/* Header */}
      <header className="sticky top-0 z-30 border-b border-white/8 bg-[#060810]/80
                         backdrop-blur-xl px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-bold text-[#F1F5F9]">Workflows</h1>
            <p className="text-xs text-[#F1F5F9]/40 mt-0.5">
              Automate business processes with AI, tools, and human approval
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/workflows/marketplace')}
              className="flex items-center gap-2 px-3.5 py-2 rounded-xl border border-white/15
                         hover:border-white/25 text-[#F1F5F9]/70 hover:text-[#F1F5F9] text-sm transition-colors"
              aria-label="Browse workflow templates"
            >
              <LayoutTemplate className="h-4 w-4" /> Templates
            </button>
            <button
              onClick={() => setShowYamlCreate(true)}
              className="flex items-center gap-2 px-3.5 py-2 rounded-xl border border-white/15
                         hover:border-white/25 text-[#F1F5F9]/70 hover:text-[#F1F5F9] text-sm transition-colors"
              aria-label="Create workflow from YAML"
            >
              <FileCode2 className="h-4 w-4" /> From YAML
            </button>
            <button
              onClick={createBlank}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500
                         text-[#F1F5F9] text-sm font-medium transition-colors"
              aria-label="Create new workflow"
            >
              <Plus className="h-4 w-4" /> New Workflow
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        {/* Filters */}
        <div className="flex items-center gap-3 mb-6 flex-wrap">
          {/* Search */}
          <div className="relative flex-1 min-w-0 max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[#F1F5F9]/30"
                    aria-hidden />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search workflows…"
              aria-label="Search workflows"
              className="w-full pl-9 pr-3 py-2 rounded-xl border border-white/10 bg-[#0F1826]/5
                         text-[#F1F5F9] placeholder-white/30 text-sm focus:outline-none
                         focus:ring-2 focus:ring-sky-500 focus:border-transparent"
            />
          </div>

          {/* Status filter */}
          <div className="flex items-center gap-1.5">
            <Filter className="h-4 w-4 text-[#F1F5F9]/30" aria-hidden />
            {['', 'draft', 'published', 'archived'].map((s) => (
              <button
                key={s}
                onClick={() => resetWindow(s)}
                aria-pressed={statusFilter === s}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  statusFilter === s
                    ? 'bg-sky-600 text-[#F1F5F9]'
                    : 'bg-[#0F1826]/5 text-[#F1F5F9]/50 hover:text-[#F1F5F9] hover:bg-[#0A0D14]/10'
                }`}
              >
                {s || 'All'}
              </button>
            ))}
          </div>

          <button
            onClick={() => refetch()}
            className="ml-auto p-2 rounded-xl text-[#F1F5F9]/30 hover:text-[#F1F5F9]
                       hover:bg-[#0A0D14]/5 transition-colors"
            aria-label="Refresh list"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>

        {/* Content */}
        {isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-44 rounded-2xl bg-[#0F1826]/5 animate-pulse" />
            ))}
          </div>
        ) : error ? (
          <div className="text-center py-16 text-red-400 text-sm" role="alert">
            Failed to load workflows. <button onClick={() => refetch()} className="underline">Retry</button>
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            onCreateBlank={createBlank}
            onBrowseTemplates={() => navigate('/workflows/marketplace')}
            onCreateYaml={() => setShowYamlCreate(true)}
          />
        ) : (
          <div
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
            role="list"
            aria-label="Workflow list"
          >
            {filtered.map((wf, i) => (
              <WorkflowCard
                key={wf.id}
                wf={wf}
                index={i}
                activeRun={activeRunByWorkflow.get(wf.id) ?? null}
                onDelete={(id) => deleteMutation.mutate(id)}
              />
            ))}
          </div>
        )}

        {/* Load more — grows the server-side window (per_page) */}
        {!isLoading && !error && hasMoreWorkflows && (
          <div className="mt-6 flex justify-center">
            <button
              onClick={() => setPerPage((n) => n + WF_PAGE)}
              disabled={isFetching}
              className="flex items-center gap-2 px-4 py-2 rounded-xl border border-white/15
                         hover:border-white/25 text-[#F1F5F9]/70 hover:text-[#F1F5F9] text-sm
                         font-medium transition-colors disabled:opacity-50"
              aria-label="Load more workflows"
            >
              {isFetching && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
              Load more
            </button>
          </div>
        )}

        {/* Count */}
        {filtered.length > 0 && (
          <p className="mt-6 text-xs text-[#F1F5F9]/30 text-right">
            {filtered.length} of {totalWorkflows} workflow{totalWorkflows !== 1 ? 's' : ''}
            {search && ' (search matches loaded page)'}
          </p>
        )}
      </main>

      {showYamlCreate && (
        <WorkflowYamlCreateModal
          onClose={() => setShowYamlCreate(false)}
          onCreated={(wfId) => {
            setShowYamlCreate(false);
            qc.invalidateQueries({ queryKey: ['workflow-engine', 'list'] });
            navigate(`/workflows/${wfId}/edit`);
          }}
        />
      )}
    </JARVISStagger>
    </JARVISPageShell>
  );
}
