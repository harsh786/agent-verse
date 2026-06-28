/**
 * GoalDiffPage — compare two goal execution runs side by side.
 * Uses a simple line-level diff to highlight changes in steps/tools/outputs.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { goalsApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { GitCompare, Plus, Minus } from "lucide-react";

interface DiffLine {
  type: "added" | "removed" | "unchanged";
  content: string;
}

function computeDiff(a: string, b: string): DiffLine[] {
  // Simple line-level diff without external package to avoid import issues
  const aLines = a.split("\n");
  const bLines = b.split("\n");
  const result: DiffLine[] = [];
  const maxLen = Math.max(aLines.length, bLines.length);
  for (let i = 0; i < maxLen; i++) {
    if (i >= aLines.length) {
      result.push({ type: "added", content: bLines[i] });
    } else if (i >= bLines.length) {
      result.push({ type: "removed", content: aLines[i] });
    } else if (aLines[i] === bLines[i]) {
      result.push({ type: "unchanged", content: aLines[i] });
    } else {
      result.push({ type: "removed", content: aLines[i] });
      result.push({ type: "added", content: bLines[i] });
    }
  }
  return result;
}

function goalToText(goal: unknown): string {
  if (!goal) return "";
  const g = goal as Record<string, unknown>;
  const steps = (g.steps as unknown[]) ?? [];
  return [
    `Goal: ${g.goal}`,
    `Status: ${g.status}`,
    `Iterations: ${g.iterations ?? 0}`,
    `Cost: $${((g.cost_usd as number) ?? 0).toFixed(4)}`,
    "",
    "Steps:",
    ...steps.map((s: unknown, i: number) => {
      const step = s as Record<string, unknown>;
      return `  ${i + 1}. [${step.status}] ${step.description}\n     Output: ${String(step.output ?? "").slice(0, 200)}`;
    }),
  ].join("\n");
}

export function GoalDiffPage() {
  const [goalIdA, setGoalIdA] = useState("");
  const [goalIdB, setGoalIdB] = useState("");
  const [compare, setCompare] = useState(false);

  const { data: goalA, isLoading: loadingA } = useQuery({
    queryKey: ["goal", goalIdA],
    queryFn: () => goalsApi.get(goalIdA),
    enabled: compare && !!goalIdA,
  });

  const { data: goalB, isLoading: loadingB } = useQuery({
    queryKey: ["goal", goalIdB],
    queryFn: () => goalsApi.get(goalIdB),
    enabled: compare && !!goalIdB,
  });

  const diffLines = compare && goalA && goalB
    ? computeDiff(goalToText(goalA), goalToText(goalB))
    : [];

  const addedCount = diffLines.filter((l) => l.type === "added").length;
  const removedCount = diffLines.filter((l) => l.type === "removed").length;

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <GitCompare className="h-6 w-6 text-primary" aria-hidden="true" />
          Execution Diff
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Compare two goal runs side by side to understand regressions or improvements
        </p>
      </div>

      {/* Goal selector */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1.5" htmlFor="goal-a-id">Goal A (baseline)</label>
          <input
            id="goal-a-id"
            value={goalIdA}
            onChange={(e) => setGoalIdA(e.target.value)}
            placeholder="Paste goal ID…"
            className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1.5" htmlFor="goal-b-id">Goal B (comparison)</label>
          <input
            id="goal-b-id"
            value={goalIdB}
            onChange={(e) => setGoalIdB(e.target.value)}
            placeholder="Paste goal ID…"
            className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
      </div>

      <button
        onClick={() => setCompare(true)}
        disabled={!goalIdA || !goalIdB}
        className="px-6 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        Compare
      </button>

      {/* Diff result */}
      {compare && (loadingA || loadingB) && (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-5 w-full" />)}
        </div>
      )}

      {compare && goalA && goalB && (
        <>
          {/* Summary */}
          <div className="flex items-center gap-4 text-sm">
            <div className="flex items-center gap-1 text-green-600 dark:text-green-400">
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              {addedCount} additions
            </div>
            <div className="flex items-center gap-1 text-red-500 dark:text-red-400">
              <Minus className="h-3.5 w-3.5" aria-hidden="true" />
              {removedCount} removals
            </div>
            <StatusBadge status={goalA.status ?? "unknown"} size="sm" />
            <span className="text-muted-foreground">vs</span>
            <StatusBadge status={goalB.status ?? "unknown"} size="sm" />
          </div>

          {/* Diff viewer */}
          <div className="border border-border rounded-xl overflow-hidden font-mono text-xs">
            <div className="bg-muted/50 px-4 py-2 text-xs font-medium text-muted-foreground border-b border-border">
              Execution comparison
            </div>
            <div className="overflow-auto max-h-[500px]">
              {diffLines.map((line, i) => (
                <div
                  key={i}
                  className={`flex items-start px-4 py-0.5 ${
                    line.type === "added"
                      ? "bg-green-50 dark:bg-green-950/30 text-green-800 dark:text-green-300"
                      : line.type === "removed"
                      ? "bg-red-50 dark:bg-red-950/30 text-red-800 dark:text-red-300"
                      : "text-muted-foreground"
                  }`}
                >
                  <span className="w-4 shrink-0 select-none opacity-60">
                    {line.type === "added" ? "+" : line.type === "removed" ? "-" : " "}
                  </span>
                  <span className="whitespace-pre-wrap break-all">{line.content}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
