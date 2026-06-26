import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Loader2, Download, Camera, RotateCcw,
  Edit3, Save, X, Clock, Target, ChevronDown, ChevronRight,
} from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { goalsApi } from "@/lib/api/client";

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
  const res = await fetch(`${API_BASE}/agents/${agentId}/rollback`, {
    method: "POST",
    headers: hdrs(apiKey),
    body: JSON.stringify({ snapshot_id: snapshotId }),
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
    method: "PATCH",
    headers: hdrs(apiKey),
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Failed to update agent: ${res.statusText}`);
  return res.json();
}

// ── AgentDetailPage ────────────────────────────────────────────────────────────

export function AgentDetailPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const apiKey = useAuthStore((s) => s.apiKey);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<Record<string, string>>({});
  const [exportMsg, setExportMsg] = useState("");
  const [snapshotMsg, setSnapshotMsg] = useState("");
  const [versionOpen, setVersionOpen] = useState(false);

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
      </div>

      {/* Metadata */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        {[
          { label: "Status", value: agent.status ?? "active" },
          { label: "Created", value: agent.created_at ? new Date(agent.created_at).toLocaleDateString() : "—" },
          { label: "Default Model", value: agent.default_model ?? "—" },
        ].map(({ label, value }) => (
          <div key={label} className="bg-card border border-border rounded-lg px-4 py-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="font-medium text-sm mt-0.5 truncate">{value}</p>
          </div>
        ))}
      </div>

      {/* Connectors */}
      {Array.isArray(agent.connector_requirements) && agent.connector_requirements.length > 0 && (
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-3" data-testid="connector-list">
            Required Connectors
          </h2>
          <div className="space-y-2">
            {(agent.connector_requirements as any[]).map((c: any, i: number) => (
              <div key={i} className="flex items-center justify-between text-sm">
                <span className="font-mono">{c.type ?? c}</span>
                {c.optional && (
                  <span className="text-xs text-muted-foreground">optional</span>
                )}
              </div>
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
            {snapshotMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Camera className="h-4 w-4" />
            )}
            Take Snapshot
          </button>
          <button
            onClick={() => handleExport("openai")}
            className="flex items-center gap-2 px-4 py-2 text-sm border border-border rounded-md hover:bg-accent transition-colors"
            data-testid="export-btn"
          >
            <Download className="h-4 w-4" />
            Export (OpenAI)
          </button>
          <button
            onClick={() => handleExport("anthropic")}
            className="flex items-center gap-2 px-4 py-2 text-sm border border-border rounded-md hover:bg-accent transition-colors"
          >
            <Download className="h-4 w-4" />
            Export (Anthropic)
          </button>
        </div>
        {snapshotMsg && <p className="text-xs text-green-600">{snapshotMsg}</p>}
        {exportMsg && <p className="text-xs text-muted-foreground">{exportMsg}</p>}
      </div>

      {/* Version history */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <button
          onClick={() => setVersionOpen((v) => !v)}
          className="w-full flex items-center justify-between px-5 py-3 hover:bg-accent/50 transition-colors"
        >
          <h2 className="font-semibold text-sm">Version History ({versions.length})</h2>
          {versionOpen ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
        </button>
        {versionOpen && (
          <div className="divide-y divide-border border-t border-border">
            {versions.length === 0 ? (
              <p className="px-5 py-4 text-sm text-muted-foreground">No snapshots yet.</p>
            ) : (
              versions.map((v) => (
                <div
                  key={v.snapshot_id}
                  className="flex items-center justify-between px-5 py-3"
                >
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

      {/* Recent Goals */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border">
          <h2 className="font-semibold text-sm flex items-center gap-2">
            <Target className="h-4 w-4" />
            Recent Goals
          </h2>
        </div>
        {!recentGoals || recentGoals.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted-foreground">
            No goals run by this agent yet.
          </p>
        ) : (
          <div className="divide-y divide-border">
            {recentGoals.slice(0, 5).map((g) => (
              <div
                key={g.goal_id ?? g.id}
                className="flex items-center justify-between px-5 py-3 text-sm"
              >
                <p className="truncate flex-1">{g.goal}</p>
                <span
                  className={`ml-3 px-2 py-0.5 rounded-full text-xs flex-shrink-0 ${
                    g.status === "complete"
                      ? "bg-green-100 text-green-700"
                      : g.status === "failed"
                      ? "bg-red-100 text-red-700"
                      : "bg-blue-100 text-blue-700"
                  }`}
                >
                  {g.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
