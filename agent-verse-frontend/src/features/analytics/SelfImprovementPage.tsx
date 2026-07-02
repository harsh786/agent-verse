import { useState } from "react";
import type { JSX } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle, AlertCircle, Clock, FlaskConical, RotateCcw } from "lucide-react";
import { selfImprovementApi } from "@/lib/api/client";
import type { Experiment, Suggestion } from "@/lib/api/client";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ThemedBarChart } from "@/components/charts";
import { toast } from "@/stores/toast";

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<Experiment["status"], string> = {
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  concluded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  pending: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
};

const CONFIDENCE_STYLES: Record<Suggestion["status"], string> = {
  pending: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  applied: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  rejected: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

// ── Experiment Detail ─────────────────────────────────────────────────────────

function ExperimentDetail({ experiment }: { experiment: Experiment }): JSX.Element {
  const controlKeys = Object.keys(experiment.control_config);
  const challengerKeys = Object.keys(experiment.challenger_config);
  const allKeys = Array.from(new Set([...controlKeys, ...challengerKeys]));

  const comparisonData = allKeys.map((key) => ({
    key,
    control: typeof experiment.control_config[key] === "number" ? (experiment.control_config[key] as number) : 0,
    challenger:
      typeof experiment.challenger_config[key] === "number"
        ? (experiment.challenger_config[key] as number)
        : 0,
  }));

  return (
    <div className="bg-muted/30 rounded-xl p-4 mt-3 space-y-3">
      <div className="flex items-center gap-4">
        {experiment.lift_pct !== null && (
          <div
            className={`px-3 py-1.5 rounded-lg text-sm font-bold ${
              experiment.lift_pct > 0 ? "text-green-600 bg-green-50 dark:bg-green-900/20" : "text-red-600 bg-red-50 dark:bg-red-900/20"
            }`}
          >
            {experiment.lift_pct > 0 ? "+" : ""}{experiment.lift_pct.toFixed(1)}% lift
          </div>
        )}
        <div className="text-xs text-muted-foreground">
          Started {new Date(experiment.started_at).toLocaleDateString()}
          {experiment.concluded_at && ` · Concluded ${new Date(experiment.concluded_at).toLocaleDateString()}`}
        </div>
      </div>

      {comparisonData.length > 0 && comparisonData.some((d) => d.control !== 0 || d.challenger !== 0) && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Control vs Challenger</p>
          <ThemedBarChart
            data={comparisonData}
            bars={[
              { key: "control", label: "Control", color: "#64748b" },
              { key: "challenger", label: "Challenger", color: "#3b82f6" },
            ]}
            xKey="key"
            height={150}
          />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <p className="font-medium text-muted-foreground mb-1">Control Config</p>
          <pre className="bg-background border border-border rounded px-2 py-1.5 overflow-auto max-h-24 text-xs">
            {JSON.stringify(experiment.control_config, null, 2)}
          </pre>
        </div>
        <div>
          <p className="font-medium text-muted-foreground mb-1">Challenger Config</p>
          <pre className="bg-background border border-border rounded px-2 py-1.5 overflow-auto max-h-24 text-xs">
            {JSON.stringify(experiment.challenger_config, null, 2)}
          </pre>
        </div>
      </div>
    </div>
  );
}

// ── SelfImprovementPage ───────────────────────────────────────────────────────

type PageTab = "experiments" | "suggestions" | "history";

export function SelfImprovementPage(): JSX.Element {
  const qc = useQueryClient();
  const [tab, setTab] = useState<PageTab>("experiments");
  const [expandedExp, setExpandedExp] = useState<string | null>(null);

  const experimentsQuery = useQuery({
    queryKey: ["experiments"],
    queryFn: () => selfImprovementApi.listExperiments(),
    enabled: tab === "experiments" || tab === "history",
  });

  const suggestionsQuery = useQuery({
    queryKey: ["suggestions"],
    queryFn: () => selfImprovementApi.getSuggestions(),
    enabled: tab === "suggestions",
  });

  const applyMutation = useMutation({
    mutationFn: (id: string) => selfImprovementApi.applySuggestion(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Suggestion applied" });
      qc.invalidateQueries({ queryKey: ["suggestions"] });
    },
    onError: (e) => toast({ kind: "error", message: `Failed: apply suggestion. ${String(e)}` }),
  });

  const rejectMutation = useMutation({
    mutationFn: (id: string) => selfImprovementApi.rejectSuggestion(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Suggestion rejected" });
      qc.invalidateQueries({ queryKey: ["suggestions"] });
    },
    onError: (e) => toast({ kind: "error", message: `Failed: reject suggestion. ${String(e)}` }),
  });

  const rollbackMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      selfImprovementApi.rollbackExperiment(id, reason),
    onSuccess: () => {
      toast({ kind: "success", message: "Rolled back — agent restored to control configuration" });
      qc.invalidateQueries({ queryKey: ["experiments"] });
    },
    onError: (e) => toast({ kind: "error", message: `Rollback failed: ${String(e)}` }),
  });

  const experiments: Experiment[] = experimentsQuery.data ?? [];
  const suggestions: Suggestion[] = suggestionsQuery.data ?? [];

  // History = concluded experiments (for timeline)
  const history = experiments.filter((e) => e.status === "concluded");

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">Self-Improvement</h1>
        <p className="text-sm text-muted-foreground mt-1">
          A/B experiments, AI-generated optimizations, and performance suggestions.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {(["experiments", "suggestions", "history"] as PageTab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors capitalize ${
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t}
            {t === "suggestions" && suggestions.filter((s) => s.status === "pending").length > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 text-xs rounded-full bg-orange-500 text-white">
                {suggestions.filter((s) => s.status === "pending").length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Experiments tab */}
      {tab === "experiments" && (
        <div className="space-y-3">
          {experimentsQuery.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}
            </div>
          ) : experimentsQuery.isError ? (
            <div role="alert" className="flex flex-col items-center justify-center h-32 text-muted-foreground">
              <AlertCircle className="h-8 w-8 opacity-40 mb-2" />
              <p className="text-sm">Failed to load experiments</p>
              <button onClick={() => void experimentsQuery.refetch()} className="mt-2 text-xs text-primary hover:underline">
                Retry
              </button>
            </div>
          ) : experiments.length === 0 ? (
            <EmptyState
              title="No experiments yet"
              description="A/B experiments are created automatically as the system detects optimization opportunities."
            />
          ) : (
            experiments.map((exp) => (
              <div key={exp.id} className="experiment-entering bg-card border border-border rounded-xl">
                <button
                  onClick={() => setExpandedExp(expandedExp === exp.id ? null : exp.id)}
                  className="w-full text-left px-5 py-4 hover:bg-muted/30 transition-colors rounded-xl"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{exp.name}</span>
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_STYLES[exp.status]}`}>
                          {exp.status}
                        </span>
                        {exp.lift_pct !== null && (
                          <span
                            className={`text-xs font-bold ${
                              exp.lift_pct > 0 ? "text-green-600" : "text-red-600"
                            }`}
                          >
                            {exp.lift_pct > 0 ? "+" : ""}{exp.lift_pct.toFixed(1)}%
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">Agent: {exp.agent_id.slice(0, 12)}…</p>
                    </div>
                    <FlaskConical className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  </div>
                </button>
                {expandedExp === exp.id && <div className="px-5 pb-5"><ExperimentDetail experiment={exp} /></div>}
                {/* Rollback available for concluded experiments */}
                {expandedExp === exp.id && exp.status === "concluded" && (
                  <div className="px-5 pb-4 border-t border-border pt-4 flex items-center justify-between">
                    <p className="text-xs text-muted-foreground">
                      {exp.lift_pct !== null && exp.lift_pct > 0
                        ? `Challenger improved by +${exp.lift_pct.toFixed(1)}% — rollback to restore control`
                        : "Rollback to restore the original agent configuration"}
                    </p>
                    <button
                      type="button"
                      onClick={() => rollbackMutation.mutate({ id: exp.id, reason: `Rolled back from UI. Lift: ${exp.lift_pct?.toFixed(1) ?? "N/A"}%.` })}
                      disabled={rollbackMutation.isPending}
                      aria-label={`Roll back experiment ${exp.name}`}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-orange-300 bg-orange-50 text-orange-700 hover:bg-orange-100 dark:border-orange-700/60 dark:bg-orange-950/30 dark:text-orange-300 disabled:opacity-50 transition-colors"
                    >
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                      {rollbackMutation.isPending ? "Rolling back…" : "Rollback"}
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Suggestions tab */}
      {tab === "suggestions" && (
        <div className="space-y-3">
          {suggestionsQuery.isLoading ? (
            <LoadingSpinner />
          ) : suggestions.length === 0 ? (
            <EmptyState
              title="No suggestions"
              description="The optimizer will generate suggestions as it analyzes agent performance."
            />
          ) : (
            suggestions.map((s) => (
              <div key={s.id} className="bg-card border border-border rounded-xl px-5 py-4 flex items-start justify-between gap-4">
                <div className="space-y-1.5 min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{s.type.replace(/_/g, " ")}</span>
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${CONFIDENCE_STYLES[s.status]}`}>
                      {s.status}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {(s.confidence * 100).toFixed(0)}% confidence
                    </span>
                  </div>
                  <p className="text-sm text-muted-foreground">{s.description}</p>
                  {s.agent_id && (
                    <p className="text-xs text-muted-foreground">
                      Agent: <span className="font-mono">{s.agent_id.slice(0, 12)}…</span>
                    </p>
                  )}
                </div>
                {s.status === "pending" && (
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={() => applyMutation.mutate(s.id)}
                      disabled={applyMutation.isPending}
                      className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white rounded-md text-xs hover:bg-green-700 disabled:opacity-50"
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" /> Apply
                    </button>
                    <button
                      onClick={() => rejectMutation.mutate(s.id)}
                      disabled={rejectMutation.isPending}
                      className="flex items-center gap-1 px-3 py-1.5 border border-border rounded-md text-xs hover:bg-muted disabled:opacity-50"
                    >
                      <XCircle className="h-3.5 w-3.5" /> Reject
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* History tab */}
      {tab === "history" && (
        <div className="space-y-4">
          <h2 className="text-sm font-semibold">Optimization Timeline</h2>
          {experimentsQuery.isLoading ? (
            <LoadingSpinner />
          ) : history.length === 0 ? (
            <EmptyState
              title="No optimization history"
              description="Concluded experiments and applied optimizations will appear here."
            />
          ) : (
            <div className="relative pl-6 border-l border-border space-y-4">
              {history.map((exp) => (
                <div key={exp.id} className="relative">
                  <div className="absolute -left-8 top-1 w-4 h-4 rounded-full border-2 border-primary bg-background flex items-center justify-center">
                    <div className="w-2 h-2 rounded-full bg-primary" />
                  </div>
                  <div className="bg-card border border-border rounded-lg px-4 py-3 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{exp.name}</span>
                      {exp.lift_pct !== null && (
                        <span className={`text-xs font-bold ${exp.lift_pct > 0 ? "text-green-600" : "text-red-600"}`}>
                          {exp.lift_pct > 0 ? "+" : ""}{exp.lift_pct.toFixed(1)}%
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {exp.concluded_at ? new Date(exp.concluded_at).toLocaleDateString() : "—"}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
