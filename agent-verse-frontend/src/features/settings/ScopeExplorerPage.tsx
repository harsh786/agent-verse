import { useState } from "react";
import type { ComponentType, JSX } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Key, Lock, CheckCircle2, Copy, Trash2, Plus, Eye, EyeOff,
  Shield, Search, ChevronDown, ChevronRight, AlertCircle,
  Target, Bot, BookOpen, Plug, BarChart3, X,
} from "lucide-react";
import { tenantsApi } from "@/lib/api/client";
import type { ApiKeyResponse } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { toast } from "@/stores/toast";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ScopeDefinition { name: string; description: string; examples: string[] }
interface ScopeGroup { resource: string; icon: ComponentType<{ className?: string }>; scopes: ScopeDefinition[] }

// ── Scope catalog ─────────────────────────────────────────────────────────────

const SCOPE_GROUPS: ScopeGroup[] = [
  { resource: "goals", icon: Target, scopes: [
    { name: "goals:read",   description: "List and read goals and their status",     examples: ["GET /goals", "GET /goals/:id"] },
    { name: "goals:write",  description: "Submit and create new goals",              examples: ["POST /goals"] },
    { name: "goals:cancel", description: "Cancel running goals mid-execution",       examples: ["POST /goals/:id/cancel"] },
    { name: "goals:batch",  description: "Submit goals in bulk batches",             examples: ["POST /goals/batch"] },
  ]},
  { resource: "agents", icon: Bot, scopes: [
    { name: "agents:read",     description: "List and view agent configurations",        examples: ["GET /agents"] },
    { name: "agents:write",    description: "Create and update agent configurations",    examples: ["POST /agents"] },
    { name: "agents:delete",   description: "Permanently delete agents",                examples: ["DELETE /agents/:id"] },
    { name: "agents:snapshot", description: "Take versioned snapshots of agent configs", examples: ["POST /agents/:id/snapshot"] },
  ]},
  { resource: "knowledge", icon: BookOpen, scopes: [
    { name: "knowledge:read",   description: "Search and read knowledge collections", examples: ["GET /knowledge/collections"] },
    { name: "knowledge:write",  description: "Create collections and ingest documents", examples: ["POST /knowledge/ingest"] },
    { name: "knowledge:delete", description: "Delete knowledge collections",           examples: ["DELETE /knowledge/collections/:id"] },
  ]},
  { resource: "connectors", icon: Plug, scopes: [
    { name: "connectors:read",   description: "List and view registered connectors", examples: ["GET /connectors"] },
    { name: "connectors:write",  description: "Register new MCP connectors",         examples: ["POST /connectors"] },
    { name: "connectors:delete", description: "Unregister connectors",               examples: ["DELETE /connectors/:id"] },
  ]},
  { resource: "governance", icon: Shield, scopes: [
    { name: "governance:read",    description: "View policies and approvals",             examples: ["GET /governance/policies"] },
    { name: "governance:write",   description: "Create and manage governance policies",   examples: ["POST /governance/policies"] },
    { name: "governance:approve", description: "Approve or reject HITL requests",        examples: ["POST /governance/approvals/:id/approve"] },
  ]},
  { resource: "analytics", icon: BarChart3, scopes: [
    { name: "analytics:read",   description: "View cost, eval, and performance metrics", examples: ["GET /analytics/costs"] },
    { name: "analytics:export", description: "Export analytics data and training sets",  examples: ["POST /intelligence/export-training-data"] },
  ]},
];

const ALL_SCOPES = SCOPE_GROUPS.flatMap((g) => g.scopes.map((s) => s.name));

const PLAN_SCOPES: Record<string, string[]> = {
  free:         ["goals:read", "agents:read", "knowledge:read"],
  starter:      ["goals:read", "goals:write", "agents:read", "agents:write", "knowledge:read", "knowledge:write", "connectors:read"],
  professional: ["goals:read","goals:write","goals:cancel","goals:batch","agents:read","agents:write","agents:delete","agents:snapshot","knowledge:read","knowledge:write","knowledge:delete","connectors:read","connectors:write","connectors:delete","governance:read","analytics:read","analytics:export"],
  enterprise:   ALL_SCOPES,
};

