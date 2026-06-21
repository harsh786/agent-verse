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
} from "lucide-react";
import { useState } from "react";
import { goalsApi } from "@/lib/api/client";
import { useGoalStream } from "@/lib/sse/useGoalStream";

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

function StepRow({ event }: { event: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const step = event.step as string;
  const status = event.status as string;
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
        <span className="flex-1 text-sm font-medium">{step}</span>
        {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && (
        <pre className="px-4 pb-3 text-xs overflow-x-auto whitespace-pre-wrap text-muted-foreground">
          {JSON.stringify(event, null, 2)}
        </pre>
      )}
    </li>
  );
}

export function GoalDetailPage() {
  const { goalId } = useParams<{ goalId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [approvalNote, setApprovalNote] = useState("");

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
          <textarea
            value={approvalNote}
            onChange={(e) => setApprovalNote(e.target.value)}
            placeholder="Optional note…"
            rows={2}
            className="w-full px-3 py-2 text-sm border border-orange-300 dark:border-orange-700 rounded-md bg-background mb-3 focus:outline-none focus:ring-2 focus:ring-orange-400 resize-none"
          />
          <div className="flex gap-2">
            <button className="px-4 py-1.5 bg-green-600 text-white text-sm rounded-md hover:bg-green-700 transition-colors">
              Approve
            </button>
            <button className="px-4 py-1.5 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 transition-colors">
              Reject
            </button>
          </div>
        </div>
      )}

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
              <StepRow key={i} event={evt as Record<string, unknown>} />
            ))}
          </ul>
        )}
      </div>

      {/* Raw state */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="font-semibold text-sm">Raw state</h2>
        </div>
        <pre className="px-4 py-4 text-xs overflow-x-auto whitespace-pre-wrap text-muted-foreground">
          {JSON.stringify(goal, null, 2)}
        </pre>
      </div>
    </div>
  );
}
