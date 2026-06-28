import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  RefreshCw,
  Loader2,
  ChevronDown,
  ChevronRight,
  Pause,
  Play,
  Dna,
  GitCompare,
  Ghost,
} from "lucide-react";
import { useState } from "react";
import { goalsApi, governanceApi } from "@/lib/api/client";
import { useGoalStream } from "@/lib/sse/useGoalStream";
import { useAuthStore } from "@/stores/auth";
import { ExecutionTimeline } from "@/components/execution/ExecutionTimeline";
import { ToolCallInspector } from "@/components/execution/ToolCallInspector";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    complete: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    executing: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
    planning: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
    failed: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
    waiting_human: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  };
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium ${colors[status] ?? "bg-muted text-muted-foreground"}`}>
      {status.replace("_", " ")}
    </span>
  );
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function formatValue(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (["string", "number", "boolean"].includes(typeof value)) return String(value);
  return JSON.stringify(value, null, 2);
}

function eventSummary(event: Record<string, unknown>) {
  const type = readString(event.type) ?? "event";
  const step = readString(event.step);
  const status = readString(event.status);
  const toolName = readString(event.tool_name) ?? readString(event.tool) ?? "Tool call";
  const serverId = readString(event.server_id);
  const success = typeof event.success === "boolean" ? event.success : undefined;
  const details: string[] = [];

  switch (type) {
    case "goal_started":
      return { label: "Goal started", status: status ?? "executing", details };
    case "plan_ready": {
      if (Array.isArray(event.steps) && event.steps.length > 0) {
        details.push("Steps:", ...event.steps.map((item, index) => `${index + 1}. ${formatValue(item) ?? "Step"}`));
      }
      return { label: "Plan ready", status: status ?? "complete", details };
    }
    case "step_started":
      return { label: step ?? "Step started", status: status ?? "executing", details };
    case "step_complete":
      return { label: step ?? "Step complete", status: status ?? "complete", details };
    case "tool_call_complete": {
      if (serverId) details.push(`Server: ${serverId}`);
      const output = formatValue(event.output);
      const error = formatValue(event.error);
      if (output) details.push("Output:", output);
      if (error) details.push("Error:", error);
      const succeeded = success !== false;
      return {
        label: `${toolName} ${succeeded ? "succeeded" : "failed"}`,
        status: succeeded ? "complete" : "failed",
        details,
      };
    }
    case "tool_call_failed": {
      if (serverId) details.push(`Server: ${serverId}`);
      const error = formatValue(event.error);
      if (error) details.push("Error:", error);
      return { label: `${toolName} failed`, status: "failed", details };
    }
    case "dry_run_preview":
      return { label: "Dry run preview", status: status ?? "complete", details };
    case "verification_done": {
      details.push(`Success: ${success === true ? "yes" : "no"}`);
      const reason = formatValue(event.reason);
      if (reason) details.push(`Reason: ${reason}`);
      return {
        label: success === false ? "Verification failed" : "Verification passed",
        status: success === false ? "failed" : "complete",
        details,
      };
    }
    default:
      return { label: step ?? type.replace(/_/g, " "), status: status ?? "executing", details };
  }
}

function StepRow({ event }: { event: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const summary = eventSummary(event);
  const status = summary.status;
  const Icon = status === "complete" ? CheckCircle : status === "failed" ? XCircle : Loader2;
  const iconColor =
    status === "complete"
      ? "text-green-500"
      : status === "failed"
      ? "text-red-500"
      : "text-blue-500";

  return (
    <li className="border-b border-border last:border-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-accent/50 text-left"
      >
        <Icon className={`h-4 w-4 flex-shrink-0 ${iconColor} ${status === "executing" ? "animate-spin" : ""}`} />
        <span className="flex-1 text-sm font-medium">{summary.label}</span>
        {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && (
        <pre className="px-4 pb-3 text-xs overflow-x-auto whitespace-pre-wrap text-muted-foreground">
          {summary.details.length > 0 ? summary.details.join("\n") : JSON.stringify(event, null, 2)}
        </pre>
      )}
    </li>
  );
}

export function GoalDetailPage() {
  const { goalId } = useParams<{ goalId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const tenantId = useAuthStore((s) => s.tenantId);
  const [approvalNote, setApprovalNote] = useState("");
  const [activeTab, setActiveTab] = useState<"pipeline" | "events" | "eval">("pipeline");

  const { data: goal, isLoading } = useQuery({
    queryKey: ["goal", goalId],
    queryFn: () => goalsApi.get(goalId!),
    refetchInterval: 5_000,
    enabled: !!goalId,
  });

  const { events, connected } = useGoalStream(goalId ?? "");

  const cancel = useMutation({
    mutationFn: () => goalsApi.cancel(goalId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goal", goalId] }),
  });

  const pauseMutation = useMutation({
    mutationFn: () => goalsApi.pause(goalId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goal", goalId] });
      toast({ kind: "success", message: "Goal paused." });
    },
    onError: (e) => toast({ kind: "error", message: `Pause failed: ${e}` }),
  });

  const resumeMutation = useMutation({
    mutationFn: () => goalsApi.resume(goalId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goal", goalId] });
      toast({ kind: "success", message: "Goal resumed." });
    },
    onError: (e) => toast({ kind: "error", message: `Resume failed: ${e}` }),
  });

  // Event log (replay) — fetched when tab is active
  const { data: eventLog = [], isLoading: eventsLoading } = useQuery({
    queryKey: ["goal-events", goalId],
    queryFn: () => goalsApi.getEventLog(goalId!),
    enabled: !!goalId && activeTab === "events",
  });

  // Eval scorecard — fetched when tab is active and goal is terminal
  const { data: evaluation, isLoading: evalLoading } = useQuery({
    queryKey: ["goal-eval", goalId],
    queryFn: () => goalsApi.getEvaluation(goalId!),
    enabled:
      !!goalId &&
      activeTab === "eval" &&
      ["complete", "failed"].includes(goal?.status ?? ""),
  });

  // HITL: fetch pending approvals — enabled only while waiting_human
  const { data: approvals, isLoading: approvalsLoading } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    enabled: goal?.status === "waiting_human",
    refetchInterval: 3_000,
  });

  const pendingApproval = approvals?.find(
    (a) => a.goal_id === (goal?.goal_id ?? goal?.id) && a.status === "pending"
  );

  const approveMutation = useMutation({
    mutationFn: () => {
      if (!pendingApproval) throw new Error("No pending approval request found");
      const approverName = `user:${tenantId?.slice(0, 8) ?? "unknown"}`;
      return governanceApi.approve(pendingApproval.request_id, approverName, approvalNote);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goal", goalId] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () => {
      if (!pendingApproval) throw new Error("No pending approval request found");
      const approverName = `user:${tenantId?.slice(0, 8) ?? "unknown"}`;
      return governanceApi.reject(pendingApproval.request_id, approverName, approvalNote);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goal", goalId] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-40">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!goal) {
    return (
      <div className="text-center py-20 text-muted-foreground">
        Goal not found.{" "}
        <button onClick={() => navigate("/goals")} className="text-primary hover:underline">
          Back to goals
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <div>
        <button
          onClick={() => navigate("/goals")}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground mb-3 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> Back to goals
        </button>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold leading-snug">{goal.goal}</h1>
            <p className="text-xs text-muted-foreground font-mono mt-1">{goal.goal_id}</p>
          </div>
          <StatusBadge status={goal.status} />
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {["executing", "planning"].includes(goal.status) && (
          <button
            onClick={() => cancel.mutate()}
            disabled={cancel.isPending}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border border-destructive text-destructive rounded-md hover:bg-destructive/10 transition-colors disabled:opacity-50"
          >
            <XCircle className="h-4 w-4" /> Cancel
          </button>
        )}
        {goal.status === "executing" && (
          <button
            onClick={() => pauseMutation.mutate()}
            disabled={pauseMutation.isPending}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border border-yellow-300 text-yellow-700 rounded-md hover:bg-yellow-50 transition-colors disabled:opacity-50"
            aria-label="Pause goal"
          >
            <Pause className="h-4 w-4" /> Pause
          </button>
        )}
        {goal.status === "paused" && (
          <button
            onClick={() => resumeMutation.mutate()}
            disabled={resumeMutation.isPending}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border border-green-300 text-green-700 rounded-md hover:bg-green-50 transition-colors disabled:opacity-50"
            aria-label="Resume goal"
          >
            <Play className="h-4 w-4" /> Resume
          </button>
        )}
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ["goal", goalId] })}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border border-border rounded-md hover:bg-accent transition-colors"
        >
          <RefreshCw className="h-4 w-4" /> Refresh
        </button>
      </div>

      {/* HITL approval panel */}
      {goal.status === "waiting_human" && (
        <div className="bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800 rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-2 text-orange-800 dark:text-orange-300">
            Human approval required
          </h2>
          <p className="text-sm text-orange-700 dark:text-orange-400 mb-3">
            The agent is paused waiting for your approval to continue.
          </p>

          {approvalsLoading ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-3">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Loading approval request…
            </div>
          ) : !pendingApproval ? (
            <p className="text-xs text-orange-600 dark:text-orange-400 italic mb-3">
              Waiting for approval request to be registered by the backend…
            </p>
          ) : (
            <>
              {pendingApproval.action && (
                <p className="text-xs text-orange-700 dark:text-orange-400 mb-1">
                  Action:{" "}
                  <span className="font-mono font-medium">{pendingApproval.action}</span>
                </p>
              )}
              {pendingApproval.risk_level && (
                <p className="text-xs text-orange-700 dark:text-orange-400 mb-3">
                  Risk level:{" "}
                  <span className="font-medium capitalize">{pendingApproval.risk_level}</span>
                </p>
              )}
              <textarea
                value={approvalNote}
                onChange={(e) => setApprovalNote(e.target.value)}
                placeholder="Optional note…"
                rows={2}
                className="w-full px-3 py-2 text-sm border border-orange-300 dark:border-orange-700 rounded-md bg-background mb-3 focus:outline-none focus:ring-2 focus:ring-orange-400 resize-none"
              />
              {(approveMutation.isError || rejectMutation.isError) && (
                <p className="text-xs text-red-600 mb-2">
                  {String(approveMutation.error ?? rejectMutation.error)}
                </p>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => approveMutation.mutate()}
                  disabled={approveMutation.isPending || rejectMutation.isPending}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-green-600 text-white text-sm rounded-md hover:bg-green-700 transition-colors disabled:opacity-50"
                >
                  {approveMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <CheckCircle className="h-3.5 w-3.5" />
                  )}
                  {approveMutation.isPending ? "Approving…" : "Approve"}
                </button>
                <button
                  onClick={() => rejectMutation.mutate()}
                  disabled={approveMutation.isPending || rejectMutation.isPending}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 transition-colors disabled:opacity-50"
                >
                  {rejectMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <XCircle className="h-3.5 w-3.5" />
                  )}
                  {rejectMutation.isPending ? "Rejecting…" : "Reject"}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Tab bar */}
      <div className="flex items-center gap-2 flex-wrap mt-2 pt-2 border-t border-border">
        <span className="text-xs text-muted-foreground">Analysis:</span>
        <button
          onClick={() => navigate(`/goals/${goalId}/dna`)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-border rounded-lg hover:bg-muted/60 transition-colors"
          title="Visualize execution as a force graph"
        >
          <Dna className="h-3 w-3" aria-hidden="true" />
          View DNA
        </button>
        <button
          onClick={() => navigate(`/goals/${goalId}/diff`)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-border rounded-lg hover:bg-muted/60 transition-colors"
          title="Compare this run with another"
        >
          <GitCompare className="h-3 w-3" aria-hidden="true" />
          Diff Run
        </button>
        <button
          onClick={() => navigate("/goals/ghost-run")}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-border rounded-lg hover:bg-muted/60 transition-colors"
          title="Run same goal with multiple strategies"
        >
          <Ghost className="h-3 w-3" aria-hidden="true" />
          Ghost Run
        </button>
      </div>

      {/* Tab bar */}
      <div role="tablist" aria-label="Goal detail tabs" className="flex gap-1 border-b">
        {(["pipeline", "events"] as const).map((tab) => (
          <button
            key={tab}
            role="tab"
            aria-selected={activeTab === tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors capitalize ${
              activeTab === tab
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab === "pipeline" ? "Pipeline" : "Event Log"}
          </button>
        ))}
        {["complete", "failed"].includes(goal.status) && (
          <button
            role="tab"
            aria-selected={activeTab === "eval"}
            onClick={() => setActiveTab("eval")}
            className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === "eval"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            Eval
          </button>
        )}
      </div>

      {/* Pipeline tab */}
      {activeTab === "pipeline" && (
        <>
          {/* Live event stream */}
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <h2 className="font-semibold text-sm">Pipeline steps</h2>
              <span className={`text-xs ${connected ? "text-green-500" : "text-muted-foreground"}`}>
                {connected ? "● Live" : "○ Disconnected"}
              </span>
            </div>
            {events.length === 0 ? (
              <div className="px-4 py-6 text-center text-sm text-muted-foreground">
                {goal.status === "complete" || goal.status === "failed"
                  ? "Goal finished. No live events."
                  : "Waiting for events…"}
              </div>
            ) : (
              <ul>
                {events.map((evt, i) => (
                  <StepRow key={(evt as any).event_id ?? `evt-${i}`} event={evt as Record<string, unknown>} />
                ))}
              </ul>
            )}
          </div>

          {/* Execution Timeline */}
          {events.length > 0 && <ExecutionTimeline events={events as any[]} />}

          {/* Tool Call Inspector */}
          {events.some((e) => (e as any).type === "tool_call_complete") && (
            <ToolCallInspector
              toolEvents={(events as any[]).filter((e) => e.type === "tool_call_complete")}
            />
          )}
        </>
      )}

      {/* Event Log tab */}
      {activeTab === "events" && (
        <div className="space-y-2">
          {eventsLoading
            ? Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))
            : eventLog.length === 0
            ? (
              <EmptyState
                title="No persisted events"
                description="Events appear after goal execution completes."
              />
            )
            : eventLog.map((ev, i) => (
              <div
                key={i}
                className="flex items-start gap-3 p-3 rounded-lg border bg-card text-sm"
              >
                <span className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                  {ev.created_at
                    ? new Date(ev.created_at).toLocaleTimeString()
                    : `#${i + 1}`}
                </span>
                <span className="font-medium">{ev.type}</span>
                {ev.payload?.message != null && (
                  <span className="text-muted-foreground text-xs">
                    {String(ev.payload.message)}
                  </span>
                )}
              </div>
            ))}
        </div>
      )}

      {/* Eval tab */}
      {activeTab === "eval" && (
        <div className="space-y-4">
          {evalLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : !evaluation ? (
            <EmptyState
              title="No evaluation"
              description="Evaluation runs automatically after goal completion."
            />
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-4 p-4 rounded-lg border bg-card">
                <div className="text-3xl font-bold">
                  {((evaluation.score ?? 0) * 100).toFixed(0)}%
                </div>
                <div>
                  <p className="text-sm font-medium">Overall Score</p>
                  <p
                    className={`text-xs font-medium ${
                      evaluation.passed ? "text-green-600" : "text-red-600"
                    }`}
                  >
                    {evaluation.passed ? "PASSED" : "FAILED"}
                  </p>
                </div>
              </div>
              {evaluation.criteria && evaluation.criteria.length > 0 && (
                <div className="grid grid-cols-2 gap-2">
                  {evaluation.criteria.map((c) => (
                    <div key={c.name} className="p-3 rounded border bg-card">
                      <p className="text-xs text-muted-foreground capitalize">
                        {c.name.replace(/_/g, " ")}
                      </p>
                      <p className="text-lg font-semibold">
                        {(c.score * 100).toFixed(0)}%
                      </p>
                      <span
                        className={`text-[10px] font-medium ${
                          c.passed ? "text-green-600" : "text-red-500"
                        }`}
                      >
                        {c.passed ? "✓ pass" : "✗ fail"}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {import.meta.env.DEV && (
        <details className="mt-4">
          <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground">
            Debug: raw goal state
          </summary>
          <pre className="mt-2 text-[10px] bg-muted rounded p-3 overflow-auto max-h-64">
            {JSON.stringify(goal, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
