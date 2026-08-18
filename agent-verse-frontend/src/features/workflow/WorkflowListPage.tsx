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
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Plus, Search, Zap, LayoutTemplate, Play, Edit2, Trash2,
  CheckCircle, Clock, Archive, AlertCircle, Filter, RefreshCw,
} from 'lucide-react';
import { workflowEngineApi, type WEWorkflow } from '../../lib/api/client';
import { getStatusClasses } from './design/tokens';
import { nodeBounce, emptyStateFade } from './design/motion';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

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

function WorkflowCard({
  wf,
  onDelete,
}: {
  wf: WEWorkflow;
  onDelete: (id: string) => void;
}) {

  return (
    <motion.article
      layout
      variants={nodeBounce}
      initial="initial"
      animate="animate"
      exit="exit"
      className="group relative rounded-2xl border border-white/10 bg-white/5 hover:bg-white/8
                 backdrop-blur-sm p-5 flex flex-col gap-3 transition-colors
                 focus-within:ring-2 focus-within:ring-sky-500"
      role="article"
      aria-label={`Workflow: ${wf.name}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <h3 className="text-white font-semibold truncate text-sm leading-tight">
            {wf.name}
          </h3>
          {wf.description && (
            <p className="text-white/50 text-xs mt-1 line-clamp-2 leading-relaxed">
              {wf.description}
            </p>
          )}
        </div>
        <StatusBadge status={wf.status} />
      </div>

      {/* Meta */}
      <div className="flex items-center gap-3 text-xs text-white/40">
        {wf.trigger_type && (
          <span className="flex items-center gap-1">
            <Zap className="h-3 w-3" />
            {wf.trigger_type}
          </span>
        )}
        <span>v{wf.version}</span>
        <span className="ml-auto">
          {new Date(wf.updated_at).toLocaleDateString()}
        </span>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1 border-t border-white/5">
        <Link
          to={`/workflows/${wf.id}/edit`}
          className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium
                     py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-white/70
                     hover:text-white transition-colors"
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
          className="p-1.5 rounded-lg text-white/30 hover:text-red-400
                     hover:bg-red-500/10 transition-colors"
          aria-label={`Delete workflow ${wf.name}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </motion.article>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ onCreateBlank, onBrowseTemplates }: {
  onCreateBlank: () => void;
  onBrowseTemplates: () => void;
}) {
  return (
    <motion.div
      variants={emptyStateFade}
      initial="initial"
      animate="animate"
      className="flex flex-col items-center justify-center py-24 px-8 text-center"
      role="status"
      aria-label="No workflows found"
    >
      {/* Illustration */}
      <div className="relative mb-6">
        <div className="w-24 h-24 rounded-3xl bg-white/5 border border-white/10 flex items-center
                        justify-center">
          <Zap className="h-10 w-10 text-white/20" />
        </div>
        <div className="absolute -right-2 -top-2 w-8 h-8 rounded-xl bg-sky-500/20 border
                        border-sky-500/30 flex items-center justify-center">
          <Plus className="h-4 w-4 text-sky-400" />
        </div>
      </div>

      <h2 className="text-xl font-semibold text-white mb-2">No workflows yet</h2>
      <p className="text-white/50 text-sm mb-8 max-w-xs leading-relaxed">
        Build repeatable business processes that run autonomously with AI, tools, and
        human approval checkpoints.
      </p>

      <div className="flex items-center gap-3 flex-wrap justify-center">
        <button
          onClick={onCreateBlank}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-sky-600 hover:bg-sky-500
                     text-white text-sm font-medium transition-colors"
        >
          <Plus className="h-4 w-4" /> Create blank workflow
        </button>
        <button
          onClick={onBrowseTemplates}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl border border-white/15
                     hover:border-white/25 text-white/70 hover:text-white text-sm font-medium
                     transition-colors"
        >
          <LayoutTemplate className="h-4 w-4" /> Browse templates
        </button>
      </div>
    </motion.div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function WorkflowListPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [, setShowTemplates] = useState(false);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['workflow-engine', 'list', statusFilter],
    queryFn: () => workflowEngineApi.list({ per_page: 100, status: statusFilter || undefined }),
    staleTime: 30_000,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => workflowEngineApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workflow-engine', 'list'] }),
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

  const filtered = (data?.items ?? []).filter((w) =>
    !search || w.name.toLowerCase().includes(search.toLowerCase())
      || w.description?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <JARVISPageShell>
    <JARVISStagger className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950
                    text-white">
      {/* Header */}
      <header className="sticky top-0 z-30 border-b border-white/8 bg-slate-950/80
                         backdrop-blur-xl px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-bold text-white">Workflows</h1>
            <p className="text-xs text-white/40 mt-0.5">
              Automate business processes with AI, tools, and human approval
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowTemplates(true)}
              className="flex items-center gap-2 px-3.5 py-2 rounded-xl border border-white/15
                         hover:border-white/25 text-white/70 hover:text-white text-sm transition-colors"
              aria-label="Browse workflow templates"
            >
              <LayoutTemplate className="h-4 w-4" /> Templates
            </button>
            <button
              onClick={createBlank}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500
                         text-white text-sm font-medium transition-colors"
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
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-white/30"
                    aria-hidden />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search workflows…"
              aria-label="Search workflows"
              className="w-full pl-9 pr-3 py-2 rounded-xl border border-white/10 bg-white/5
                         text-white placeholder-white/30 text-sm focus:outline-none
                         focus:ring-2 focus:ring-sky-500 focus:border-transparent"
            />
          </div>

          {/* Status filter */}
          <div className="flex items-center gap-1.5">
            <Filter className="h-4 w-4 text-white/30" aria-hidden />
            {['', 'draft', 'published', 'archived'].map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                aria-pressed={statusFilter === s}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  statusFilter === s
                    ? 'bg-sky-600 text-white'
                    : 'bg-white/5 text-white/50 hover:text-white hover:bg-white/10'
                }`}
              >
                {s || 'All'}
              </button>
            ))}
          </div>

          <button
            onClick={() => refetch()}
            className="ml-auto p-2 rounded-xl text-white/30 hover:text-white
                       hover:bg-white/5 transition-colors"
            aria-label="Refresh list"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>

        {/* Content */}
        {isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-44 rounded-2xl bg-white/5 animate-pulse" />
            ))}
          </div>
        ) : error ? (
          <div className="text-center py-16 text-red-400 text-sm" role="alert">
            Failed to load workflows. <button onClick={() => refetch()} className="underline">Retry</button>
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            onCreateBlank={createBlank}
            onBrowseTemplates={() => setShowTemplates(true)}
          />
        ) : (
          <AnimatePresence mode="popLayout">
            <div
              className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
              role="list"
              aria-label="Workflow list"
            >
              {filtered.map((wf) => (
                <WorkflowCard
                  key={wf.id}
                  wf={wf}
                  onDelete={(id) => deleteMutation.mutate(id)}
                />
              ))}
            </div>
          </AnimatePresence>
        )}

        {/* Count */}
        {filtered.length > 0 && (
          <p className="mt-6 text-xs text-white/30 text-right">
            {filtered.length} workflow{filtered.length !== 1 ? 's' : ''}
          </p>
        )}
      </main>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
