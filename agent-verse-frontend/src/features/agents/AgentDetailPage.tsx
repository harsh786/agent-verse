import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Loader2, Download, Camera, RotateCcw,
  Edit3, Save, X, Clock, Target, ChevronDown, ChevronRight,
} from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { goalsApi, agentsApi, knowledgeApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { toast } from "@/stores/toast";

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000";

interface AgentVersion {
  snapshot_id: string;
  created_at: string;
  label?: string;
}

function hdrs(apiKey: string) {
  return { "X-API-Key": apiKey, "Content-Type": "application/json" };
}

async function fetchAgentDetail(apiKey: string, agentId: string) {
  const res = await fetch(`${API_BASE}/agents/${agentId}`, { headers: hdrs(apiKey) });
  if (!res.ok) throw new Error(`Agent not found: ${res.statusText}`);
  return res.json();
}

async function fetchAgentVersions(apiKey: string, agentId: string): Promise<AgentVersion[]> {
  const res = await fetch(`${API_BASE}/agents/${agentId}/versions`, { headers: hdrs(apiKey) });
  if (!res.ok) return [];
  return res.json();
}

async function createSnapshot(apiKey: string, agentId: string): Promise<{ snapshot_id: string }> {
  const res = await fetch(`${API_BASE}/agents/${agentId}/snapshot`, {
    method: "POST",
    headers: hdrs(apiKey),
  });
  if (!res.ok) throw new Error(`Failed to create snapshot: ${res.statusText}`);
  return res.json();
}

async function rollbackAgent(apiKey: string, agentId: string, snapshotId: string) {
  const res = await fetch(`${API_BASE}/agents/${agentId}/rollback/${snapshotId}`, {
    method: "POST",
    headers: { "X-API-Key": apiKey },
  });
  if (!res.ok) throw new Error(`Failed to rollback: ${res.statusText}`);
  return res.json();
}

async function exportAgentFormat(apiKey: string, agentId: string, format: string) {
  const res = await fetch(`${API_BASE}/agents/${agentId}/export?format=${format}`, {
    headers: hdrs(apiKey),
  });
  if (!res.ok) throw new Error(`Export failed: ${res.statusText}`);
  return res.json();
}

async function updateAgent(apiKey: string, agentId: string, patch: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/agents/${agentId}`, {
    method: "PUT",
    headers: hdrs(apiKey),
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Failed to update agent: ${res.statusText}`);
  return res.json();
}

// ── AgentDetailPage ────────────────────────────────────────────────────────────

type AgentTab = 'overview' | 'versions' | 'permissions' | 'knowledge' | 'rollout' | 'credentials';

