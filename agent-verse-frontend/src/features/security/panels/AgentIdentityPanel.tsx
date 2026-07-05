import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../../../stores/auth';

const API = import.meta.env.VITE_API_URL || '';

function apiFetch(path: string, apiKey: string, opts?: RequestInit) {
  return fetch(`${API}${path}`, {
    ...opts,
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json', ...opts?.headers },
  }).then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); });
}

interface AgentKey {
  key_id: string;
  name: string;
  allowed_tools: string[] | null;
  denied_tools: string[];
  created_at: number;
  last_used_at: number | null;
  is_active: boolean;
  use_count: number;
}

function AgentKeyCard({ agentId, apiKey }: { agentId: string; apiKey: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ['agent-keys', agentId],
    queryFn: () => apiFetch(`/agents/${agentId}/keys`, apiKey),
    enabled: !!agentId && !!apiKey,
  });

  const revokeMutation = useMutation({
    mutationFn: (keyId: string) => apiFetch(`/agents/${agentId}/keys/${keyId}`, apiKey, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-keys', agentId] }),
  });

  const keys: AgentKey[] = data?.keys || [];

  if (!keys.length) return (
    <div className="text-xs text-muted-foreground py-2">No agent keys. Create one to enable per-agent access control.</div>
  );

  return (
    <div className="space-y-2">
      {keys.map(k => (
        <div key={k.key_id} className={`rounded-lg border p-3 ${k.is_active ? 'bg-card' : 'bg-muted/50 opacity-60'}`}>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-foreground">{k.name}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Used {k.use_count}x · {k.last_used_at ? new Date(k.last_used_at * 1000).toLocaleDateString() : 'Never used'}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span className={`text-xs px-2 py-0.5 rounded-full ${k.is_active ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-muted text-muted-foreground'}`}>
                {k.is_active ? 'Active' : 'Revoked'}
              </span>
              {k.is_active && (
                <button
                  onClick={() => revokeMutation.mutate(k.key_id)}
                  className="text-xs text-destructive hover:underline"
                >
                  Revoke
                </button>
              )}
            </div>
          </div>
          {k.allowed_tools && (
            <div className="mt-2 flex flex-wrap gap-1">
              <span className="text-xs text-muted-foreground">Allowed:</span>
              {k.allowed_tools.slice(0, 3).map(t => (
                <span key={t} className="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary">{t}</span>
              ))}
              {k.allowed_tools.length > 3 && <span className="text-xs text-muted-foreground">+{k.allowed_tools.length - 3}</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function AgentIdentityPanel() {
  const apiKey = useAuthStore(s => s.apiKey) || '';
  const [selectedAgent, setSelectedAgent] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: '', allowed_tools: '', denied_tools: '' });
  const qc = useQueryClient();

  const { data: agentsData } = useQuery({
    queryKey: ['agents-list'],
    queryFn: () => apiFetch('/agents?limit=50', apiKey),
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: () => apiFetch(`/agents/${selectedAgent}/keys`, apiKey, {
      method: 'POST',
      body: JSON.stringify({
        name: form.name,
        allowed_tools: form.allowed_tools ? form.allowed_tools.split(',').map(s => s.trim()) : null,
        denied_tools: form.denied_tools ? form.denied_tools.split(',').map(s => s.trim()) : [],
      }),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-keys', selectedAgent] });
      setShowCreate(false);
      setForm({ name: '', allowed_tools: '', denied_tools: '' });
    },
  });

  const agents = agentsData?.agents || [];

  return (
    <div className="space-y-6">
      {/* Concept explanation */}
      <div className="rounded-xl border bg-blue-50 dark:bg-blue-900/20 p-4">
        <h3 className="font-semibold text-foreground mb-1">What is Agent Identity?</h3>
        <p className="text-sm text-muted-foreground">
          Every agent can have its own scoped API key — separate from your tenant key.
          Agent keys enforce <strong>per-agent tool allowlists</strong> (e.g. &ldquo;this agent can only call jira_*&rdquo;),
          generate signed <strong>capability manifests</strong> for external verification,
          and track <strong>delegation lineage</strong> when agents spawn sub-agents.
        </p>
      </div>

      {/* Key management */}
      <div className="rounded-xl border bg-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-foreground">Per-Agent API Keys</h2>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:opacity-90"
          >
            + Create Key
          </button>
        </div>

        {/* Agent selector */}
        <div className="mb-4">
          <select
            className="w-full border rounded-lg px-3 py-2 bg-background text-foreground text-sm"
            value={selectedAgent}
            onChange={e => setSelectedAgent(e.target.value)}
          >
            <option value="">Select an agent…</option>
            {agents.map((a: Record<string, string>) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </div>

        {showCreate && selectedAgent && (
          <div className="rounded-lg border bg-muted/30 p-4 mb-4 space-y-3">
            <h3 className="text-sm font-semibold text-foreground">New Agent Key</h3>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Key Name</label>
              <input
                className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground"
                placeholder="e.g. jira-only-key"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Allowed Tools (comma-separated, or blank for all)</label>
              <input
                className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground"
                placeholder="jira_*, slack_send_message"
                value={form.allowed_tools}
                onChange={e => setForm(f => ({ ...f, allowed_tools: e.target.value }))}
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Denied Tools (comma-separated)</label>
              <input
                className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground"
                placeholder="delete_*, drop_*"
                value={form.denied_tools}
                onChange={e => setForm(f => ({ ...f, denied_tools: e.target.value }))}
              />
            </div>
            <button
              onClick={() => createMutation.mutate()}
              disabled={!form.name || createMutation.isPending}
              className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating…' : 'Create Key'}
            </button>
          </div>
        )}

        {selectedAgent && <AgentKeyCard agentId={selectedAgent} apiKey={apiKey} />}
      </div>

      {/* Delegation lineage explainer */}
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold text-foreground mb-3">Delegation Lineage</h2>
        <div className="bg-muted/50 rounded-lg p-4 font-mono text-xs text-foreground">
          <p className="text-muted-foreground mb-2">Example delegation chain:</p>
          <p>user:alice → agent:CEO-Agent → agent:CTO-Agent → called <span className="text-primary">github_create_pr</span></p>
        </div>
        <p className="text-xs text-muted-foreground mt-3">
          Every tool call in a multi-agent workflow is traced back to the originating user.
          This chain appears in every audit record.
        </p>
      </div>
    </div>
  );
}