const PLAN_ORDER = ["free", "starter", "professional", "enterprise"] as const;
type PlanTier = (typeof PLAN_ORDER)[number];

const PLAN_META: Record<PlanTier, { label: string; badge: string; dot: string }> = {
  free:         { label: "Free",       badge: "bg-muted text-muted-foreground border-border",                                          dot: "bg-gray-400" },
  starter:      { label: "Starter",    badge: "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-300",       dot: "bg-blue-500" },
  professional: { label: "Pro",        badge: "bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-900/30 dark:text-purple-300", dot: "bg-purple-500" },
  enterprise:   { label: "Enterprise", badge: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300",  dot: "bg-amber-500" },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso?: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
  toast({ kind: "success", message: "Copied to clipboard" });
}

// ── ScopeExplorerPage ─────────────────────────────────────────────────────────

export function ScopeExplorerPage(): JSX.Element {
  const qc = useQueryClient();

  // UI state
  const [search, setSearch]               = useState("");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set(["goals", "agents"]));
  const [createOpen, setCreateOpen]       = useState(false);
  const [newKeyName, setNewKeyName]       = useState("");
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set());
  const [createdKey, setCreatedKey]       = useState<{ raw_key: string; key_id: string } | null>(null);
  const [showRaw, setShowRaw]             = useState(false);
  const [revokeId, setRevokeId]           = useState<string | null>(null);

  // Queries
  const { data: tenant, isLoading: tenantLoading, isError: tenantError, refetch } = useQuery({
    queryKey: ["tenant-me"],
    queryFn: () => tenantsApi.me(),
  });
  const { data: keys = [], isLoading: keysLoading } = useQuery({
    queryKey: ["tenant-keys"],
    queryFn: () => tenantsApi.listKeys(),
  });

  // Mutations
  const createMutation = useMutation({
    mutationFn: () => tenantsApi.createKey(newKeyName.trim(), selectedScopes.size > 0 ? [...selectedScopes] : undefined),
    onSuccess: (data) => {
      setCreatedKey(data);
      void qc.invalidateQueries({ queryKey: ["tenant-keys"] });
      toast({ kind: "success", message: "API key created" });
    },
    onError: () => toast({ kind: "error", message: "Failed to create API key" }),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => tenantsApi.revokeKey(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tenant-keys"] });
      toast({ kind: "success", message: "API key revoked" });
      setRevokeId(null);
    },
    onError: () => toast({ kind: "error", message: "Failed to revoke key" }),
  });

  const plan = (tenant?.plan ?? "free") as PlanTier;
  const meta = PLAN_META[plan] ?? PLAN_META.free;
  const grantedScopes = PLAN_SCOPES[plan] ?? PLAN_SCOPES.free;

  const toggleGroup = (r: string) => setExpandedGroups((p) => { const n = new Set(p); n.has(r) ? n.delete(r) : n.add(r); return n; });
  const toggleScope = (s: string) => setSelectedScopes((p) => { const n = new Set(p); n.has(s) ? n.delete(s) : n.add(s); return n; });

  const filteredGroups = SCOPE_GROUPS.map((g) => ({
    ...g,
    scopes: g.scopes.filter(
      (s) => !search || s.name.toLowerCase().includes(search.toLowerCase()) || s.description.toLowerCase().includes(search.toLowerCase())
    ),
  })).filter((g) => g.scopes.length > 0);

  const resetModal = () => { setCreateOpen(false); setNewKeyName(""); setSelectedScopes(new Set()); setCreatedKey(null); setShowRaw(false); };

  if (tenantLoading) return (
    <div className="p-6 max-w-5xl mx-auto space-y-4">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-12 w-64" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );

  if (tenantError) return (
    <div role="alert" className="flex flex-col items-center justify-center h-40 text-muted-foreground">
      <AlertCircle className="h-8 w-8 opacity-40 mb-2" />
      <p className="text-sm">Failed to load scope data</p>
      <button onClick={() => void refetch()} className="mt-2 text-xs text-primary hover:underline">Retry</button>
    </div>
  );

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-8">

      {/* ── Current plan banner ─────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl p-5 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className={`rounded-full p-2.5 border ${meta.badge}`}><Shield className="h-5 w-5" /></div>
          <div>
            <p className="text-xs text-muted-foreground uppercase tracking-wider font-medium">Current Plan</p>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-xl font-bold">{tenant?.name}</span>
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${meta.badge}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
                {meta.label}
              </span>
            </div>
          </div>
        </div>
        <div className="flex gap-6 text-center">
          <div><p className="text-2xl font-bold">{grantedScopes.length}</p><p className="text-xs text-muted-foreground">Granted scopes</p></div>
          <div><p className="text-2xl font-bold">{ALL_SCOPES.length}</p><p className="text-xs text-muted-foreground">Total scopes</p></div>
          <div><p className="text-2xl font-bold">{keys.length}</p><p className="text-xs text-muted-foreground">API keys</p></div>
        </div>
      </div>

      {/* ── Analytics stats ──────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { icon: Shield,   label: "Total Scopes",   value: String(ALL_SCOPES.length) },
          { icon: Key,      label: "Active Keys",     value: keysLoading ? "…" : String(keys.length) },
          { icon: BarChart3, label: "Last API Call",  value: "2h ago" },
          { icon: CheckCircle2, label: "Plan Tier",   value: meta.label },
        ].map(({ icon: Icon, label, value }) => (
          <div key={label} className="bg-card border border-border rounded-xl p-4 flex items-start gap-3">
            <div className="rounded-lg bg-muted p-2 mt-0.5"><Icon className="h-4 w-4 text-muted-foreground" /></div>
            <div><p className="text-lg font-bold leading-tight">{value}</p><p className="text-xs text-muted-foreground">{label}</p></div>
          </div>
        ))}
      </div>

      {/* ── Plan upgrade path ─────────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border"><h2 className="font-semibold text-sm">Plan Scope Progression</h2></div>
        <div className="p-4 grid grid-cols-2 md:grid-cols-4 gap-3">
          {PLAN_ORDER.map((tier) => {
            const m = PLAN_META[tier];
            const scopes = PLAN_SCOPES[tier];
            const isCurrent = tier === plan;
            return (
              <div key={tier} className={`rounded-lg border-2 p-3 transition-all ${isCurrent ? `border-current ${m.badge}` : "border-border bg-muted/20"}`}>
                <div className="flex items-center justify-between mb-2">
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${m.badge}`}>{m.label}</span>
                  {isCurrent && <span className="text-xs font-medium text-green-600 dark:text-green-400">Current</span>}
                </div>
                <p className="text-2xl font-bold">{scopes.length}</p>
                <p className="text-xs text-muted-foreground">scopes</p>
                <div className="flex flex-wrap gap-0.5 mt-2">
                  {ALL_SCOPES.map((s) => (
                    <span key={s} className={`w-2 h-2 rounded-full ${scopes.includes(s) ? m.dot : "bg-muted-foreground/20"}`} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Scope search ─────────────────────────────────────────────── */}
      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
        <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search scopes…"
          className="w-full pl-9 pr-4 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-ring" />
      </div>

      {/* ── Scope groups accordion ───────────────────────────────────── */}
      <div className="space-y-3" data-testid="scope-groups">
        {filteredGroups.map(({ resource, icon: Icon, scopes }) => {
          const isOpen = expandedGroups.has(resource);
          const grantedCount = scopes.filter((s) => grantedScopes.includes(s.name)).length;
          return (
            <div key={resource} className="bg-card border border-border rounded-xl overflow-hidden">
              <button onClick={() => toggleGroup(resource)} aria-expanded={isOpen}
                className="w-full flex items-center justify-between px-5 py-4 hover:bg-muted/40 transition-colors">
                <div className="flex items-center gap-3">
                  <Icon className="h-5 w-5 text-muted-foreground" />
                  <span className="font-semibold capitalize">{resource}</span>
                  <span className="text-xs text-muted-foreground">{grantedCount}/{scopes.length} granted</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex gap-0.5">
                    {scopes.map((s) => (<span key={s.name} className={`w-2 h-2 rounded-full ${grantedScopes.includes(s.name) ? "bg-green-500" : "bg-muted-foreground/25"}`} />))}
                  </div>
                  {isOpen ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
                </div>
              </button>
              {isOpen && (
                <div className="border-t border-border divide-y divide-border">
                  {scopes.map((scope) => {
                    const granted = grantedScopes.includes(scope.name);
                    return (
                      <div key={scope.name} className={`px-5 py-4 flex items-start justify-between gap-4 ${granted ? "bg-green-50/40 dark:bg-green-950/20" : ""}`}>
                        <div className="space-y-1 min-w-0">
                          <div className="flex items-center gap-2">
                            {granted ? <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 shrink-0" /> : <Lock className="h-4 w-4 text-muted-foreground/50 shrink-0" />}
                            <span className="font-mono text-sm font-medium">{scope.name}</span>
                          </div>
                          <p className="text-xs text-muted-foreground pl-6">{scope.description}</p>
                          {scope.examples.length > 0 && (
                            <div className="pl-6 flex flex-wrap gap-1">
                              {scope.examples.map((ex) => (<code key={ex} className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">{ex}</code>))}
                            </div>
                          )}
                        </div>
                        {!granted && (
                          <button onClick={() => toast({ kind: "info", message: `Upgrade your plan to unlock "${scope.name}"` })}
                            className="shrink-0 px-3 py-1.5 text-xs border border-border rounded-md hover:bg-muted transition-colors">
                            Unlock
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
        {filteredGroups.length === 0 && <p className="text-center py-10 text-sm text-muted-foreground">No scopes match "{search}".</p>}
      </div>

      {/* ── API Keys section ─────────────────────────────────────────── */}
      <div data-testid="api-keys-section">
        <div className="flex items-center justify-between mb-3">
          <div><h2 className="font-semibold">API Keys</h2><p className="text-xs text-muted-foreground mt-0.5">Keys inherit your plan scopes unless scoped down explicitly</p></div>
          <button data-testid="create-key-btn" onClick={() => setCreateOpen(true)}
            className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:opacity-90 transition-opacity">
            <Plus className="h-4 w-4" /> Create Key
          </button>
        </div>

        <div className="bg-card border border-border rounded-xl overflow-hidden" data-testid="key-table">
          {keysLoading ? (
            <div className="p-4 space-y-3">{[1,2,3].map((i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
          ) : keys.length === 0 ? (
            <div className="py-12 flex flex-col items-center gap-2 text-muted-foreground">
              <Key className="h-8 w-8 opacity-30" />
              <p className="text-sm">No API keys yet</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-muted/40">
                <tr>{["Name","Scopes","Created","Last Used",""].map((h) => <th key={h} className="text-left px-4 py-3 text-xs font-medium text-muted-foreground">{h}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-border">
                {keys.map((k: ApiKeyResponse) => (
                  <tr key={k.key_id} className="hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 font-medium">{k.name}</td>
                    <td className="px-4 py-3">
                      {k.scopes && k.scopes.length > 0
                        ? <span className="text-xs bg-muted px-1.5 py-0.5 rounded">{k.scopes.length} scopes</span>
                        : <span className="text-xs text-muted-foreground">All plan scopes</span>}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{fmtDate(k.created_at)}</td>
                    <td className="px-4 py-3 text-muted-foreground">{fmtDate(k.last_used_at)}</td>
                    <td className="px-4 py-3">
                      <button data-testid={`revoke-btn-${k.key_id}`} onClick={() => setRevokeId(k.key_id)}
                        className="p-1.5 text-muted-foreground hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors" aria-label="Revoke key">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* ── Create Key Modal ─────────────────────────────────────────── */}
      {createOpen && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Create API key">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={resetModal} aria-hidden="true" />
          <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-lg w-full p-6 space-y-5 animate-in zoom-in-95 duration-200 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-base flex items-center gap-2"><Key className="h-4 w-4" /> {createdKey ? "Key Created" : "Create API Key"}</h2>
              <button onClick={resetModal} className="text-muted-foreground hover:text-foreground" aria-label="Close"><X className="h-4 w-4" /></button>
            </div>

            {createdKey ? (
              /* ── Raw key display (show once) ── */
              <div data-testid="raw-key-display" className="space-y-4">
                <div className="rounded-lg border border-amber-200 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-800 p-3 text-xs text-amber-800 dark:text-amber-300">
                  Copy this key now — it will not be shown again.
                </div>
                <div className="flex items-center gap-2">
                  <code className="flex-1 bg-muted px-3 py-2 rounded-lg text-sm font-mono break-all">
                    {showRaw ? createdKey.raw_key : "•".repeat(Math.min(createdKey.raw_key.length, 48))}
                  </code>
                  <button onClick={() => setShowRaw((v) => !v)} className="p-2 border border-border rounded-lg hover:bg-muted transition-colors" aria-label="Toggle visibility">
                    {showRaw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                  <button onClick={() => void copyText(createdKey.raw_key)} className="p-2 border border-border rounded-lg hover:bg-muted transition-colors" aria-label="Copy key">
                    <Copy className="h-4 w-4" />
                  </button>
                </div>
                <button onClick={resetModal} className="w-full py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 transition-opacity">Done</button>
              </div>
            ) : (
              /* ── Create form ── */
              <div className="space-y-4">
                <div>
                  <label className="text-sm font-medium block mb-1.5">Key name <span className="text-red-500">*</span></label>
                  <input autoFocus value={newKeyName} onChange={(e) => setNewKeyName(e.target.value)} placeholder="e.g. CI pipeline key"
                    className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-ring" />
                </div>
                <div>
                  <p className="text-sm font-medium mb-1.5">Scopes <span className="text-xs text-muted-foreground">(leave empty to inherit all plan scopes)</span></p>
                  <div className="space-y-2 max-h-52 overflow-y-auto pr-1">
                    {SCOPE_GROUPS.map((g) => (
                      <div key={g.resource}>
                        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1 capitalize">{g.resource}</p>
                        <div className="grid grid-cols-2 gap-1">
                          {g.scopes.filter((s) => grantedScopes.includes(s.name)).map((s) => (
                            <label key={s.name} className="flex items-center gap-2 text-xs cursor-pointer hover:text-foreground">
                              <input type="checkbox" checked={selectedScopes.has(s.name)} onChange={() => toggleScope(s.name)}
                                className="rounded accent-primary" />
                              <span className="font-mono">{s.name}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <button disabled={!newKeyName.trim() || createMutation.isPending}
                  onClick={() => createMutation.mutate()}
                  className="w-full py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50">
                  {createMutation.isPending ? "Creating…" : "Create API Key"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Revoke confirm ───────────────────────────────────────────── */}
      <ConfirmModal
        open={revokeId !== null}
        title="Revoke API key?"
        description="This action is permanent. Any integrations using this key will stop working immediately."
        confirmLabel="Revoke"
        variant="danger"
        isLoading={revokeMutation.isPending}
        onConfirm={() => { if (revokeId) revokeMutation.mutate(revokeId); }}
        onCancel={() => setRevokeId(null)}
      />
    </div>
  );
}
