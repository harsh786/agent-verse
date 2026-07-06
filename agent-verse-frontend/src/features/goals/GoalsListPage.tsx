import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Search,
  XCircle,
  ArrowUpDown,
  CheckCircle2,
  Zap,
  Loader2,
} from "lucide-react";
import { goalsApi } from "@/lib/api/client";
import { MissionGoalComposer } from "@/features/goals/components/MissionGoalComposer";
import { useAuthStore } from "@/stores/auth";
import { Skeleton } from "@/components/ui/Skeleton";
import { Pagination } from "@/components/ui/Pagination";
import { toast } from "@/stores/toast";

const STATUS_OPTIONS = ["all", "planning", "executing", "complete", "failed", "waiting_human"];

type SortField = "created_at" | "status" | "goal";
type SortDir = "asc" | "desc";

// Fix 6: relative timestamp utility
function timeAgo(isoString: string): string {
  if (!isoString) return "";
  const diff = Date.now() - new Date(isoString).getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    complete:      "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    executing:     "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
    planning:      "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
    failed:        "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
    waiting_human: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[status] ?? "bg-muted text-muted-foreground"}`}>
      {status.replace("_", " ")}
    </span>
  );
}

export function GoalsListPage() {
  const tenantId = useAuthStore((s) => s.tenantId);

  const [pageSize, setPageSize] = useState(25);

  // Fix 1: bulk selection state
  const [selectedGoals, setSelectedGoals] = useState<Set<string>>(new Set());
  // Fix 5: per-row cancel loading state
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set());

  // ── URL-backed filter/search/sort/page state ──────────────────────────────
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = searchParams.get("status") ?? "all";
  const search = searchParams.get("q") ?? "";
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const sortField = (searchParams.get("sort") ?? "created_at") as SortField;
  const sortDir = (searchParams.get("dir") ?? "desc") as SortDir;

  const updateParams = (updates: Record<string, string | null>) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(updates).forEach(([k, v]) => {
        if (v === null || v === "" || v === "all") next.delete(k);
        else next.set(k, v);
      });
      return next;
    });
  };

  // Fix 2: sort handler
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      updateParams({ sort: field, dir: sortDir === "asc" ? "desc" : "asc" });
    } else {
      updateParams({ sort: field, dir: "asc" });
    }
  };

  useEffect(() => {
    document.title = `Goals${filter !== "all" ? ` · ${filter}` : ""} — AgentVerse`;
    return () => { document.title = "AgentVerse"; };
  }, [filter]);

  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["goals", tenantId],
    queryFn: () => goalsApi.list(),
    refetchInterval: 5_000,
  });

  const cancel = useMutation({
    mutationFn: (id: string) => goalsApi.cancel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goals", tenantId] }),
  });

  // Fix 4: status counts across ALL goals (unfiltered)
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = { all: data?.goals?.length ?? 0 };
    (data?.goals ?? []).forEach((g) => {
      counts[g.status] = (counts[g.status] ?? 0) + 1;
    });
    return counts;
  }, [data]);

  const filteredGoals = useMemo(
    () =>
      (data?.goals ?? []).filter((g) => {
        const matchStatus = filter === "all" || g.status === filter;
        const matchSearch = !search || g.goal.toLowerCase().includes(search.toLowerCase());
        return matchStatus && matchSearch;
      }),
    [data, filter, search]
  );

  // Fix 2: sorted goals
  const sortedGoals = useMemo(() => {
    return [...filteredGoals].sort((a, b) => {
      let cmp = 0;
      if (sortField === "created_at")
        cmp = (a.created_at ?? "") < (b.created_at ?? "") ? -1 : 1;
      else if (sortField === "status") cmp = a.status.localeCompare(b.status);
      else if (sortField === "goal") cmp = a.goal.localeCompare(b.goal);
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [filteredGoals, sortField, sortDir]);

  const paginatedGoals = sortedGoals.slice((page - 1) * pageSize, page * pageSize);

  // Fix 1: bulk selection handlers
  const toggleSelect = (id: string) =>
    setSelectedGoals((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const selectAll = () => {
    if (selectedGoals.size === paginatedGoals.length) setSelectedGoals(new Set());
    else setSelectedGoals(new Set(paginatedGoals.map((g) => g.id)));
  };

  const bulkCancel = async () => {
    const results = await Promise.allSettled(
      [...selectedGoals]
        .filter((id) => {
          const g = filteredGoals.find((g) => g.id === id);
          return g && ["executing", "planning"].includes(g.status);
        })
        .map((id) => goalsApi.cancel(id))
    );
    qc.invalidateQueries({ queryKey: ["goals", tenantId] });
    setSelectedGoals(new Set());
    const done = results.filter((r) => r.status === "fulfilled").length;
    toast({ kind: "success", message: `${done} goal(s) cancelled` });
  };

  // Fix 5: per-row cancel with visual feedback
  const handleCancel = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setCancellingIds((prev) => new Set([...prev, id]));
    try {
      await cancel.mutateAsync(id);
      toast({ kind: "success", message: "Goal cancelled" });
    } catch {
      toast({ kind: "error", message: "Failed to cancel goal" });
    } finally {
      setCancellingIds((prev) => {
        const n = new Set(prev);
        n.delete(id);
        return n;
      });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Goals</h1>
          <p className="text-muted-foreground text-sm mt-1">Submit and track autonomous agent goals</p>
        </div>
      </div>

      {/* Mission Goal Composer */}
      <MissionGoalComposer />

      {/* Fix 4: Filter pills with status count badges */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 flex-1 min-w-48">
          <Search className="h-4 w-4 text-muted-foreground flex-shrink-0" aria-hidden="true" />
          <input
            type="search"
            value={search}
            onChange={(e) => updateParams({ q: e.target.value, page: null })}
            placeholder="Search goals…"
            className="flex-1 text-sm bg-transparent outline-none placeholder:text-muted-foreground"
            aria-label="Search goals"
          />
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {STATUS_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => updateParams({ status: s, page: null })}
              className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                filter === s
                  ? "bg-primary text-primary-foreground border-primary"
                  : "border-border hover:bg-accent"
              }`}
            >
              {s}
              {statusCounts[s] !== undefined && statusCounts[s] > 0 && (
                <span
                  className={`ml-1 px-1.5 rounded-full text-[10px] ${
                    filter === s ? "bg-primary-foreground/20" : "bg-muted"
                  }`}
                >
                  {statusCounts[s]}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Fix 1: Bulk action toolbar */}
      {selectedGoals.size > 0 && (
        <div className="flex items-center gap-3 p-3 bg-primary/5 border border-primary/20 rounded-lg">
          <span className="text-sm font-medium">{selectedGoals.size} selected</span>
          <button
            onClick={bulkCancel}
            className="px-3 py-1.5 text-xs bg-orange-100 text-orange-800 rounded-lg hover:bg-orange-200"
          >
            Cancel All
          </button>
          <button
            onClick={() => setSelectedGoals(new Set())}
            className="text-xs text-muted-foreground hover:text-foreground ml-auto"
          >
            Clear selection
          </button>
        </div>
      )}

      {/* Goals table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isError ? (
          <div className="flex items-center justify-center h-64 text-risk-amber">
            <p>Failed to load goals. {error instanceof Error ? error.message : 'Please try again.'}</p>
          </div>
        ) : isLoading ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-3 w-8" />
                <th className="text-left px-4 py-3 font-medium text-muted-foreground">Goal</th>
                <th className="text-left px-4 py-3 font-medium text-muted-foreground w-32">Status</th>
                <th className="text-right px-4 py-3 font-medium text-muted-foreground w-24">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  <td className="px-4 py-3"><Skeleton className="h-4 w-4" /></td>
                  <td className="px-4 py-3">
                    <Skeleton className="h-4 w-3/4 mb-1" />
                    <Skeleton className="h-3 w-1/3" />
                  </td>
                  <td className="px-4 py-3"><Skeleton className="h-4 w-20" /></td>
                  <td className="px-4 py-3 text-right"><Skeleton className="h-4 w-6 ml-auto" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : filteredGoals.length === 0 ? (
          /* Fix 3: Rich empty states per context */
          <div className="px-5 py-16 text-center">
            {filter !== "all" ? (
              <>
                <CheckCircle2 className="h-12 w-12 mx-auto mb-3 text-green-500 opacity-50" />
                <p className="text-sm font-medium">No {filter} goals</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {filter === "failed"
                    ? "All goals running smoothly!"
                    : filter === "executing"
                    ? "No goals currently executing"
                    : `No goals with status: ${filter}`}
                </p>
                <button
                  onClick={() => updateParams({ status: null, page: null })}
                  className="mt-3 text-xs text-primary hover:underline"
                >
                  Show all goals
                </button>
              </>
            ) : data?.goals?.length === 0 ? (
              <>
                <Zap className="h-12 w-12 mx-auto mb-3 text-primary opacity-30" />
                <p className="text-sm font-medium">No goals yet</p>
                <p className="text-xs text-muted-foreground mt-1">Submit your first goal to get started</p>
              </>
            ) : (
              <>
                <Search className="h-12 w-12 mx-auto mb-3 opacity-20" />
                <p className="text-sm font-medium">No goals match your search</p>
                <button
                  onClick={() => updateParams({ q: null, page: null })}
                  className="mt-3 text-xs text-primary hover:underline"
                >
                  Clear search
                </button>
              </>
            )}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                {/* Fix 1: checkbox select-all header */}
                <th className="px-4 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={paginatedGoals.length > 0 && selectedGoals.size === paginatedGoals.length}
                    onChange={selectAll}
                    className="accent-primary"
                    aria-label="Select all goals on page"
                  />
                </th>
                {/* Fix 2: sortable Goal header */}
                <th
                  onClick={() => handleSort("goal")}
                  className="text-left px-4 py-3 font-medium text-muted-foreground cursor-pointer hover:text-foreground select-none"
                >
                  Goal{" "}
                  {sortField === "goal" ? (
                    sortDir === "asc" ? "↑" : "↓"
                  ) : (
                    <ArrowUpDown className="inline h-3 w-3 opacity-40" />
                  )}
                </th>
                {/* Fix 2: sortable Status header */}
                <th
                  onClick={() => handleSort("status")}
                  className="text-left px-4 py-3 font-medium text-muted-foreground w-32 cursor-pointer hover:text-foreground select-none"
                >
                  Status{" "}
                  {sortField === "status" ? (
                    sortDir === "asc" ? "↑" : "↓"
                  ) : (
                    <ArrowUpDown className="inline h-3 w-3 opacity-40" />
                  )}
                </th>
                <th className="text-right px-4 py-3 font-medium text-muted-foreground w-24">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {paginatedGoals.map((goal) => (
                <tr
                  key={goal.id}
                  onClick={() => navigate(`/goals/${goal.id}`)}
                  className={`hover:bg-accent/50 cursor-pointer transition-colors ${
                    selectedGoals.has(goal.id) ? "bg-primary/5" : ""
                  }`}
                >
                  {/* Fix 1: per-row checkbox */}
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedGoals.has(goal.id)}
                      onChange={() => toggleSelect(goal.id)}
                      className="accent-primary"
                      aria-label={`Select goal ${goal.id}`}
                    />
                  </td>
                   {/* Goal text + metadata */}
                   <td className="px-4 py-3">
                     <p className="font-medium truncate max-w-lg">{goal.goal}</p>
                     <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                       <span className="text-xs text-muted-foreground">
                         {goal.created_at ? timeAgo(goal.created_at) : <span className="font-mono">{goal.id}</span>}
                         {" · "}
                         {goal.event_count ?? 0} events
                       </span>
                       {/* Agent badge */}
                       {(goal as any).agent_id && (
                         <span className="inline-flex items-center gap-1 text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded-full font-medium">
                           🤖 {(goal as any).agent_name ?? (goal as any).agent_id?.slice(0, 8)}
                         </span>
                       )}
                       {/* Iteration count */}
                       {(goal as any).iterations > 0 && (
                         <span className="text-[10px] text-muted-foreground font-mono">
                           {(goal as any).iterations} iters
                         </span>
                       )}
                     </div>
                   </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={goal.status} />
                  </td>
                  {/* Fix 5: per-row cancel with spinner */}
                  <td className="px-4 py-3 text-right">
                    {["executing", "planning"].includes(goal.status) && (
                      <button
                        onClick={(e) => handleCancel(goal.id, e)}
                        disabled={cancellingIds.has(goal.id)}
                        className="p-1.5 hover:text-destructive transition-colors disabled:opacity-50"
                        aria-label="Cancel goal"
                      >
                        {cancellingIds.has(goal.id) ? (
                          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                        ) : (
                          <XCircle className="h-4 w-4" aria-hidden="true" />
                        )}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {!isLoading && filteredGoals.length > 0 && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={filteredGoals.length}
          onPageChange={(p) => updateParams({ page: String(p) })}
          onPageSizeChange={(s) => {
            setPageSize(s);
            updateParams({ page: null });
          }}
        />
      )}
    </div>
  );
}
