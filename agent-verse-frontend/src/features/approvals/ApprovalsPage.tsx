import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { governanceApi, ApprovalRequest } from "@/lib/api/client";
import { CheckCircle, XCircle, Loader2, Inbox } from "lucide-react";

// ── ApprovalsPage ─────────────────────────────────────────────────────────────

export function ApprovalsPage() {
  const qc = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [actionPending, setActionPending] = useState<string | null>(null);

  const {
    data: approvals = [],
    isLoading,
    error,
  } = useQuery<ApprovalRequest[]>({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 5_000,
  });

  const pending = approvals.filter((a) => a.status === "pending");

  const approveMutation = useMutation({
    mutationFn: ({ requestId, note }: { requestId: string; note: string }) =>
      governanceApi.approve(requestId, "ui-user", note),
    onMutate: ({ requestId }) => setActionPending(requestId),
    onSettled: () => {
      setActionPending(null);
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ requestId, note }: { requestId: string; note: string }) =>
      governanceApi.reject(requestId, "ui-user", note),
    onMutate: ({ requestId }) => setActionPending(requestId),
    onSettled: () => {
      setActionPending(null);
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
  });

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            Approval Inbox
            {pending.length > 0 && (
              <span className="inline-flex items-center justify-center px-2 py-0.5 rounded-full text-xs font-bold bg-orange-500 text-white min-w-[1.5rem]">
                {pending.length}
              </span>
            )}
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            Human-in-the-loop requests awaiting your decision
          </p>
        </div>
        <div className="text-xs text-muted-foreground">
          Auto-refreshes every 5s
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-16" data-testid="loading">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
          Failed to load approvals: {String(error)}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && pending.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center" data-testid="empty-state">
          <Inbox className="h-12 w-12 text-muted-foreground mb-4" />
          <h2 className="font-semibold text-lg mb-1">All clear</h2>
          <p className="text-muted-foreground text-sm">No pending approval requests.</p>
        </div>
      )}

      {/* Approval cards */}
      {pending.length > 0 && (
        <div className="space-y-4">
          {pending.map((req) => {
            const note = notes[req.request_id] ?? "";
            const isBusy = actionPending === req.request_id;

            return (
              <div
                key={req.request_id}
                className="bg-card border border-border rounded-xl p-5 space-y-4"
              >
                {/* Request header */}
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-xs font-mono text-muted-foreground truncate">
                      {req.request_id}
                    </p>
                    {req.action && (
                      <p className="font-medium mt-1">{req.action}</p>
                    )}
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Goal: <span className="font-mono">{req.goal_id}</span>
                    </p>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {req.risk_level && (
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          req.risk_level === "critical"
                            ? "bg-red-100 text-red-700"
                            : req.risk_level === "high"
                            ? "bg-orange-100 text-orange-700"
                            : req.risk_level === "medium"
                            ? "bg-yellow-100 text-yellow-700"
                            : "bg-green-100 text-green-700"
                        }`}
                      >
                        {req.risk_level}
                      </span>
                    )}
                    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-orange-100 text-orange-700">
                      pending
                    </span>
                  </div>
                </div>

                {/* Note input */}
                <textarea
                  value={note}
                  onChange={(e) =>
                    setNotes((n) => ({ ...n, [req.request_id]: e.target.value }))
                  }
                  placeholder="Optional note (required for rejection)…"
                  rows={2}
                  className="w-full px-3 py-2 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                />

                {/* Actions */}
                <div className="flex items-center gap-3">
                  <button
                    onClick={() =>
                      approveMutation.mutate({ requestId: req.request_id, note })
                    }
                    disabled={isBusy}
                    className="flex items-center gap-1.5 px-4 py-2 bg-green-600 hover:bg-green-700 text-white text-sm rounded-md transition-colors disabled:opacity-50"
                  >
                    {isBusy && approveMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <CheckCircle className="h-4 w-4" />
                    )}
                    Approve
                  </button>
                  <button
                    onClick={() =>
                      rejectMutation.mutate({ requestId: req.request_id, note })
                    }
                    disabled={isBusy}
                    className="flex items-center gap-1.5 px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm rounded-md transition-colors disabled:opacity-50"
                  >
                    {isBusy && rejectMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <XCircle className="h-4 w-4" />
                    )}
                    Reject
                  </button>
                </div>

                {/* Mutation errors */}
                {(approveMutation.isError || rejectMutation.isError) &&
                  actionPending === null && (
                    <p className="text-xs text-red-600">
                      {String(approveMutation.error ?? rejectMutation.error)}
                    </p>
                  )}
              </div>
            );
          })}
        </div>
      )}

      {/* Resolved section */}
      {approvals.some((a) => a.status !== "pending") && (
        <div>
          <h2 className="text-sm font-semibold text-muted-foreground mb-3">
            Recently resolved
          </h2>
          <div className="space-y-2">
            {approvals
              .filter((a) => a.status !== "pending")
              .slice(0, 10)
              .map((req) => (
                <div
                  key={req.request_id}
                  className="flex items-center justify-between px-4 py-2.5 bg-muted/40 rounded-lg text-sm"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    {req.status === "approved" ? (
                      <CheckCircle className="h-4 w-4 text-green-500 flex-shrink-0" />
                    ) : (
                      <XCircle className="h-4 w-4 text-red-500 flex-shrink-0" />
                    )}
                    <span className="font-mono text-xs text-muted-foreground truncate">
                      {req.request_id}
                    </span>
                    {req.action && (
                      <span className="truncate text-xs">{req.action}</span>
                    )}
                  </div>
                  <span
                    className={`px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0 ${
                      req.status === "approved"
                        ? "bg-green-100 text-green-700"
                        : "bg-red-100 text-red-700"
                    }`}
                  >
                    {req.status}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
