import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus, Search, XCircle } from "lucide-react";
import { goalsApi } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";

const STATUS_OPTIONS = ["all", "planning", "executing", "complete", "failed", "waiting_human"];

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    complete: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    executing: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
    planning: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
    failed: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
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
  const [goalText, setGoalText] = useState("");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["goals", tenantId],
    queryFn: () => goalsApi.list(),
    refetchInterval: 5_000,
  });

  const submit = useMutation({
    mutationFn: (goal: string) => goalsApi.submit({ goal, dry_run: dryRun }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["goals"] });
      navigate(`/goals/${res.goal_id}`);
    },
  });

  const cancel = useMutation({
    mutationFn: (id: string) => goalsApi.cancel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goals"] }),
  });

  const goals = (data?.goals ?? []).filter((g) => {
    const matchStatus = filter === "all" || g.status === filter;
    const matchSearch = !search || g.goal.toLowerCase().includes(search.toLowerCase());
    return matchStatus && matchSearch;
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Goals</h1>
          <p className="text-muted-foreground text-sm mt-1">Submit and track autonomous agent goals</p>
        </div>
      </div>

      {/* Submit form */}
      <div className="bg-card border border-border rounded-xl p-5">
        <h2 className="font-semibold text-sm mb-3">Submit a new goal</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (goalText.trim()) submit.mutate(goalText.trim());
          }}
          className="flex flex-col gap-3"
        >
          <textarea
            value={goalText}
            onChange={(e) => setGoalText(e.target.value)}
            placeholder="Describe the goal in natural language, e.g. 'Fix all JIRA bugs labelled prod-down and open a PR'"
            rows={3}
            className="w-full px-3 py-2 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
            aria-label="Goal text"
          />
          <div className="flex items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(e) => setDryRun(e.target.checked)}
                className="accent-primary"
              />
              Dry run (preview only)
            </label>
            <button
              type="submit"
              disabled={submit.isPending || !goalText.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              {submit.isPending ? "Submitting…" : dryRun ? "Dry run" : "Submit"}
            </button>
          </div>
          {submit.isError && (
            <p role="alert" className="text-xs text-red-600 dark:text-red-400">
              {String(submit.error)}
            </p>
          )}
        </form>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 flex-1 min-w-48">
          <Search className="h-4 w-4 text-muted-foreground flex-shrink-0" aria-hidden="true" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search goals…"
            className="flex-1 text-sm bg-transparent outline-none placeholder:text-muted-foreground"
            aria-label="Search goals"
          />
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {STATUS_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                filter === s
                  ? "bg-primary text-primary-foreground border-primary"
                  : "border-border hover:bg-accent"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Goals table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">Loading…</div>
        ) : goals.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">No goals found.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="text-left px-4 py-3 font-medium text-muted-foreground">Goal</th>
                <th className="text-left px-4 py-3 font-medium text-muted-foreground w-32">Status</th>
                <th className="text-right px-4 py-3 font-medium text-muted-foreground w-24">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {goals.map((goal) => (
                <tr
                  key={goal.id}
                  onClick={() => navigate(`/goals/${goal.id}`)}
                  className="hover:bg-accent/50 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    <p className="font-medium truncate max-w-lg">{goal.goal}</p>
                    <p className="text-xs text-muted-foreground font-mono mt-0.5">{goal.id}</p>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={goal.status} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    {["executing", "planning"].includes(goal.status) && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          cancel.mutate(goal.id);
                        }}
                        className="p-1.5 hover:text-destructive transition-colors"
                        aria-label="Cancel goal"
                      >
                        <XCircle className="h-4 w-4" aria-hidden="true" />
                      </button>
                    )}
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
