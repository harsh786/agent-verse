import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

// ── Types ────────────────────────────────────────────────────────────────────

interface Policy {
  policy_id: string;
  name: string;
  tools_pattern: string;
  action: 'deny' | 'require_approval';
  created_at?: string;
}

interface ApprovalRequest {
  request_id: string;
  goal_id: string;
  tool_name: string;
  action?: string;
  reason?: string;
  created_at?: string;
}

interface AuditEntry {
  entry_id?: string;
  goal_id: string;
  tool_name: string;
  action?: string;
  result?: string;
  timestamp?: string;
}

interface Budget {
  per_goal_usd: number;
  per_tenant_daily_usd: number;
}

// ── API helpers ───────────────────────────────────────────────────────────────

const hdrs = (apiKey: string) => ({
  'X-API-Key': apiKey,
  'Content-Type': 'application/json',
});

async function apiFetch<T>(apiKey: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: hdrs(apiKey),
    ...init,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Tab components ─────────────────────────────────────────────────────────

function PoliciesTab({ apiKey }: { apiKey: string }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: '', tools_pattern: '', action: 'deny' });
  const [showForm, setShowForm] = useState(false);

  const { data: policies = [], isLoading, error } = useQuery({
    queryKey: ['policies'],
    queryFn: () => apiFetch<Policy[]>(apiKey, '/governance/policies'),
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<Policy>(apiKey, '/governance/policies', {
        method: 'POST',
        body: JSON.stringify(form),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['policies'] });
      setForm({ name: '', tools_pattern: '', action: 'deny' });
      setShowForm(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(apiKey, `/governance/policies/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['policies'] }),
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button
          onClick={() => setShowForm((v) => !v)}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90"
        >
          {showForm ? 'Cancel' : '+ New Policy'}
        </button>
      </div>

      {showForm && (
        <div className="bg-card border border-border rounded-xl p-4 space-y-3">
          <h3 className="font-medium text-sm">New Policy</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1">Name</label>
              <input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="block-shell-commands"
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">Tools Pattern (glob)</label>
              <input
                value={form.tools_pattern}
                onChange={(e) => setForm((f) => ({ ...f, tools_pattern: e.target.value }))}
                placeholder="shell:*"
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">Action</label>
              <select
                value={form.action}
                onChange={(e) => setForm((f) => ({ ...f, action: e.target.value }))}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="deny">Deny</option>
                <option value="require_approval">Require Approval</option>
              </select>
            </div>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-red-600">{String(createMutation.error)}</p>
          )}
          <div className="flex justify-end">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!form.name.trim() || !form.tools_pattern.trim() || createMutation.isPending}
              className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
            >
              {createMutation.isPending ? 'Saving…' : 'Save Policy'}
            </button>
          </div>
        </div>
      )}

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-muted-foreground">Loading…</div>
        ) : error ? (
          <div className="py-10 text-center text-sm text-red-500">Failed to load policies.</div>
        ) : policies.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted-foreground">No policies defined.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                {['Name', 'Tools Pattern', 'Action', ''].map((h) => (
                  <th key={h} className="text-left px-4 py-3 font-medium text-muted-foreground">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {policies.map((p) => (
                <tr key={p.policy_id} className="hover:bg-accent/50 transition-colors">
                  <td className="px-4 py-3 font-medium">{p.name}</td>
                  <td className="px-4 py-3 font-mono text-xs">{p.tools_pattern}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        p.action === 'deny'
                          ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300'
                          : 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300'
                      }`}
                    >
                      {p.action.replace('_', ' ')}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => deleteMutation.mutate(p.policy_id)}
                      disabled={deleteMutation.isPending}
                      className="text-destructive hover:opacity-70 text-sm disabled:opacity-40"
                    >
                      Delete
                    </button>
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

function ApprovalsTab({ apiKey }: { apiKey: string }) {
  const qc = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const approver = useAuthStore((s) => s.tenantId) || 'ui-user';

  const { data: approvals = [], isLoading, error } = useQuery({
    queryKey: ['approvals'],
    queryFn: () => apiFetch<ApprovalRequest[]>(apiKey, '/governance/approvals'),
    enabled: !!apiKey,
    refetchInterval: 10_000,
  });

  const approveMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      apiFetch<void>(apiKey, `/governance/approvals/${id}/approve`, {
        method: 'POST',
        body: JSON.stringify({ approver, note }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      apiFetch<void>(apiKey, `/governance/approvals/${id}/reject`, {
        method: 'POST',
        body: JSON.stringify({ approver, note }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  });

  return (
    <div className="space-y-4">
      {isLoading ? (
        <div className="py-10 text-center text-sm text-muted-foreground">Loading…</div>
      ) : error ? (
        <div className="py-10 text-center text-sm text-red-500">Failed to load approvals.</div>
      ) : approvals.length === 0 ? (
        <div className="bg-card border border-border rounded-xl py-10 text-center text-sm text-muted-foreground">
          No pending approvals. Your agents are running autonomously.
        </div>
      ) : (
        approvals.map((req) => (
          <div key={req.request_id} className="bg-card border border-orange-200 rounded-xl p-4">
            <div className="flex justify-between items-start mb-2">
              <div>
                <p className="font-medium text-sm">{req.tool_name}</p>
                <p className="text-xs text-muted-foreground font-mono mt-0.5">
                  goal: {req.goal_id}
                </p>
              </div>
              {req.created_at && (
                <span className="text-xs text-muted-foreground">
                  {new Date(req.created_at).toLocaleString()}
                </span>
              )}
            </div>
            {req.reason && (
              <p className="text-sm text-muted-foreground mb-3">{req.reason}</p>
            )}
            <textarea
              value={notes[req.request_id] ?? ''}
              onChange={(e) =>
                setNotes((n) => ({ ...n, [req.request_id]: e.target.value }))
              }
              placeholder="Optional note…"
              rows={2}
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary resize-none mb-3"
            />
            <div className="flex gap-2">
              <button
                onClick={() =>
                  approveMutation.mutate({
                    id: req.request_id,
                    note: notes[req.request_id] ?? '',
                  })
                }
                disabled={approveMutation.isPending}
                className="px-4 py-1.5 bg-green-600 text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50"
              >
                Approve
              </button>
              <button
                onClick={() =>
                  rejectMutation.mutate({
                    id: req.request_id,
                    note: notes[req.request_id] ?? '',
                  })
                }
                disabled={rejectMutation.isPending}
                className="px-4 py-1.5 bg-red-600 text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50"
              >
                Reject
              </button>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function AuditTab({ apiKey }: { apiKey: string }) {
  const [goalId, setGoalId] = useState('');
  const [toolName, setToolName] = useState('');
  const [submitted, setSubmitted] = useState(false);

  const { data: entries = [], isLoading, error, refetch } = useQuery({
    queryKey: ['audit', goalId, toolName],
    queryFn: () => {
      const params = new URLSearchParams({ limit: '50' });
      if (goalId) params.set('goal_id', goalId);
      if (toolName) params.set('tool_name', toolName);
      return apiFetch<AuditEntry[]>(apiKey, `/governance/audit?${params}`);
    },
    enabled: submitted && !!apiKey,
  });

  const handleSearch = () => {
    setSubmitted(true);
    refetch();
  };

  return (
    <div className="space-y-4">
      <div className="bg-card border border-border rounded-xl p-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-medium mb-1">Goal ID (optional)</label>
            <input
              value={goalId}
              onChange={(e) => setGoalId(e.target.value)}
              placeholder="goal_..."
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">Tool Name (optional)</label>
            <input
              value={toolName}
              onChange={(e) => setToolName(e.target.value)}
              placeholder="shell:execute"
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={handleSearch}
              disabled={isLoading}
              className="w-full bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
            >
              {isLoading ? 'Searching…' : 'Search'}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="text-sm text-red-500">Failed to load audit log.</div>
      )}

      {submitted && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          {entries.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">
              No audit entries found.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  {['Goal ID', 'Tool', 'Action', 'Result', 'Timestamp'].map((h) => (
                    <th key={h} className="text-left px-4 py-3 font-medium text-muted-foreground">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {entries.map((e, i) => (
                  <tr key={e.entry_id ?? i} className="hover:bg-accent/50">
                    <td className="px-4 py-3 font-mono text-xs">{e.goal_id}</td>
                    <td className="px-4 py-3 text-xs">{e.tool_name}</td>
                    <td className="px-4 py-3 text-xs">{e.action ?? '—'}</td>
                    <td className="px-4 py-3">
                      {e.result && (
                        <span
                          className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                            e.result === 'allowed'
                              ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300'
                              : 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300'
                          }`}
                        >
                          {e.result}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">
                      {e.timestamp ? new Date(e.timestamp).toLocaleString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

function BudgetTab({ apiKey }: { apiKey: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ per_goal_usd: 0, per_tenant_daily_usd: 0 });

  const { data: budget, isLoading, error } = useQuery({
    queryKey: ['budget'],
    queryFn: () => apiFetch<Budget>(apiKey, '/governance/budget'),
    enabled: !!apiKey,
  });

  React.useEffect(() => {
    if (budget) setForm(budget);
  }, [budget]);

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<Budget>(apiKey, '/governance/budget', {
        method: 'PUT',
        body: JSON.stringify(form),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['budget'] });
      setEditing(false);
    },
  });

  if (isLoading) {
    return <div className="py-10 text-center text-sm text-muted-foreground">Loading…</div>;
  }
  if (error) {
    return <div className="py-10 text-center text-sm text-red-500">Failed to load budget.</div>;
  }

  return (
    <div className="max-w-md space-y-4">
      <div className="bg-card border border-border rounded-xl p-5 space-y-4">
        <div className="flex justify-between items-center">
          <h3 className="font-semibold text-sm">Budget Limits</h3>
          <button
            onClick={() => setEditing((v) => !v)}
            className="text-sm text-primary hover:opacity-70"
          >
            {editing ? 'Cancel' : 'Edit'}
          </button>
        </div>

        {editing ? (
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium mb-1">Per Goal (USD)</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={form.per_goal_usd}
                onChange={(e) =>
                  setForm((f) => ({ ...f, per_goal_usd: parseFloat(e.target.value) || 0 }))
                }
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">Per Tenant Daily (USD)</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={form.per_tenant_daily_usd}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    per_tenant_daily_usd: parseFloat(e.target.value) || 0,
                  }))
                }
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            {saveMutation.isError && (
              <p className="text-xs text-red-600">{String(saveMutation.error)}</p>
            )}
            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending}
              className="w-full bg-primary text-primary-foreground py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
            >
              {saveMutation.isPending ? 'Saving…' : 'Save Budget'}
            </button>
          </div>
        ) : (
          <dl className="space-y-3">
            <div className="flex justify-between text-sm">
              <dt className="text-muted-foreground">Per Goal Limit</dt>
              <dd className="font-medium">${budget?.per_goal_usd?.toFixed(2) ?? '—'}</dd>
            </div>
            <div className="flex justify-between text-sm">
              <dt className="text-muted-foreground">Daily Tenant Limit</dt>
              <dd className="font-medium">
                ${budget?.per_tenant_daily_usd?.toFixed(2) ?? '—'}
              </dd>
            </div>
          </dl>
        )}
      </div>
    </div>
  );
}

// ── Emergency Stop Banner ─────────────────────────────────────────────────────

function EmergencyStopBanner({ apiKey }: { apiKey: string }) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [emergencyActive, setEmergencyActive] = useState(false);
  const [stats, setStats] = useState<{ cancelled_goals: number; rejected_approvals: number } | null>(null);

  const stopMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string; cancelled_goals: number; rejected_approvals: number }>(
        apiKey,
        '/governance/emergency-stop',
        { method: 'POST' }
      ),
    onSuccess: (data) => {
      setEmergencyActive(true);
      setStats({ cancelled_goals: data.cancelled_goals, rejected_approvals: data.rejected_approvals });
      setShowConfirm(false);
    },
  });

  const clearMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string; tenant_id: string }>(
        apiKey,
        '/governance/emergency-stop',
        { method: 'DELETE' }
      ),
    onSuccess: () => {
      setEmergencyActive(false);
      setStats(null);
    },
  });

  return (
    <>
      {emergencyActive ? (
        <div className="border border-red-300 bg-red-50 dark:bg-red-950/30 rounded-xl p-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-red-700 dark:text-red-400">
              ⚠ Emergency Stop Active — All goal execution halted
            </p>
            {stats && (
              <p className="text-xs text-red-600 dark:text-red-400 mt-1">
                {stats.cancelled_goals} goals cancelled · {stats.rejected_approvals} approvals rejected
              </p>
            )}
          </div>
          <button
            onClick={() => clearMutation.mutate()}
            disabled={clearMutation.isPending}
            className="px-3 py-1.5 text-sm border border-red-300 rounded-md text-red-700 hover:bg-red-100 disabled:opacity-50 transition-colors"
          >
            {clearMutation.isPending ? 'Clearing…' : 'Clear Emergency Stop'}
          </button>
        </div>
      ) : (
        <div className="flex justify-end">
          <button
            onClick={() => setShowConfirm(true)}
            className="px-4 py-2 bg-red-600 text-white text-sm font-semibold rounded-lg hover:bg-red-700 transition-colors"
          >
            Emergency Stop
          </button>
        </div>
      )}

      {showConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-card border border-border rounded-xl p-6 max-w-sm w-full shadow-2xl space-y-4">
            <h2 className="text-lg font-bold text-red-600">Activate Emergency Stop?</h2>
            <p className="text-sm text-muted-foreground">
              Are you sure? This will immediately cancel all running goals and reject all pending
              approvals for your tenant.
            </p>
            {stopMutation.isError && (
              <p className="text-xs text-red-600">{String(stopMutation.error)}</p>
            )}
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 text-sm border border-border rounded-md hover:bg-accent transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => stopMutation.mutate()}
                disabled={stopMutation.isPending}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50 transition-colors"
              >
                {stopMutation.isPending ? 'Activating…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

type Tab = 'policies' | 'approvals' | 'audit' | 'budget';

const TABS: Tab[] = ['policies', 'approvals', 'audit', 'budget'];

export function GovernancePage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [tab, setTab] = useState<Tab>('policies');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Governance</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Policies, approvals, audit log, and budget controls
        </p>
      </div>

      {/* Emergency Stop — always-visible safety control */}
      <EmergencyStopBanner apiKey={apiKey} />

      {/* Tabs */}
      <div className="flex gap-4 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-2 px-1 capitalize font-medium text-sm transition-colors ${
              tab === t
                ? 'border-b-2 border-primary text-primary'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'policies' && <PoliciesTab apiKey={apiKey} />}
      {tab === 'approvals' && <ApprovalsTab apiKey={apiKey} />}
      {tab === 'audit' && <AuditTab apiKey={apiKey} />}
      {tab === 'budget' && <BudgetTab apiKey={apiKey} />}
    </div>
  );
}
