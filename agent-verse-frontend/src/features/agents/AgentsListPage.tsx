import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Search, ChevronUp, ChevronDown, ArrowUpDown, Bot, Plus } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { agentsApi } from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';
import { ConfirmModal } from '@/components/ui/ConfirmModal';
import { Pagination } from '@/components/ui/Pagination';

import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
interface Agent {
  agent_id: string;
  name: string;
  autonomy_mode: 'supervised' | 'bounded-autonomous' | 'fully-autonomous' | string;
  goal_template: string;
  status?: string;
  is_active?: boolean;
  created_at?: string;
}

const AUTONOMY_COLORS: Record<string, string> = {
  supervised:           'bg-emerald-100 text-emerald-800 border border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800',
  'bounded-autonomous': 'bg-cyan-100 text-cyan-800 border border-cyan-200 dark:bg-cyan-900/30 dark:text-cyan-300 dark:border-cyan-800',
  'fully-autonomous':   'bg-violet-100 text-violet-800 border border-violet-200 dark:bg-violet-900/30 dark:text-violet-300 dark:border-violet-800',
  manual:               'bg-muted text-muted-foreground border border-border',
};

// Human-readable autonomy mode labels
const AUTONOMY_LABELS: Record<string, string> = {
  'fully-autonomous':   'Fully Autonomous',
  'bounded-autonomous': 'Bounded Autonomous',
  'human-in-loop':      'Human in Loop',
  'manual':             'Manual',
  'supervised':         'Supervised',
};

const AUTONOMY_MODES = ['all', 'supervised', 'bounded-autonomous', 'fully-autonomous'];

const PAGE_SIZE = 15;

type AgentSortField = 'name' | 'created_at';

