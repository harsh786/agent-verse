import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';
import { agentsApi } from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';
import { ConfirmModal } from '@/components/ui/ConfirmModal';

interface Agent {
  agent_id: string;
  name: string;
  autonomy_mode: 'supervised' | 'bounded-autonomous' | 'fully-autonomous' | string;
  goal_template: string;
  status?: string;
  created_at?: string;
}

const AUTONOMY_COLORS: Record<string, string> = {
  supervised:           'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
  'bounded-autonomous': 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  'fully-autonomous':   'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
  manual:               'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
};

const AUTONOMY_MODES = ['all', 'supervised', 'bounded-autonomous', 'fully-autonomous'];

export function AgentsListPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [nlCommand, setNlCommand] = useState('');
  const [filterMode, setFilterMode] = useState('all');
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

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

  const filtered =
    filterMode === 'all'
      ? (agents as Agent[])
      : (agents as Agent[]).filter((a) => a.autonomy_mode === filterMode);

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
          + Create Agent
        </button>
      </div>

      {/* Autonomy filter */}
      <div className="flex gap-2 flex-wrap">
        {AUTONOMY_MODES.map((mode) => (
          <button
            key={mode}
            onClick={() => setFilterMode(mode)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors capitalize ${
              filterMode === mode
                ? 'bg-primary text-primary-foreground border-primary'
                : 'border-border hover:bg-accent'
            }`}
          >
            {mode}
          </button>
        ))}
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
        confirmLabel="Delete"
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
                {['Name', 'Autonomy Mode', 'Goal Template', 'Created', 'Actions'].map((h) => (
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
        ) : filtered.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            <p className="font-medium">
              {filterMode === 'all' ? 'No agents yet' : `No ${filterMode} agents`}
            </p>
            {filterMode === 'all' && (
              <p className="mt-1">Create your first agent using natural language above</p>
            )}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                {['Name', 'Autonomy Mode', 'Goal Template', 'Created', 'Actions'].map((h) => (
                  <th key={h} className="text-left px-4 py-3 font-medium text-muted-foreground">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map((agent) => (
                <tr
                  key={agent.agent_id}
                  onClick={() => navigate(`/agents/${agent.agent_id}`)}
                  className="hover:bg-muted/40 transition-colors cursor-pointer"
                  role="button"
                  aria-label={`View agent ${agent.name}`}
                >
                  <td className="px-4 py-3 font-medium">{agent.name}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        AUTONOMY_COLORS[agent.autonomy_mode] ??
                        'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300'
                      }`}
                    >
                      {agent.autonomy_mode}
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
    </div>
  );
}
