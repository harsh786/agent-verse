/**
 * GhostRunPage — Submit a goal with multiple strategies simultaneously.
 * Runs the same goal with different agent configurations and compares results.
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { goalsApi } from "@/lib/api/client";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Ghost, Play, ExternalLink, Trophy } from "lucide-react";

interface GhostResult {
  strategy: string;
  agentId?: string;
  goalId: string;
  status: string;
  cost?: number;
  duration?: number;
}

export function GhostRunPage() {
  const navigate = useNavigate();
  const [goal, setGoal] = useState("");
  const [results, setResults] = useState<GhostResult[]>([]);

  const ghostRun = useMutation({
    mutationFn: async () => {
      // Run the same goal with 3 different modes in parallel
      const [single, highPrio, dryRun] = await Promise.allSettled([
        goalsApi.submit({ goal, workflow_mode: "single_agent" }),
        goalsApi.submit({ goal, workflow_mode: "single_agent", priority: "high" }),
        goalsApi.submit({ goal, dry_run: true }),
      ]);

      const strategies: GhostResult[] = [];

      if (single.status === "fulfilled") {
        const v = single.value;
        strategies.push({
          strategy: "Standard",
          goalId: v.goal_id ?? v.id,
          status: v.status ?? "planning",
        });
      }
      if (highPrio.status === "fulfilled") {
        const v = highPrio.value;
        strategies.push({
          strategy: "High Priority",
          goalId: v.goal_id ?? v.id,
          status: v.status ?? "planning",
        });
      }
      if (dryRun.status === "fulfilled") {
        const v = dryRun.value;
        strategies.push({
          strategy: "Dry Run",
          goalId: v.goal_id ?? v.id,
          status: v.status ?? "dry_run",
        });
      }

      return strategies;
    },
    onSuccess: (strategies) => setResults(strategies),
  });

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Ghost className="h-6 w-6 text-primary" aria-hidden="true" />
          Ghost Run — A/B Comparison
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Run the same goal with multiple strategies simultaneously. Compare which performs best.
        </p>
      </div>

      <div className="space-y-3">
        <label className="block text-sm font-medium" htmlFor="ghost-goal">Goal description</label>
        <textarea
          id="ghost-goal"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          rows={3}
          placeholder="Describe what you want to achieve…"
          className="w-full px-3 py-2.5 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
          disabled={ghostRun.isPending}
        />

        <div className="flex gap-3 text-xs text-muted-foreground bg-muted/30 rounded-lg p-3 border border-border">
          <Ghost className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <p className="font-medium text-foreground mb-1">How Ghost Run works</p>
            <p>Submits your goal with 3 strategies: Standard execution, High-priority execution, and a Dry Run preview. Track each in real-time and compare results.</p>
          </div>
        </div>

        <button
          onClick={() => ghostRun.mutate()}
          disabled={!goal.trim() || ghostRun.isPending}
          className="flex items-center gap-2 px-6 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          <Play className="h-4 w-4" aria-hidden="true" />
          {ghostRun.isPending ? "Launching strategies…" : "Launch Ghost Run"}
        </button>
      </div>

      {ghostRun.isPending && (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
        </div>
      )}

      {results.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Trophy className="h-4 w-4 text-amber-500" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{results.length} strategies launched</h2>
          </div>
          {results.map((r) => (
            <div key={r.goalId} className="flex items-center justify-between p-4 bg-card border border-border rounded-xl">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{r.strategy}</span>
                  <StatusBadge status={r.status} size="sm" />
                </div>
                <p className="text-xs text-muted-foreground font-mono">{r.goalId}</p>
              </div>
              <button
                onClick={() => navigate(`/goals/${r.goalId}`)}
                className="flex items-center gap-1.5 text-xs text-primary hover:underline"
                aria-label={`View ${r.strategy} goal`}
              >
                Track <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </button>
            </div>
          ))}
          <p className="text-xs text-muted-foreground">
            All three goals are now running. Navigate to each to compare execution, cost, and success.
          </p>
        </div>
      )}

      {ghostRun.isError && (
        <p role="alert" className="text-sm text-red-500">
          Failed to launch ghost run: {String(ghostRun.error)}
        </p>
      )}
    </div>
  );
}