// Status badge derived from agent properties
function AgentStatusBadge({ agent }: { agent: Agent }) {
  const isActive = agent.is_active ?? true;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-medium border ${
        isActive
          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
          : 'bg-muted text-muted-foreground'
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${isActive ? 'bg-emerald-500 animate-pulse' : 'bg-muted'}`} />
      {isActive ? 'Active' : 'Inactive'}
    </span>
  );
}

export function AgentsListPage() {
  const { t } = useTranslation();
  const apiKey = useAuthStore((s) => s.apiKey);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [nlCommand, setNlCommand] = useState('');

  // Escape closes the create dialog. A modal that traps the user until they
  // locate the Cancel button is a standard accessibility failure; the e2e suite
  // asserted this behaviour long before it existed.
  useEffect(() => {
    if (!showCreate) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setShowCreate(false);
        setNlCommand('');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showCreate]);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // ── URL-backed filter/search/sort/page state ──────────────────────────────
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') ?? '';
  const filterMode = searchParams.get('mode') ?? 'all';
  const page = parseInt(searchParams.get('page') ?? '1', 10);
  const sortField = (searchParams.get('sort') ?? 'created_at') as AgentSortField;
  const sortDir = (searchParams.get('dir') ?? 'desc') as 'asc' | 'desc';

  const updateParams = (updates: Record<string, string | null>) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(updates).forEach(([k, v]) => {
        if (!v || v === 'all') next.delete(k);
        else next.set(k, v);
      });
      return next;
    });
  };

  const handleSort = (field: AgentSortField) => {
    if (sortField === field) {
      updateParams({ sort: field, dir: sortDir === 'asc' ? 'desc' : 'asc' });
    } else {
      updateParams({ sort: field, dir: 'asc' });
    }
  };

  // TODO(scale): GET /agents returns the full list (no server page/limit/filter
  // params — see agentsApi.list), so search/mode-filter/sort/paging all run
  // client-side below over the loaded array. Needs a backend cursor to page
  // server-side. Until then the <Pagination> control keeps the rendered DOM
  // bounded to PAGE_SIZE rows.
  const {
    data: agents = [],
    isLoading,
    error,
  } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list(),
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: () => agentsApi.createNl(nlCommand, false),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] });
      setShowCreate(false);
      setNlCommand('');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => agentsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] });
      setConfirmDeleteId(null);
    },
  });

  // Filter by autonomy mode AND search term
  const filteredAgents = (agents as Agent[])
    .filter((a) => filterMode === 'all' || a.autonomy_mode === filterMode)
    .filter(
      (a) =>
        !search ||
        a.name.toLowerCase().includes(search.toLowerCase()) ||
        a.goal_template?.toLowerCase().includes(search.toLowerCase()),
    );

  // Sort filtered agents
  const sortedAgents = [...filteredAgents].sort((a, b) => {
    let cmp = 0;
    if (sortField === 'name') cmp = a.name.localeCompare(b.name);
    else cmp = (a.created_at ?? '') < (b.created_at ?? '') ? -1 : 1;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  // Paginate
  const paginatedAgents = sortedAgents.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const agentToDelete = confirmDeleteId
    ? (agents as Agent[]).find((a) => a.agent_id === confirmDeleteId)
    : null;

  return (
    <JARVISPageShell>
    <div className="space-y-6">
      {/* Page header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-lg bg-primary/10 border border-primary/20">
              <Bot className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-[#00D4FF] tracking-tight">Agent Registry</h1>
              <p className="text-muted-foreground text-sm mt-0.5">
                {(agents as Agent[]).length} autonomous agents under mission control
              </p>
            </div>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2 rounded-lg text-sm font-medium transition-colors "
          >
            <Plus className="h-4 w-4" />
            {t('agents.new')}
          </button>
        </div>

        {/* Search input + autonomy filter row */}
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
            <input
              value={search}
              onChange={(e) => updateParams({ q: e.target.value, page: null })}
              placeholder="Search agents…"
              className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-lg bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary transition-colors"
            />
          </div>
          <div className="flex gap-2 flex-wrap">
            {AUTONOMY_MODES.map((mode) => (
              <button
                key={mode}
                onClick={() => updateParams({ mode, page: null })}
                className={`px-3 py-1 text-xs rounded-full border transition-[color,background-color,border-color,opacity,box-shadow,transform] ${
                  filterMode === mode
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'border-border text-muted-foreground hover:border-primary/40 hover:text-foreground hover:bg-muted'
                }`}
              >
                {mode === 'all' ? 'All' : (AUTONOMY_LABELS[mode] ?? mode)}
              </button>
            ))}
          </div>
        </div>

        {/* Create modal */}
        {showCreate && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50">
            <div className="bg-card border border-border rounded-xl p-6 w-full max-w-lg shadow-xl">
              <div className="flex items-center gap-3 mb-4">
                <div className="p-2 rounded-lg bg-primary/10 border border-primary/20">
                  <Bot className="h-4 w-4 text-primary" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-foreground">Deploy New Agent</h2>
                  <p className="text-muted-foreground text-xs mt-0.5">Describe the mission in plain English</p>
                </div>
              </div>
              <textarea
                value={nlCommand}
                onChange={(e) => setNlCommand(e.target.value)}
                placeholder="e.g. 'Create an agent that monitors GitHub issues labeled bug and creates JIRA tickets automatically'"
                rows={4}
                className="w-full border border-input rounded-lg p-3 text-sm resize-none focus:ring-2 focus:ring-primary focus:border-primary outline-none bg-background text-foreground placeholder:text-muted-foreground transition-colors"
                autoFocus
              />
              {createMutation.isError && (
                <p role="alert" className="text-xs text-mission-red mt-2">
                  {String(createMutation.error)}
                </p>
              )}
              <div className="flex gap-3 mt-4 justify-end">
                <button
                  onClick={() => {
                    setShowCreate(false);
                    setNlCommand('');
                  }}
                  className="px-4 py-2 border border-border rounded-lg text-sm text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={() => createMutation.mutate()}
                  disabled={!nlCommand.trim() || createMutation.isPending}
                  className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:bg-primary/90 disabled:opacity-50 transition-opacity "
                >
                  {createMutation.isPending ? 'Deploying…' : 'Deploy Agent'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Delete confirm modal */}
        <ConfirmModal
          open={confirmDeleteId !== null}
          title={`Delete agent "${agentToDelete?.name ?? ''}"`}
          description="This action cannot be undone. All associated data will be removed."
          confirmLabel={t('common.delete')}
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => confirmDeleteId && deleteMutation.mutate(confirmDeleteId)}
          onCancel={() => setConfirmDeleteId(null)}
        />

        {/* Agent Table */}
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          {isLoading ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  {['Name', 'Status', 'Autonomy Mode', 'Goal Template', 'Created', 'Actions'].map((h) => (
                    <th key={h} className="text-left px-4 py-3 font-medium text-muted-foreground text-xs uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i}>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-32" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-24" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-48" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-20" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-16" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : error ? (
            <div className="px-5 py-10 text-center text-sm text-mission-red">
              Failed to load agents. Check your connection.
            </div>
          ) : filteredAgents.length === 0 ? (
            <div className="px-5 py-16 text-center">
              <Bot className="h-10 w-10 text-muted-foreground/30 mx-auto mb-3" />
              <p className="font-medium text-muted-foreground">
                {filterMode === 'all' && !search
                  ? t('agents.noAgents')
                  : 'No matching agents'}
              </p>
              {filterMode === 'all' && !search && (
                <p className="mt-1 text-muted-foreground text-sm">
                  Deploy your first agent using the button above.
                </p>
              )}
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  {/* Sortable: Name */}
                  <th
                    onClick={() => handleSort('name')}
                    className="text-left px-4 py-3 font-medium text-muted-foreground text-xs uppercase tracking-wider cursor-pointer hover:text-foreground select-none transition-colors"
                  >
                    <span className="inline-flex items-center gap-1">
                      Name{' '}
                      {sortField === 'name' ? (
                        sortDir === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />
                      ) : (
                        <ArrowUpDown className="h-3 w-3 opacity-40" />
                      )}
                    </span>
                  </th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground text-xs uppercase tracking-wider">Status</th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground text-xs uppercase tracking-wider">Autonomy Mode</th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground text-xs uppercase tracking-wider">Goal Template</th>
                  {/* Sortable: Created */}
                  <th
                    onClick={() => handleSort('created_at')}
                    className="text-left px-4 py-3 font-medium text-muted-foreground text-xs uppercase tracking-wider cursor-pointer hover:text-foreground select-none transition-colors"
                  >
                    <span className="inline-flex items-center gap-1">
                      Created{' '}
                      {sortField === 'created_at' ? (
                        sortDir === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />
                      ) : (
                        <ArrowUpDown className="h-3 w-3 opacity-40" />
                      )}
                    </span>
                  </th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground text-xs uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {paginatedAgents.map((agent) => (
                  <tr
                    key={agent.agent_id}
                    onClick={() => navigate(`/agents/${agent.agent_id}`)}
                    className="jarvis-rise-in hover:bg-[#1A1F2E] transition-colors cursor-pointer group"
                    role="button"
                    aria-label={`View agent ${agent.name}`}
                  >
                    <td className="px-4 py-3 font-medium text-foreground group-hover:text-primary transition-colors">
                      {agent.name}
                    </td>
                    {/* Status badge */}
                    <td className="px-4 py-3">
                      <AgentStatusBadge agent={agent} />
                    </td>
                    {/* Human-readable autonomy mode */}
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          AUTONOMY_COLORS[agent.autonomy_mode] ??
                          'bg-muted text-muted-foreground border border-border'
                        }`}
                      >
                        {AUTONOMY_LABELS[agent.autonomy_mode] ?? agent.autonomy_mode}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground max-w-xs truncate font-mono text-xs">
                      {agent.goal_template || '—'}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground/70 font-mono text-xs">
                      {agent.created_at
                        ? new Date(agent.created_at).toLocaleDateString()
                        : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-3">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/agents/${agent.agent_id}`);
                          }}
                          className="text-primary hover:text-primary/70 text-sm font-medium transition-colors"
                        >
                          View
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setConfirmDeleteId(agent.agent_id);
                          }}
                          disabled={deleteMutation.isPending}
                          className="text-mission-red/60 hover:text-mission-red text-sm disabled:opacity-40 transition-colors"
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination — only rendered when there are more than PAGE_SIZE agents */}
        {!isLoading && filteredAgents.length > PAGE_SIZE && (
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={filteredAgents.length}
            onPageChange={(p) => updateParams({ page: String(p) })}
          />
        )}
    </div>
    </JARVISPageShell>
  );
}
