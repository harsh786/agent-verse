import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  goalsApi, agentsApi, knowledgeApi, credentialsApi,
  type CreateAgentRequest,
} from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';
import { Activity, ArrowLeft, Brain, Camera, ChevronDown, ChevronRight, Clock, Download, Edit3, Inbox, Loader2, Lock, RotateCcw, Save, Shield, Sliders, Target, X } from 'lucide-react';

interface AgentVersion {
  snapshot_id: string;
  created_at: string;
  label?: string;
}

// Fix 8: Typed interface replacing useState<any> for readiness
interface ReadinessResult {
  ready: boolean;
  score?: number;
  issues?: string[];
  connector_statuses?: Record<string, string>;
  checks?: Array<{ status: string; message: string }>;
}

// ── CredentialsTab ─────────────────────────────────────────────────────────────

function CredentialsTab({ agentId }: { agentId: string }) {
  const qc = useQueryClient();
  const [issuing, setIssuing] = useState(false);
  const [newScopes, setNewScopes] = useState('');

  const { data: creds = [], isLoading } = useQuery({
    queryKey: ['agent-credentials', agentId],
    queryFn: () => credentialsApi.list(agentId),
    enabled: !!agentId,
  });

  const issueMutation = useMutation({
    mutationFn: (scopes: string[]) =>
      credentialsApi.issue(agentId, { key_type: 'api_key', scopes }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-credentials', agentId] });
      setIssuing(false);
      setNewScopes('');
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (credId: string) => credentialsApi.revoke(agentId, credId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-credentials', agentId] }),
  });

  if (isLoading) return <Skeleton className="h-32 w-full" />;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-sm font-semibold text-foreground">Agent Credentials</h3>
        <button
          onClick={() => setIssuing(true)}
          className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90 transition-opacity"
        >
          Issue Credential
        </button>
      </div>

      {issuing && (
        <div className="border border-border rounded-lg p-4 space-y-3">
          <label className="text-sm text-muted-foreground">Scopes (comma-separated)</label>
          <input
            value={newScopes}
            onChange={(e) => setNewScopes(e.target.value)}
            placeholder="goals:read,goals:write"
            className="w-full border border-border rounded px-2 py-1 text-sm bg-background"
          />
          <div className="flex gap-2">
            <button
              onClick={() =>
                issueMutation.mutate(
                  newScopes
                    .split(',')
                    .map((s) => s.trim())
                    .filter(Boolean)
                )
              }
              disabled={issueMutation.isPending}
              className="px-3 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-50"
            >
              {issueMutation.isPending ? 'Issuing…' : 'Issue'}
            </button>
            <button
              onClick={() => setIssuing(false)}
              className="px-3 py-1 text-xs bg-muted text-muted-foreground rounded"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {(creds as any[]).length === 0 ? (
        <EmptyState
          icon={<Inbox size={40} />}
          title="No credentials issued"
          description="Issue API credentials scoped to this agent."
          variant="float"
        />
      ) : (
        <div className="space-y-2">
          {(creds as any[]).map((c: any) => {
            const credId = c.credential_id ?? c.key_id;
            return (
              <div
                key={credId}
                className="flex items-center justify-between border border-border rounded-lg p-3"
              >
                <div>
                  <p className="text-sm font-medium text-foreground">{credId}</p>
                  <p className="text-xs text-muted-foreground">
                    {(c.scopes || []).join(', ')} ·{' '}
                    Created{' '}
                    {c.created_at ? new Date(c.created_at).toLocaleDateString() : '—'}
                  </p>
                </div>
                <button
                  onClick={() => revokeMutation.mutate(credId)}
                  disabled={revokeMutation.isPending}
                  className="text-xs text-destructive hover:underline disabled:opacity-50"
                >
                  Revoke
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── AgentDetailPage ────────────────────────────────────────────────────────────

type AgentTab = 'overview' | 'versions' | 'permissions' | 'knowledge' | 'rollout' | 'credentials';

export function AgentDetailPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [tab, setTab] = useState<AgentTab>('overview');
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<Record<string, string>>({});
  // Fix 9: exportMsg replaced with toast — no local state needed
  const [snapshotMsg, setSnapshotMsg] = useState("");
  const [versionOpen, setVersionOpen] = useState(false);
  // Fix 8: Properly typed state
  const [readiness, setReadiness] = useState<ReadinessResult | null>(null);
  const [testGoal, setTestGoal] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string>('');

  // Fix 6: Use agentsApi.get instead of raw fetch
  const {
    data: agent,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => agentsApi.get(agentId!),
    enabled: !!agentId,
  });

  // Fix 6: Use agentsApi.listVersions instead of raw fetch
  const { data: versions = [] } = useQuery<AgentVersion[]>({
    queryKey: ["agent-versions", agentId],
    queryFn: () => agentsApi.listVersions(agentId!) as Promise<AgentVersion[]>,
    enabled: !!agentId,
  });

  // Fix 7: staleTime avoids excessive refetches; slice(0,10) limits result without a backend filter
  const { data: recentGoals } = useQuery({
    queryKey: ["goals", "byAgent", agentId],
    queryFn: async () => {
      const all = await goalsApi.list();
      return (all.goals ?? [])
        .filter((g) => (g as any).agent_id === agentId)
        .slice(0, 10);
    },
    enabled: !!agentId,
    staleTime: 60_000,
  });

  // Fix 6: Use agentsApi.snapshot instead of raw fetch
  const snapshotMutation = useMutation({
    mutationFn: () => agentsApi.snapshot(agentId!),
    onSuccess: (data) => {
      setSnapshotMsg(`Snapshot created: ${data.snapshot_id}`);
      qc.invalidateQueries({ queryKey: ["agent-versions", agentId] });
      setTimeout(() => setSnapshotMsg(""), 4000);
    },
  });

  // Fix 6: Use agentsApi.rollback instead of raw fetch
  const rollbackMutation = useMutation({
    mutationFn: (snapshotId: string) => agentsApi.rollback(agentId!, snapshotId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agentId] });
    },
  });

  // Fix 6: Use agentsApi.update instead of raw fetch
  const saveMutation = useMutation({
    mutationFn: () => agentsApi.update(agentId!, editForm as Partial<CreateAgentRequest>),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agentId] });
      setEditing(false);
    },
  });

  // Fix 6+9: Use agentsApi.export; replace setExportMsg with toast
  const handleExport = async (format: string) => {
    try {
      const data = await agentsApi.export(agentId!, format as "openai" | "anthropic");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `agent-${agentId}-${format}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast({ kind: "success", message: `Exported as ${format} format` });
    } catch (e) {
      toast({ kind: "error", message: `Export failed: ${String(e)}` });
    }
  };

  const checkReadinessMutation = useMutation({
    mutationFn: () => agentsApi.checkReadiness(agentId!),
    onSuccess: (data) => {
      setReadiness(data);
      toast({
        kind: data.ready ? 'success' : 'warning',
        message: data.ready ? 'Agent is ready!' : `${data.issues?.length ?? 0} issues found`,
      });
    },
    onError: (e) => toast({ kind: 'error', message: `Readiness check failed: ${String(e)}` }),
  });

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
  const { data: permissions, isLoading: permsLoading, error: permsError } = useQuery({
    queryKey: ['agent-permissions', agentId],
    queryFn: () => agentsApi.getPermissions(agentId!),
    enabled: !!agentId && tab === 'permissions',
  });

  const { data: rolloutGate, isLoading: rolloutLoading, error: rolloutError } = useQuery({
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
    <JARVISPageShell>
    <JARVISStagger className="space-y-6 max-w-4xl">
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
              onClick={() => checkReadinessMutation.mutate()}
              disabled={checkReadinessMutation.isPending}
              className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700 flex items-center gap-1.5 disabled:opacity-50"
            >
              {checkReadinessMutation.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Shield className="h-4 w-4" />}
              {checkReadinessMutation.isPending ? 'Checking…' : 'Check Readiness'}
            </button>
            <button
              onClick={() => cloneMutation.mutate()}
              disabled={cloneMutation.isPending}
              aria-label="Clone agent"
              className="px-3 py-1 border rounded text-sm hover:bg-muted disabled:opacity-50"
            >
              {cloneMutation.isPending ? 'Cloning…' : t('agents.actions.clone')}
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
              {editing ? t('common.cancel') : t('common.edit')}
            </button>
            <button
              onClick={() => navigate(`/agents/${agentId}/radar`)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-border rounded-lg hover:bg-muted/60 transition-colors"
              aria-label="View agent health radar"
              title="6-axis health radar chart"
            >
              <Activity className="h-3.5 w-3.5" aria-hidden="true" />
              Health Radar
            </button>
            <button
              onClick={() => navigate(`/agents/${agentId}/personality`)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-border rounded-lg hover:bg-muted/60 transition-colors"
              aria-label="Configure agent personality"
              title="Visual personality slider configuration"
            >
              <Sliders className="h-3.5 w-3.5" aria-hidden="true" />
              Personality
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
                {t('common.save')}
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
          ) : permsError ? (
            <EmptyState title="Failed to load permissions" description={String(permsError)} />
          ) : !permissions ? (
            <EmptyState
          icon={<Lock size={40} />}
          title="No permissions configured"
          variant="float"
        />
          ) : (() => {
            // Backend returns { agent_id, permissions: [{tool_name, level, ...}] | {} }
            // Normalise to array
            const raw = (permissions as any);
            const permList: any[] = Array.isArray(raw)
              ? raw
              : Array.isArray(raw?.permissions)
              ? raw.permissions
              : typeof raw?.permissions === 'object' && raw?.permissions !== null
              ? Object.entries(raw.permissions).map(([k, v]) => ({ tool_name: k, level: v }))
              : [];

            if (permList.length === 0) {
              return <EmptyState
          icon={<Lock size={40} />}
          title="No permissions configured"
          description="This agent has no tool-level permission rules."
          variant="float"
        />;
            }

            return (
              <div className="border rounded-lg overflow-hidden">
                <div className="px-4 py-2 bg-muted/40 border-b text-xs font-semibold uppercase tracking-wide text-muted-foreground grid grid-cols-4 gap-2">
                  <span>Tool</span><span>Level</span><span>Daily limit</span><span>Per-goal limit</span>
                </div>
                {permList.map((p: any, i: number) => (
                  <div key={i} className="px-4 py-2.5 border-b last:border-0 text-sm grid grid-cols-4 gap-2 bg-card items-center">
                    <span className="font-mono text-xs">{p.tool_name ?? p.tool ?? '—'}</span>
                    <span className={`capitalize text-xs font-medium ${p.level === 'deny' ? 'text-red-600' : p.level === 'allow' ? 'text-green-600' : 'text-muted-foreground'}`}>
                      {p.level ?? '—'}
                    </span>
                    <span className="text-muted-foreground">{p.daily_limit ?? '∞'}</span>
                    <span className="text-muted-foreground">{p.per_goal_limit ?? '∞'}</span>
                  </div>
                ))}
              </div>
            );
          })()}
        </div>
      )}

      {/* Knowledge tab */}
      {tab === 'knowledge' && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">Assign knowledge collections this agent can retrieve from.</p>
          {allKnowledge.length === 0 ? (
            <EmptyState
          icon={<Brain size={40} />}
          title="No knowledge collections"
          description="Create a collection in the Knowledge page first."
          variant="float"
        />
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
          ) : rolloutError ? (
            <EmptyState title="Failed to load rollout gate" description={String(rolloutError)} />
          ) : !rolloutGate ? (
            <EmptyState
          icon={<Inbox size={40} />}
          title="No rollout gate configured"
          description="Rollout gates control traffic steering to this agent version."
          variant="float"
        />
          ) : (() => {
            // Backend returns { gate_passed, reason, run_count, pass_rate, avg_score, agent_id }
            const raw = rolloutGate as any;
            const gatePassed: boolean = raw.gate_passed ?? raw.gate_status === 'passed';
            const passRate: number = raw.pass_rate ?? 0;
            const runCount: number = raw.run_count ?? 0;
            const avgScore: number = raw.avg_score ?? 0;
            const reason: string = raw.reason ?? '';
            const conditions: string[] = Array.isArray(raw.conditions) ? raw.conditions : [];

            return (
              <div className="p-4 rounded-lg border bg-card space-y-3">
                <div className="flex items-center gap-3">
                  <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-semibold ${gatePassed ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}>
                    {gatePassed ? '✓ Gate passed' : '✗ Gate blocked'}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div className="bg-muted/40 rounded-lg p-3">
                    <p className="text-xs text-muted-foreground">Pass rate</p>
                    <p className="font-semibold text-lg">{(passRate * 100).toFixed(1)}%</p>
                  </div>
                  <div className="bg-muted/40 rounded-lg p-3">
                    <p className="text-xs text-muted-foreground">Runs</p>
                    <p className="font-semibold text-lg">{runCount}</p>
                  </div>
                  <div className="bg-muted/40 rounded-lg p-3">
                    <p className="text-xs text-muted-foreground">Avg score</p>
                    <p className="font-semibold text-lg">{(avgScore * 100).toFixed(0)}%</p>
                  </div>
                </div>
                {reason && (
                  <p className="text-sm text-muted-foreground italic">{reason}</p>
                )}
                {conditions.length > 0 && (
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Conditions</p>
                    <ul className="list-disc pl-4 text-sm space-y-1">
                      {conditions.map((c, i) => <li key={i}>{c}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      )}

      {/* Credentials tab */}
      {tab === 'credentials' && (
        <CredentialsTab agentId={agentId!} />
      )}
    </JARVISStagger>
    </JARVISPageShell>
  );
}