export function AgentDetailPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const apiKey = useAuthStore((s) => s.apiKey);
  const [tab, setTab] = useState<AgentTab>('overview');
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<Record<string, string>>({});
  const [exportMsg, setExportMsg] = useState("");
  const [snapshotMsg, setSnapshotMsg] = useState("");
  const [versionOpen, setVersionOpen] = useState(false);
  const [readiness, setReadiness] = useState<any>(null);
  const [testGoal, setTestGoal] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string>('');

  const {
    data: agent,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => fetchAgentDetail(apiKey, agentId!),
    enabled: !!agentId,
  });

  const { data: versions = [] } = useQuery<AgentVersion[]>({
    queryKey: ["agent-versions", agentId],
    queryFn: () => fetchAgentVersions(apiKey, agentId!),
    enabled: !!agentId,
  });

  const { data: recentGoals } = useQuery({
    queryKey: ["goals", "byAgent", agentId],
    queryFn: async () => {
      const all = await goalsApi.list();
      return (all.goals ?? []).filter(
        (g) => (g as any).agent_id === agentId
      );
    },
    enabled: !!agentId,
  });

  const snapshotMutation = useMutation({
    mutationFn: () => createSnapshot(apiKey, agentId!),
    onSuccess: (data) => {
      setSnapshotMsg(`Snapshot created: ${data.snapshot_id}`);
      qc.invalidateQueries({ queryKey: ["agent-versions", agentId] });
      setTimeout(() => setSnapshotMsg(""), 4000);
    },
  });

  const rollbackMutation = useMutation({
    mutationFn: (snapshotId: string) => rollbackAgent(apiKey, agentId!, snapshotId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agentId] });
    },
  });

  const saveMutation = useMutation({
    mutationFn: () => updateAgent(apiKey, agentId!, editForm),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agentId] });
      setEditing(false);
    },
  });

  const handleExport = async (format: string) => {
    try {
      const data = await exportAgentFormat(apiKey, agentId!, format);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `agent-${agentId}-${format}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setExportMsg(`Exported as ${format} format`);
      setTimeout(() => setExportMsg(""), 3000);
    } catch (e) {
      setExportMsg(`Export failed: ${String(e)}`);
    }
  };

  const checkReadiness = async () => {
    const resp = await fetch(`${API_BASE}/agents/${agentId}/readiness`, {
      headers: { 'X-API-Key': apiKey },
    });
    if (resp.ok) setReadiness(await resp.json());
  };

  const handleTestAgent = async () => {
    if (!testGoal.trim()) return;
    setTesting(true);
    try {
      const data = await goalsApi.submit({ goal: testGoal, agent_id: agentId, dry_run: true });
      setTestResult(`Goal submitted: ${data.goal_id}. Plan: ${JSON.stringify((data as any).plan || (data as any).execution_context || {}, null, 2)}`);
    } finally {
      setTesting(false);
    }
  };

  // Phase-5: new tab queries
  const { data: permissions, isLoading: permsLoading } = useQuery({
    queryKey: ['agent-permissions', agentId],
    queryFn: () => agentsApi.getPermissions(agentId!),
    enabled: !!agentId && tab === 'permissions',
  });

  const { data: rolloutGate, isLoading: rolloutLoading } = useQuery({
    queryKey: ['agent-rollout', agentId],
    queryFn: () => agentsApi.getRolloutGate(agentId!),
    enabled: !!agentId && tab === 'rollout',
  });

  const { data: allKnowledge = [] } = useQuery({
    queryKey: ['knowledge'],
    queryFn: () => knowledgeApi.list(),
    enabled: tab === 'knowledge',
  });

  const cloneMutation = useMutation({
    mutationFn: () => agentsApi.clone(agentId!),
    onSuccess: (data) => {
      navigate(`/agents/${data.agent_id}`);
      toast({ kind: 'success', message: 'Agent cloned.' });
    },
    onError: (e) => toast({ kind: 'error', message: `Clone failed: ${e}` }),
  });

  const assignKnowledgeMutation = useMutation({
    mutationFn: (kId: string) => agentsApi.assignKnowledge(agentId!, kId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent', agentId] });
      toast({ kind: 'success', message: 'Knowledge assigned.' });
    },
  });

  const removeKnowledgeMutation = useMutation({
    mutationFn: (kId: string) => agentsApi.removeKnowledge(agentId!, kId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent', agentId] });
      toast({ kind: 'success', message: 'Knowledge removed.' });
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-40" data-testid="loading">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="text-center py-20 text-muted-foreground" data-testid="not-found">
        Agent not found.{" "}
        <button
          onClick={() => navigate("/agents")}
          className="text-primary hover:underline"
        >
          Back to agents
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Back */}
      <button
        onClick={() => navigate("/agents")}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        <ArrowLeft className="h-4 w-4" /> Back to agents
      </button>

      {/* Header */}
      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold" data-testid="agent-name">
              {agent.name}
            </h1>
            <p className="text-xs text-muted-foreground font-mono mt-1">{agent.agent_id}</p>
            <p className="text-sm text-muted-foreground mt-1">{agent.autonomy_mode}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={checkReadiness}
              className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700"
            >
              Check Readiness
            </button>
            <button
              onClick={() => cloneMutation.mutate()}
              disabled={cloneMutation.isPending}
              aria-label="Clone agent"
              className="px-3 py-1 border rounded text-sm hover:bg-muted disabled:opacity-50"
            >
              {cloneMutation.isPending ? 'Cloning…' : 'Clone'}
            </button>
            <button
              onClick={() => {
                setEditing((v) => !v);
                setEditForm({
                  name: agent.name ?? "",
                  goal_template: agent.goal_template ?? "",
                  autonomy_mode: agent.autonomy_mode ?? "",
                });
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-border rounded-md hover:bg-accent transition-colors"
            >
              {editing ? <X className="h-4 w-4" /> : <Edit3 className="h-4 w-4" />}
              {editing ? "Cancel" : "Edit"}
            </button>
          </div>
        </div>

        {/* Readiness widget */}
        {readiness && (
          <div className="mt-4 p-4 border rounded bg-muted/30">
            <div className="flex items-center gap-2 mb-2">
              <span className={`w-3 h-3 rounded-full ${readiness.ready ? 'bg-green-500' : 'bg-red-500'}`} />
              <span className="font-medium">{readiness.ready ? 'Production Ready' : 'Not Ready'}</span>
            </div>
            {readiness.checks?.map((check: any, i: number) => (
              <div key={i} className="flex items-center gap-2 text-sm text-muted-foreground py-1">
                <span>{check.status === 'pass' ? '✓' : '✗'}</span>
                <span>{check.message}</span>
              </div>
            ))}
          </div>
        )}

        {/* Edit form */}
        {editing && (
          <div className="mt-4 space-y-3 border-t border-border pt-4">
            <div>
              <label className="block text-xs font-medium mb-1">Name</label>
              <input
                value={editForm.name ?? ""}
                onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">Autonomy Mode</label>
              <select
                value={editForm.autonomy_mode ?? ""}
                onChange={(e) => setEditForm((f) => ({ ...f, autonomy_mode: e.target.value }))}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
              >
                <option value="supervised">supervised</option>
                <option value="bounded-autonomous">bounded-autonomous</option>
                <option value="fully-autonomous">fully-autonomous</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">Goal Template</label>
              <textarea
                value={editForm.goal_template ?? ""}
                onChange={(e) => setEditForm((f) => ({ ...f, goal_template: e.target.value }))}
                rows={3}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none resize-none"
              />
            </div>
            <div className="flex justify-end">
              <button
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending}
                className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground text-sm rounded-md hover:opacity-90 disabled:opacity-50"
              >
                {saveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                Save
              </button>
            </div>
          </div>
        )}

        {/* Test Agent */}
        <div className="mt-4 border-t pt-4">
          <h3 className="font-medium mb-2 text-sm">Test Agent</h3>
          <div className="flex gap-2">
            <input
              value={testGoal}
              onChange={(e) => setTestGoal(e.target.value)}
              placeholder="Enter a test goal..."
              className="flex-1 px-3 py-2 border rounded text-sm"
            />
            <button
              onClick={handleTestAgent}
              disabled={testing || !testGoal.trim()}
              className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm hover:opacity-90 disabled:opacity-50"
            >
              {testing ? 'Testing...' : 'Test (Dry Run)'}
            </button>
          </div>
          {testResult && (
            <pre className="mt-2 p-3 bg-muted/50 rounded text-xs overflow-auto max-h-40">
              {testResult}
            </pre>
          )}
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b">
        {(
          [
            { key: 'overview', label: 'Overview' },
            { key: 'versions', label: 'Versions' },
            { key: 'permissions', label: 'Permissions' },
            { key: 'knowledge', label: 'Knowledge' },
            { key: 'rollout', label: 'Rollout Gate' },
            { key: 'credentials', label: 'Credentials' },
          ] as { key: AgentTab; label: string }[]
        ).map(({ key, label }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Overview tab */}
      {tab === 'overview' && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {[
              { label: "Status", value: agent.status ?? "active" },
              { label: "Created", value: agent.created_at ? new Date(agent.created_at).toLocaleDateString() : "—" },
              { label: "Default Model", value: (agent as any).default_model ?? "—" },
            ].map(({ label, value }) => (
              <div key={label} className="bg-card border border-border rounded-lg px-4 py-3">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="font-medium text-sm mt-0.5 truncate">{value}</p>
              </div>
            ))}
          </div>

          {/* Connectors */}
          {Array.isArray(agent.connector_ids) && agent.connector_ids.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5">
              <h2 className="font-semibold text-sm mb-3" data-testid="connector-list">
                Connector IDs
              </h2>
              <div className="flex flex-wrap gap-2">
                {(agent.connector_ids as string[]).map((cid: string) => (
                  <span key={cid} className="bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300 text-xs px-2 py-1 rounded">
                    {cid}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Actions: Snapshot & Export */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4">
            <h2 className="font-semibold text-sm">Actions</h2>
            <div className="flex flex-wrap gap-3">
              <button
                onClick={() => snapshotMutation.mutate()}
                disabled={snapshotMutation.isPending}
                className="flex items-center gap-2 px-4 py-2 text-sm border border-border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
                data-testid="snapshot-btn"
              >
                {snapshotMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
                Take Snapshot
              </button>
              <button
                onClick={() => handleExport("openai")}
                className="flex items-center gap-2 px-4 py-2 text-sm border border-border rounded-md hover:bg-accent transition-colors"
                data-testid="export-btn"
              >
                <Download className="h-4 w-4" /> Export (OpenAI)
              </button>
              <button
                onClick={() => handleExport("anthropic")}
                className="flex items-center gap-2 px-4 py-2 text-sm border border-border rounded-md hover:bg-accent transition-colors"
              >
                <Download className="h-4 w-4" /> Export (Anthropic)
              </button>
            </div>
            {snapshotMsg && <p className="text-xs text-green-600">{snapshotMsg}</p>}
            {exportMsg && <p className="text-xs text-muted-foreground">{exportMsg}</p>}
          </div>

          {/* Recent Goals */}
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-border">
              <h2 className="font-semibold text-sm flex items-center gap-2">
                <Target className="h-4 w-4" /> Recent Goals
              </h2>
            </div>
            {!recentGoals || recentGoals.length === 0 ? (
              <p className="px-5 py-4 text-sm text-muted-foreground">No goals run by this agent yet.</p>
            ) : (
              <div className="divide-y divide-border">
                {recentGoals.slice(0, 5).map((g) => (
                  <div key={g.goal_id ?? g.id} className="flex items-center justify-between px-5 py-3 text-sm">
                    <p className="truncate flex-1">{g.goal}</p>
                  <span className={`ml-3 px-2 py-0.5 rounded-full text-xs flex-shrink-0 ${
                      g.status === "complete"
                        ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                        : g.status === "failed"
                        ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                        : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                    }`}>{g.status}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* Versions tab */}
      {tab === 'versions' && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <button
            onClick={() => setVersionOpen((v) => !v)}
            className="w-full flex items-center justify-between px-5 py-3 hover:bg-accent/50 transition-colors"
          >
            <h2 className="font-semibold text-sm">Version History ({versions.length})</h2>
            {versionOpen ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
          </button>
          {versionOpen && (
            <div className="divide-y divide-border border-t border-border">
              {versions.length === 0 ? (
                <p className="px-5 py-4 text-sm text-muted-foreground">No snapshots yet.</p>
              ) : (
                versions.map((v) => (
                  <div key={v.snapshot_id} className="flex items-center justify-between px-5 py-3">
                    <div className="min-w-0">
                      <p className="text-xs font-mono text-muted-foreground">{v.snapshot_id}</p>
                      {v.label && <p className="text-sm">{v.label}</p>}
                      <p className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                        <Clock className="h-3 w-3" />
                        {new Date(v.created_at).toLocaleString()}
                      </p>
                    </div>
                    <button
                      onClick={() => rollbackMutation.mutate(v.snapshot_id)}
                      disabled={rollbackMutation.isPending}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
                    >
                      <RotateCcw className="h-3.5 w-3.5" /> Rollback
                    </button>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      )}

      {/* Permissions tab */}
      {tab === 'permissions' && (
        <div className="space-y-4">
          {permsLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : !permissions ? (
            <EmptyState title="No permissions configured" />
          ) : (
            <div className="grid grid-cols-2 gap-4">
              <div className="p-3 rounded-lg border bg-card">
                <p className="text-xs font-medium text-muted-foreground mb-2">Read scopes</p>
                <ul className="space-y-1">
                  {permissions.read.map((s) => (
                    <li key={s} className="text-sm font-mono bg-muted rounded px-2 py-0.5">{s}</li>
                  ))}
                </ul>
              </div>
              <div className="p-3 rounded-lg border bg-card">
                <p className="text-xs font-medium text-muted-foreground mb-2">Write scopes</p>
                <ul className="space-y-1">
                  {permissions.write.map((s) => (
                    <li key={s} className="text-sm font-mono bg-muted rounded px-2 py-0.5">{s}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Knowledge tab */}
      {tab === 'knowledge' && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">Assign knowledge collections this agent can retrieve from.</p>
          {allKnowledge.length === 0 ? (
            <EmptyState title="No knowledge collections" description="Create a collection in the Knowledge page first." />
          ) : (
            <div className="divide-y border rounded-lg overflow-hidden">
              {allKnowledge.map((k) => (
                <div key={k.collection_id} className="flex items-center justify-between p-3 bg-card">
                  <span className="text-sm font-medium">{k.name}</span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => assignKnowledgeMutation.mutate(k.collection_id)}
                      className="text-xs px-2 py-1 rounded border hover:bg-muted"
                    >Assign</button>
                    <button
                      onClick={() => removeKnowledgeMutation.mutate(k.collection_id)}
                      className="text-xs px-2 py-1 rounded border text-red-600 hover:bg-red-50"
                    >Remove</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Rollout Gate tab */}
      {tab === 'rollout' && (
        <div className="space-y-3">
          {rolloutLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : !rolloutGate ? (
            <EmptyState
              title="No rollout gate configured"
              description="Rollout gates control traffic steering to this agent version."
            />
          ) : (
            <div className="p-4 rounded-lg border bg-card space-y-2">
              <div className="flex items-center gap-3">
                <StatusBadge status={rolloutGate.gate_status} />
                <span className="text-sm font-medium">{rolloutGate.traffic_pct}% traffic</span>
              </div>
              {rolloutGate.conditions.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1">Conditions</p>
                  <ul className="list-disc pl-4 text-sm space-y-1">
                    {rolloutGate.conditions.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Credentials tab */}
      {tab === 'credentials' && (
        <EmptyState
          title="Connector credentials"
          description="Credentials for MCP connectors used by this agent are managed in the Connectors page."
          action={<a href="/connectors" className="text-sm text-primary underline">Go to Connectors</a>}
        />
      )}
    </div>
  );
}
