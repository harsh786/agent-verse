import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Search, ChevronUp, ChevronDown, ArrowUpDown } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { agentsApi } from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';
import { ConfirmModal } from '@/components/ui/ConfirmModal';
import { Pagination } from '@/components/ui/Pagination';

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
  supervised:           'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
  'bounded-autonomous': 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  'fully-autonomous':   'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
  manual:               'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
};

// Fix 3: Human-readable autonomy mode labels
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

// Fix 2: Status badge derived from agent properties
function AgentStatusBadge({ agent }: { agent: Agent }) {
  const isActive = agent.is_active ?? true;
  return (
    <span
      className={`text-xs px-2 py-0.5 rounded-full font-medium ${
        isActive
          ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
          : 'bg-muted text-muted-foreground'
      }`}
    >
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

  // Fix 1+2+3: Filter by autonomy mode AND search term
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

  // Fix 5: Paginate
  const paginatedAgents = sortedAgents.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const agentToDelete = confirmDeleteId
    ? (agents as Agent[]).find((a) => a.agent_id === confirmDeleteId)
    : null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agents</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Manage autonomous agents
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg hover:opacity-90 text-sm font-medium"
        >
          + {t('agents.new')}
        </button>
      </div>

      {/* Fix 1: Search input + autonomy filter row */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
          <input
            value={search}
            onChange={(e) => updateParams({ q: e.target.value, page: null })}
            placeholder="Search agents…"
            className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div className="flex gap-2 flex-wrap">
          {AUTONOMY_MODES.map((mode) => (
            <button
              key={mode}
              onClick={() => updateParams({ mode, page: null })}
              className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                filterMode === mode
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'border-border hover:bg-accent'
              }`}
            >
              {mode === 'all' ? 'All' : (AUTONOMY_LABELS[mode] ?? mode)}
            </button>
          ))}
        </div>
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-card border border-border rounded-xl p-6 w-full max-w-lg shadow-2xl">
            <h2 className="text-xl font-semibold mb-4">
              Create Agent with Natural Language
            </h2>
            <textarea
              value={nlCommand}
              onChange={(e) => setNlCommand(e.target.value)}
              placeholder="Describe your agent in plain English, e.g. 'Create an agent that monitors GitHub issues labeled bug and creates JIRA tickets automatically'"
              rows={4}
              className="w-full border border-input rounded-lg p-3 text-sm resize-none focus:ring-2 focus:ring-primary outline-none bg-background"
              autoFocus
            />
            {createMutation.isError && (
              <p role="alert" className="text-xs text-red-600 dark:text-red-400 mt-2">
                {String(createMutation.error)}
              </p>
            )}
            <div className="flex gap-3 mt-4 justify-end">
              <button
                onClick={() => {
                  setShowCreate(false);
                  setNlCommand('');
                }}
                className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => createMutation.mutate()}
                disabled={!nlCommand.trim() || createMutation.isPending}
                className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {createMutation.isPending ? 'Creating…' : 'Create'}
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

      {/* Table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                {['Name', 'Status', 'Autonomy Mode', 'Goal Template', 'Created', 'Actions'].map((h) => (
                  <th key={h} className="text-left px-4 py-3 font-medium text-muted-foreground">
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
          <div className="px-5 py-10 text-center text-sm text-red-500">
            Failed to load agents. Check your connection.
          </div>
        ) : filteredAgents.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            <p className="font-medium">
              {/* Fix 4: Updated empty state copy */}
              {filterMode === 'all' && !search
                ? t('agents.noAgents')
                : 'No matching agents'}
            </p>
            {filterMode === 'all' && !search && (
              <p className="mt-1">
                No agents found. Create your first agent using the button above.
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
                  className="text-left px-4 py-3 font-medium text-muted-foreground cursor-pointer hover:text-foreground select-none"
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
                <th className="text-left px-4 py-3 font-medium text-muted-foreground">Status</th>
                <th className="text-left px-4 py-3 font-medium text-muted-foreground">Autonomy Mode</th>
                <th className="text-left px-4 py-3 font-medium text-muted-foreground">Goal Template</th>
                {/* Sortable: Created */}
                <th
                  onClick={() => handleSort('created_at')}
                  className="text-left px-4 py-3 font-medium text-muted-foreground cursor-pointer hover:text-foreground select-none"
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
                <th className="text-left px-4 py-3 font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {paginatedAgents.map((agent) => (
                <tr
                  key={agent.agent_id}
                  onClick={() => navigate(`/agents/${agent.agent_id}`)}
                  className="hover:bg-muted/40 transition-colors cursor-pointer"
                  role="button"
                  aria-label={`View agent ${agent.name}`}
                >
                  <td className="px-4 py-3 font-medium">{agent.name}</td>
                  {/* Fix 2: Status badge */}
                  <td className="px-4 py-3">
                    <AgentStatusBadge agent={agent} />
                  </td>
                  {/* Fix 3: Human-readable autonomy mode */}
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        AUTONOMY_COLORS[agent.autonomy_mode] ??
                        'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300'
                      }`}
                    >
                      {AUTONOMY_LABELS[agent.autonomy_mode] ?? agent.autonomy_mode}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground max-w-xs truncate">
                    {agent.goal_template || '—'}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {agent.created_at
                      ? new Date(agent.created_at).toLocaleDateString()
                      : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/agents/${agent.agent_id}`);
                        }}
                        className="text-primary hover:opacity-70 text-sm font-medium"
                      >
                        View
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setConfirmDeleteId(agent.agent_id);
                        }}
                        disabled={deleteMutation.isPending}
                        className="text-destructive hover:opacity-70 text-sm disabled:opacity-40 transition-opacity"
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

      {/* Fix 5: Pagination — only rendered when there are more than PAGE_SIZE agents */}
      {!isLoading && filteredAgents.length > PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={filteredAgents.length}
          onPageChange={(p) => updateParams({ page: String(p) })}
        />
      )}
    </div>
  );
}
