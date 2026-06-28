import { useState } from "react";
import type { JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, CheckCircle2, Lock, Target, Bot, BookOpen, Plug, Shield, BarChart3, AlertCircle } from "lucide-react";
import { tenantsApi } from "@/lib/api/client";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { toast } from "@/stores/toast";

interface ScopeDefinition {
  name: string;
  description: string;
  examples: string[];
}

interface ScopeGroup {
  resource: string;
  icon: React.ElementType;
  scopes: ScopeDefinition[];
}

const SCOPE_GROUPS: ScopeGroup[] = [
  {
    resource: "goals",
    icon: Target,
    scopes: [
      { name: "goals:read", description: "List and read goals and their status", examples: ["GET /goals", "GET /goals/:id"] },
      { name: "goals:write", description: "Submit and create new goals", examples: ["POST /goals"] },
      { name: "goals:cancel", description: "Cancel running goals mid-execution", examples: ["POST /goals/:id/cancel"] },
      { name: "goals:batch", description: "Submit goals in bulk batches", examples: ["POST /goals/batch"] },
    ],
  },
  {
    resource: "agents",
    icon: Bot,
    scopes: [
      { name: "agents:read", description: "List and view agent configurations", examples: ["GET /agents", "GET /agents/:id"] },
      { name: "agents:write", description: "Create and update agent configurations", examples: ["POST /agents", "PUT /agents/:id"] },
      { name: "agents:delete", description: "Permanently delete agents", examples: ["DELETE /agents/:id"] },
      { name: "agents:snapshot", description: "Take versioned snapshots of agent configs", examples: ["POST /agents/:id/snapshot"] },
    ],
  },
  {
    resource: "knowledge",
    icon: BookOpen,
    scopes: [
      { name: "knowledge:read", description: "Search and read knowledge collections", examples: ["GET /knowledge/collections", "GET /knowledge/search"] },
      { name: "knowledge:write", description: "Create collections and ingest documents", examples: ["POST /knowledge/collections", "POST /knowledge/ingest"] },
      { name: "knowledge:delete", description: "Delete knowledge collections", examples: ["DELETE /knowledge/collections/:id"] },
    ],
  },
  {
    resource: "connectors",
    icon: Plug,
    scopes: [
      { name: "connectors:read", description: "List and view registered connectors", examples: ["GET /connectors"] },
      { name: "connectors:write", description: "Register new MCP connectors", examples: ["POST /connectors"] },
      { name: "connectors:delete", description: "Unregister connectors", examples: ["DELETE /connectors/:id"] },
    ],
  },
  {
    resource: "governance",
    icon: Shield,
    scopes: [
      { name: "governance:read", description: "View policies and approvals", examples: ["GET /governance/policies"] },
      { name: "governance:write", description: "Create and manage governance policies", examples: ["POST /governance/policies"] },
      { name: "governance:approve", description: "Approve or reject HITL requests", examples: ["POST /governance/approvals/:id/approve"] },
    ],
  },
  {
    resource: "analytics",
    icon: BarChart3,
    scopes: [
      { name: "analytics:read", description: "View cost, eval, and performance metrics", examples: ["GET /analytics/costs", "GET /analytics/goals"] },
      { name: "analytics:export", description: "Export analytics data and training sets", examples: ["POST /intelligence/export-training-data"] },
    ],
  },
];

// Scopes granted to different plan levels (approximate)
const PLAN_SCOPES: Record<string, string[]> = {
  free: ["goals:read", "agents:read", "knowledge:read"],
  starter: ["goals:read", "goals:write", "agents:read", "agents:write", "knowledge:read", "knowledge:write", "connectors:read"],
  professional: [
    "goals:read", "goals:write", "goals:cancel", "goals:batch",
    "agents:read", "agents:write", "agents:delete", "agents:snapshot",
    "knowledge:read", "knowledge:write", "knowledge:delete",
    "connectors:read", "connectors:write", "connectors:delete",
    "governance:read", "analytics:read", "analytics:export",
  ],
  enterprise: SCOPE_GROUPS.flatMap((g) => g.scopes.map((s) => s.name)),
};

export function ScopeExplorerPage(): JSX.Element {
  const [search, setSearch] = useState("");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set(["goals", "agents"]));

  const { data: tenant, isLoading, isError, refetch } = useQuery({
    queryKey: ["tenant-me"],
    queryFn: () => tenantsApi.me(),
  });

  const grantedScopes = PLAN_SCOPES[tenant?.plan ?? "free"] ?? PLAN_SCOPES.free;

  const toggleGroup = (resource: string): void => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(resource)) next.delete(resource);
      else next.add(resource);
      return next;
    });
  };

  const handleRequestAccess = (scope: string): void => {
    toast({ kind: "info", message: `Access request for "${scope}" submitted — your admin will be notified.` });
  };

  const filteredGroups = SCOPE_GROUPS.map((g) => ({
    ...g,
    scopes: g.scopes.filter(
      (s) =>
        !search ||
        s.name.toLowerCase().includes(search.toLowerCase()) ||
        s.description.toLowerCase().includes(search.toLowerCase())
    ),
  })).filter((g) => g.scopes.length > 0);

  if (isLoading) return <LoadingSpinner />;

  if (isError) {
    return (
      <div role="alert" className="flex flex-col items-center justify-center h-32 text-muted-foreground">
        <AlertCircle className="h-8 w-8 opacity-40 mb-2" />
        <p className="text-sm">Failed to load scope data</p>
        <button onClick={() => void refetch()} className="mt-2 text-xs text-primary hover:underline">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">Scope Explorer</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Visual map of all API scopes.{" "}
          <span className="text-green-600 dark:text-green-400 font-medium">Green</span> = granted to your{" "}
          <span className="font-medium">{tenant?.plan ?? "free"}</span> plan.
        </p>
      </div>

      {/* Search */}
      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search scopes…"
          className="w-full pl-9 pr-4 py-2 text-sm border border-input rounded-lg bg-background"
        />
      </div>

      {/* Scope groups */}
      <div className="space-y-3">
        {filteredGroups.map(({ resource, icon: Icon, scopes }) => {
          const isOpen = expandedGroups.has(resource);
          const grantedCount = scopes.filter((s) => grantedScopes.includes(s.name)).length;

          return (
            <div key={resource} className="bg-card border border-border rounded-xl overflow-hidden">
              {/* Group header */}
              <button
                onClick={() => toggleGroup(resource)}
                className="w-full flex items-center justify-between px-5 py-4 hover:bg-muted/40 transition-colors"
                aria-expanded={isOpen}
              >
                <div className="flex items-center gap-3">
                  <Icon className="h-5 w-5 text-muted-foreground" />
                  <span className="font-semibold capitalize">{resource}</span>
                  <span className="text-xs text-muted-foreground">
                    {grantedCount}/{scopes.length} granted
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex gap-0.5">
                    {scopes.map((s) => (
                      <span
                        key={s.name}
                        className={`w-2 h-2 rounded-full ${
                          grantedScopes.includes(s.name) ? "bg-green-500" : "bg-muted-foreground/30"
                        }`}
                      />
                    ))}
                  </div>
                  <span className="text-muted-foreground text-sm">{isOpen ? "▲" : "▼"}</span>
                </div>
              </button>

              {/* Scope list */}
              {isOpen && (
                <div className="border-t border-border divide-y divide-border">
                  {scopes.map((scope) => {
                    const granted = grantedScopes.includes(scope.name);
                    return (
                      <div
                        key={scope.name}
                        className={`px-5 py-4 flex items-start justify-between gap-4 ${
                          granted ? "bg-green-50/40 dark:bg-green-950/20" : ""
                        }`}
                      >
                        <div className="space-y-1 min-w-0">
                          <div className="flex items-center gap-2">
                            {granted ? (
                              <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 flex-shrink-0" />
                            ) : (
                              <Lock className="h-4 w-4 text-muted-foreground/60 flex-shrink-0" />
                            )}
                            <span className="font-mono text-sm font-medium">{scope.name}</span>
                          </div>
                          <p className="text-xs text-muted-foreground pl-6">{scope.description}</p>
                          {scope.examples.length > 0 && (
                            <div className="pl-6 flex flex-wrap gap-1">
                              {scope.examples.map((ex) => (
                                <code
                                  key={ex}
                                  className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono"
                                >
                                  {ex}
                                </code>
                              ))}
                            </div>
                          )}
                        </div>
                        {!granted && (
                          <button
                            onClick={() => handleRequestAccess(scope.name)}
                            className="flex-shrink-0 px-3 py-1.5 text-xs border border-border rounded-md hover:bg-muted transition-colors"
                          >
                            Request Access
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
      </div>

      {filteredGroups.length === 0 && (
        <div className="text-center py-12 text-muted-foreground text-sm">
          No scopes match "{search}".
        </div>
      )}
    </div>
  );
}
