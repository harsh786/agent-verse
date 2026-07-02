/**
 * ApprovalsPage — world-class HITL (Human-in-the-Loop) approval inbox.
 *
 * Features:
 *  - Live SSE connection indicator (green/red dot)
 *  - Stats bar: pending by risk level, avg resolution time, SLA compliance
 *  - Two tabs: Inbox (pending) and History (resolved)
 *  - Risk-level filter pills and sort by risk/time
 *  - Bulk select (checkboxes) + Approve All / Reject All
 *  - Approval cards with: risk border, SLA countdown, multi-person progress, goal link
 *  - Collapsible note textarea per card
 *  - Keyboard navigation: ↑↓ navigate, A approve, R reject, ? help
 *  - Toast notification on new arrivals via SSE
 *  - Rich empty state and history tab
 */
import {
  useState, useEffect, useCallback, useRef, useMemo, type KeyboardEvent,
} from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle, XCircle, Loader2, Inbox, Clock, Shield, AlertTriangle,
  ChevronDown, ChevronUp, History, MessageSquare, ExternalLink,
  Keyboard, X, Check, Users,
} from "lucide-react";
import { governanceApi, type ApprovalRequest } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";
import { useEventStream } from "@/lib/sse/useEventStream";
import { toast } from "@/stores/toast";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmModal } from "@/components/ui/ConfirmModal";

// ── Helpers ───────────────────────────────────────────────────────────────────

type RiskLevel = "critical" | "high" | "medium" | "low";
type Tab = "inbox" | "history";
type SortKey = "risk" | "time";

const RISK_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

const RISK_STYLES: Record<string, { badge: string; border: string; pulse: boolean }> = {
  critical: { badge: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",    border: "border-l-4 border-l-red-500",    pulse: true  },
  high:     { badge: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400", border: "border-l-4 border-l-orange-500", pulse: false },
  medium:   { badge: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400", border: "border-l-4 border-l-yellow-500", pulse: false },
  low:      { badge: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",  border: "border-l-4 border-l-green-500",  pulse: false },
};

function riskStyle(level?: string) {
  return RISK_STYLES[level ?? "medium"] ?? RISK_STYLES.medium;
}

function timeAgo(iso?: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

function slaCountdown(createdAt?: string, timeoutSeconds = 300): { label: string; urgent: boolean } | null {
  if (!createdAt) return null;
  const expiresAt = new Date(createdAt).getTime() + timeoutSeconds * 1000;
  const remaining = expiresAt - Date.now();
  if (remaining <= 0) return { label: "Expired", urgent: true };
  const s = Math.floor(remaining / 1000);
  const m = Math.floor(s / 60);
  if (m > 60) return null; // not urgent enough to show
  return { label: `${m}m ${s % 60}s`, urgent: m < 2 };
}

// ── Keyboard shortcuts help dialog ───────────────────────────────────────────

const SHORTCUTS = [
  { keys: "↑ / ↓", description: "Navigate between requests" },
  { keys: "A",     description: "Approve focused request" },
  { keys: "R",     description: "Reject focused request" },
  { keys: "N",     description: "Toggle note on focused request" },
  { keys: "Space", description: "Toggle selection (bulk mode)" },
  { keys: "⌘A",   description: "Select all visible" },
  { keys: "?",     description: "Toggle this help dialog" },
  { keys: "Esc",   description: "Clear selection / close dialog" },
];

function ShortcutHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[400] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-2xl max-w-sm w-full p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Keyboard className="h-4 w-4 text-primary" aria-hidden="true" /> Keyboard Shortcuts
          </h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <dl className="space-y-2">
          {SHORTCUTS.map(({ keys, description }) => (
            <div key={keys} className="flex items-center justify-between gap-4">
              <kbd className="px-2 py-0.5 bg-muted rounded text-xs font-mono">{keys}</kbd>
              <dd className="text-xs text-muted-foreground text-right">{description}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

// ── SLA stats bar ─────────────────────────────────────────────────────────────

function StatsBar({ pending }: { pending: ApprovalRequest[] }) {
  const { data: sla } = useQuery({
    queryKey: ["approval-sla-stats"],
    queryFn: () => governanceApi.getSlaStats(),
    staleTime: 60_000,
  });

  const byRisk = useMemo(() => {
    const counts: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const r of pending) counts[r.risk_level ?? "medium"] = (counts[r.risk_level ?? "medium"] ?? 0) + 1;
    return counts;
  }, [pending]);

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      {(["critical", "high", "medium", "low"] as RiskLevel[]).map((risk) => {
        const style = RISK_STYLES[risk];
        return (
          <div key={risk} className={`bg-card border rounded-xl px-4 py-3 ${style.border}`}>
            <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground capitalize">{risk}</p>
            <p className="text-2xl font-bold tabular-nums">{byRisk[risk] ?? 0}</p>
          </div>
        );
      })}
      {sla && sla.avg_resolution_seconds != null && (
        <div className="sm:col-span-4 bg-card border border-border rounded-xl px-4 py-2 flex items-center gap-6 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            Avg resolution: <strong className="text-foreground">{Math.round(sla.avg_resolution_seconds / 60)}m</strong>
          </span>
          {sla.within_sla > 0 && (
            <span className="flex items-center gap-1.5">
              <Shield className="h-3.5 w-3.5 text-green-500" aria-hidden="true" />
              Within SLA: <strong className="text-green-600 dark:text-green-400">{sla.within_sla}</strong>
            </span>
          )}
          {(sla.timed_out ?? 0) > 0 && (
            <span className="flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 text-red-500" aria-hidden="true" />
              Timed out: <strong className="text-red-600 dark:text-red-400">{sla.timed_out}</strong>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Approval card ─────────────────────────────────────────────────────────────

function ApprovalCard({
  req,
  isSelected,
  isFocused,
  onSelect,
  onApprove,
  onReject,
  approving,
  rejecting,
  cardRef,
}: {
  req: ApprovalRequest;
  isSelected: boolean;
  isFocused: boolean;
  onSelect: () => void;
  onApprove: (note: string) => void;
  onReject: (note: string) => void;
  approving: boolean;
  rejecting: boolean;
  cardRef?: React.RefObject<HTMLDivElement>;
}) {
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const style = riskStyle(req.risk_level);
  const sla = slaCountdown(req.created_at);
  const isBusy = approving || rejecting;

  return (
    <div
      ref={cardRef}
      className={`bg-card border border-border rounded-xl overflow-hidden transition-all ${style.border} ${
        isFocused ? "ring-2 ring-primary ring-offset-1" : ""
      } ${style.pulse ? "animate-pulse-once" : ""}`}
      data-testid="approval-card"
      tabIndex={-1}
    >
      <div className="p-4 space-y-3">
        {/* Top row: checkbox + risk badge + time + SLA */}
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={isSelected}
            onChange={onSelect}
            className="mt-0.5 h-4 w-4 accent-primary shrink-0 cursor-pointer"
            aria-label={`Select request ${req.request_id}`}
          />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              {req.risk_level && (
                <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold uppercase tracking-wide ${style.badge}`}>
                  {req.risk_level}
                </span>
              )}
              <span className="text-[10px] bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400 px-2 py-0.5 rounded-full font-medium">
                pending
              </span>
              {sla && (
                <span className={`flex items-center gap-1 text-[10px] font-medium ${
                  sla.urgent ? "text-red-600 dark:text-red-400" : "text-amber-600 dark:text-amber-400"
                }`}>
                  <Clock className="h-3 w-3" aria-hidden="true" />
                  {sla.urgent ? "⚡ " : ""}{sla.label}
                </span>
              )}
              <span className="text-[10px] text-muted-foreground ml-auto">{timeAgo(req.created_at)}</span>
            </div>

            {/* Action */}
            {req.action && (
              <p className="text-sm font-semibold leading-snug">{req.action}</p>
            )}

            {/* Goal + multi-person progress */}
            <div className="flex items-center gap-3 flex-wrap mt-1">
              <GoalLink goalId={req.goal_id} />
              {(req.required_approvers ?? 1) > 1 && (
                <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                  <Users className="h-3 w-3" aria-hidden="true" />
                  {req.approvals_received ?? 0}/{req.required_approvers} approvers
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Note area (collapsible) */}
        <div>
          <button
            type="button"
            onClick={() => setNoteOpen((v) => !v)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" />
            {noteOpen ? "Hide note" : "Add note"}
            {noteOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
          {noteOpen && (
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Optional note (required for rejection)…"
              rows={2}
              className="mt-2 w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
              aria-label="Approval note"
            />
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => onApprove(note)}
            disabled={isBusy}
            className="flex items-center gap-1.5 px-4 py-2 bg-green-600 hover:bg-green-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
            aria-label="Approve request"
          >
            {approving ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <CheckCircle className="h-3.5 w-3.5" aria-hidden="true" />}
            Approve
          </button>
          <button
            onClick={() => onReject(note)}
            disabled={isBusy}
            className="flex items-center gap-1.5 px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
            aria-label="Reject request"
          >
            {rejecting ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <XCircle className="h-3.5 w-3.5" aria-hidden="true" />}
            Reject
          </button>
          <span className="text-[10px] text-muted-foreground font-mono truncate ml-auto">{req.request_id.slice(0, 16)}…</span>
        </div>
      </div>
    </div>
  );
}

function GoalLink({ goalId }: { goalId: string }) {
  const navigate = useNavigate();
  return (
    <button
      onClick={(e) => { e.stopPropagation(); navigate(`/goals/${goalId}`); }}
      className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-primary transition-colors font-mono"
      title={`Go to goal ${goalId}`}
    >
      <ExternalLink className="h-3 w-3" aria-hidden="true" />
      {goalId.slice(0, 16)}…
    </button>
  );
}

// ── History row ───────────────────────────────────────────────────────────────

function HistoryRow({ req }: { req: ApprovalRequest }) {
  const style = riskStyle(req.risk_level);
  return (
    <div className={`flex items-center gap-3 px-4 py-3 bg-card border border-border rounded-xl ${style.border}`} data-testid="history-row">
      <div className="flex items-center gap-2 shrink-0">
        {req.status === "approved"
          ? <CheckCircle className="h-4 w-4 text-green-500" aria-hidden="true" />
          : req.status === "timed_out"
          ? <Clock className="h-4 w-4 text-amber-500" aria-hidden="true" />
          : <XCircle className="h-4 w-4 text-red-500" aria-hidden="true" />}
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
          req.status === "approved" ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
          : req.status === "timed_out" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
          : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
        }`}>
          {req.status}
        </span>
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm truncate">{req.action ?? req.request_id}</p>
        <p className="text-[10px] text-muted-foreground">
          {req.approver && <span>by {req.approver} · </span>}
          {timeAgo(req.resolved_at ?? req.created_at)}
          {req.note && <span className="italic"> · "{req.note}"</span>}
        </p>
      </div>
      {req.risk_level && (
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${style.badge} shrink-0`}>
          {req.risk_level}
        </span>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function ApprovalsPage() {
  const qc = useQueryClient();
  const { tenantId } = useAuthStore();
  const approverId = tenantId ? `user:${tenantId.slice(0, 12)}` : "ui-user";

  // ── State ──────────────────────────────────────────────────────────────────
  const [tab, setTab] = useState<Tab>("inbox");
  const [riskFilter, setRiskFilter] = useState<RiskLevel | "all">("all");
  const [sort, setSort] = useState<SortKey>("risk");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focusIndex, setFocusIndex] = useState(0);
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [bulkNote, setBulkNote] = useState("");
  const [bulkAction, setBulkAction] = useState<"approve" | "reject" | null>(null);
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const prevCountRef = useRef(0);

  // ── Queries ────────────────────────────────────────────────────────────────
  const { data: approvals = [], isLoading, error } = useQuery<ApprovalRequest[]>({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 30_000,
  });

  const { data: history = [], isLoading: historyLoading } = useQuery<ApprovalRequest[]>({
    queryKey: ["approvals-history"],
    queryFn: () => governanceApi.listHistory(50),
    enabled: tab === "history",
    staleTime: 30_000,
  });

  // SSE for live updates
  const { connected } = useEventStream(governanceApi.approvalsStreamPath(), {
    onEvent: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });

  // Toast when new items arrive
  useEffect(() => {
    const pending = approvals.filter((a) => a.status === "pending");
    if (prevCountRef.current > 0 && pending.length > prevCountRef.current) {
      toast({ kind: "info", message: `${pending.length - prevCountRef.current} new approval request(s) arrived.` });
    }
    prevCountRef.current = pending.length;
  }, [approvals]);

  // ── Derived lists ──────────────────────────────────────────────────────────
  const pending = useMemo(() => {
    let list = approvals.filter((a) => a.status === "pending");
    if (riskFilter !== "all") list = list.filter((a) => a.risk_level === riskFilter);
    if (sort === "risk") list = [...list].sort((a, b) => (RISK_ORDER[a.risk_level ?? "medium"] ?? 3) - (RISK_ORDER[b.risk_level ?? "medium"] ?? 3));
    else list = [...list].sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime());
    return list;
  }, [approvals, riskFilter, sort]);

  // ── Mutations ──────────────────────────────────────────────────────────────
  const approveMutation = useMutation({
    mutationFn: ({ requestId, note }: { requestId: string; note: string }) =>
      governanceApi.approve(requestId, approverId, note),
    onMutate: ({ requestId }) => setActionPending(requestId),
    onSuccess: () => { toast({ kind: "success", message: "Request approved." }); },
    onSettled: () => { setActionPending(null); qc.invalidateQueries({ queryKey: ["approvals"] }); },
    onError: (e) => toast({ kind: "error", message: `Approve failed: ${String(e)}` }),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ requestId, note }: { requestId: string; note: string }) =>
      governanceApi.reject(requestId, approverId, note),
    onMutate: ({ requestId }) => setActionPending(requestId),
    onSuccess: () => { toast({ kind: "success", message: "Request rejected." }); },
    onSettled: () => { setActionPending(null); qc.invalidateQueries({ queryKey: ["approvals"] }); },
    onError: (e) => toast({ kind: "error", message: `Reject failed: ${String(e)}` }),
  });

  const batchMutation = useMutation({
    mutationFn: ({ action, note }: { action: "approve" | "reject"; note: string }) =>
      governanceApi.batchApprove(Array.from(selected), action, approverId, note),
    onSuccess: (data) => {
      const n = data.approved + data.rejected;
      toast({ kind: "success", message: `Batch action complete: ${n} request(s) processed.` });
      setSelected(new Set());
      setBulkNote("");
      setBulkAction(null);
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (e) => toast({ kind: "error", message: `Batch action failed: ${String(e)}` }),
  });

  // ── Selection helpers ──────────────────────────────────────────────────────
  const toggleSelect = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelected(new Set(pending.map((r) => r.request_id)));
  }, [pending]);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  // ── Keyboard navigation ────────────────────────────────────────────────────
  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (showHelp && e.key === "Escape") { setShowHelp(false); return; }
    if (e.key === "?") { setShowHelp((v) => !v); return; }
    if (e.key === "Escape") { clearSelection(); return; }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setFocusIndex((i) => Math.min(i + 1, pending.length - 1));
      const next = pending[Math.min(focusIndex + 1, pending.length - 1)];
      if (next) cardRefs.current.get(next.request_id)?.focus();
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setFocusIndex((i) => Math.max(i - 1, 0));
      const prev = pending[Math.max(focusIndex - 1, 0)];
      if (prev) cardRefs.current.get(prev.request_id)?.focus();
    }
    if (e.key === "a" || e.key === "A") {
      const req = pending[focusIndex];
      if (req && !actionPending) approveMutation.mutate({ requestId: req.request_id, note: "" });
    }
    if (e.key === "r" || e.key === "R") {
      const req = pending[focusIndex];
      if (req && !actionPending) rejectMutation.mutate({ requestId: req.request_id, note: "" });
    }
    if (e.key === " ") {
      e.preventDefault();
      const req = pending[focusIndex];
      if (req) toggleSelect(req.request_id);
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "a") {
      e.preventDefault();
      selectAll();
    }
  }, [showHelp, pending, focusIndex, actionPending, approveMutation, rejectMutation, toggleSelect, selectAll, clearSelection]);

  const allSelected = pending.length > 0 && pending.every((r) => selected.has(r.request_id));

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div
      className="space-y-5 max-w-3xl outline-none"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      aria-label="Approval inbox"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            Approval Inbox
            {pending.length > 0 && (
              <span className="inline-flex items-center justify-center px-2 py-0.5 rounded-full text-xs font-bold bg-orange-500 text-white min-w-[1.5rem]">
                {pending.length}
              </span>
            )}
          </h1>
          <p className="text-muted-foreground text-sm mt-0.5">
            Human-in-the-loop requests awaiting your decision
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* SSE Live indicator */}
          <div className={`flex items-center gap-1.5 text-xs ${connected ? "text-green-600 dark:text-green-400" : "text-muted-foreground"}`} aria-live="polite">
            <span className={`w-2 h-2 rounded-full ${connected ? "bg-green-500 animate-pulse" : "bg-muted-foreground/50"}`} aria-hidden="true" />
            {connected ? "Live" : "Reconnecting…"}
          </div>
          <button
            onClick={() => setShowHelp(true)}
            className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Keyboard shortcuts"
            title="Keyboard shortcuts (?)"
          >
            <Keyboard className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* Stats bar */}
      {!isLoading && <StatsBar pending={pending} />}

      {/* Tabs */}
      <div className="flex items-center justify-between gap-4">
        <div role="tablist" className="flex gap-1 border-b border-border -mb-px">
          {([
            { key: "inbox",   label: "Inbox",   count: pending.length },
            { key: "history", label: "History", count: null },
          ] as { key: Tab; label: string; count: number | null }[]).map(({ key, label, count }) => (
            <button
              key={key}
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                tab === key ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {key === "history" ? <History className="h-3.5 w-3.5" aria-hidden="true" /> : null}
              {label}
              {count !== null && count > 0 && (
                <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-orange-500 text-white">{count}</span>
              )}
            </button>
          ))}
        </div>

        {/* Inbox controls */}
        {tab === "inbox" && (
          <div className="flex items-center gap-2 flex-wrap justify-end">
            {/* Risk filter pills */}
            <div className="flex gap-1">
              {(["all", "critical", "high", "medium", "low"] as const).map((r) => (
                <button
                  key={r}
                  onClick={() => setRiskFilter(r)}
                  className={`px-2.5 py-1 text-[10px] rounded-full border capitalize transition-colors ${
                    riskFilter === r ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted/50 text-muted-foreground"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
            {/* Sort */}
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="text-xs border border-input rounded px-2 py-1 bg-background"
              aria-label="Sort by"
            >
              <option value="risk">Sort: Risk</option>
              <option value="time">Sort: Time</option>
            </select>
          </div>
        )}
      </div>

      {/* ── INBOX TAB ─────────────────────────────────────────────────────── */}
      {tab === "inbox" && (
        <>
          {isLoading && (
            <div className="space-y-3" data-testid="loading">
              {Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
            </div>
          )}
          {error && (
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl px-4 py-3 text-sm text-red-700 dark:text-red-400">
              Failed to load approvals: {String(error)}
            </div>
          )}
          {!isLoading && !error && pending.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-center" data-testid="empty-state">
              <div className="w-16 h-16 bg-muted/40 rounded-full flex items-center justify-center mb-4">
                <Inbox className="h-8 w-8 text-muted-foreground/50" aria-hidden="true" />
              </div>
              <h2 className="font-semibold text-lg mb-1">All clear</h2>
              <p className="text-muted-foreground text-sm max-w-xs">
                {riskFilter !== "all"
                  ? `No pending ${riskFilter} risk requests.`
                  : "No pending approval requests. Agents are running autonomously."}
              </p>
              {riskFilter !== "all" && (
                <button onClick={() => setRiskFilter("all")} className="mt-3 text-xs text-primary hover:underline">
                  Show all requests
                </button>
              )}
            </div>
          )}

          {/* Bulk toolbar */}
          {selected.size > 0 && (
            <div className="sticky top-2 z-10 flex items-center gap-3 bg-card border border-primary shadow-lg rounded-xl px-4 py-2.5 animate-in slide-in-from-top-2">
              <span className="text-sm font-medium text-primary">{selected.size} selected</span>
              <div className="flex-1" />
              <button
                onClick={() => setBulkAction("approve")}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white text-xs font-medium rounded-lg hover:bg-green-700"
              >
                <Check className="h-3.5 w-3.5" aria-hidden="true" /> Approve all
              </button>
              <button
                onClick={() => setBulkAction("reject")}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white text-xs font-medium rounded-lg hover:bg-red-700"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" /> Reject all
              </button>
              <button onClick={clearSelection} className="text-xs text-muted-foreground hover:text-foreground" aria-label="Clear selection">
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}

          {/* Select all toggle */}
          {pending.length > 1 && (
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={allSelected ? clearSelection : selectAll}
                className="h-4 w-4 accent-primary cursor-pointer"
                id="select-all"
                aria-label="Select all requests"
              />
              <label htmlFor="select-all" className="text-xs text-muted-foreground cursor-pointer select-none">
                {allSelected ? "Deselect all" : "Select all"} ({pending.length})
              </label>
            </div>
          )}

          {/* Cards */}
          {pending.length > 0 && (
            <div className="space-y-3">
              {pending.map((req, i) => {
                const ref = { current: cardRefs.current.get(req.request_id) ?? null } as React.RefObject<HTMLDivElement>;
                return (
                  <ApprovalCard
                    key={req.request_id}
                    req={req}
                    isSelected={selected.has(req.request_id)}
                    isFocused={focusIndex === i}
                    onSelect={() => toggleSelect(req.request_id)}
                    onApprove={(note) => approveMutation.mutate({ requestId: req.request_id, note })}
                    onReject={(note) => rejectMutation.mutate({ requestId: req.request_id, note })}
                    approving={actionPending === req.request_id && approveMutation.isPending}
                    rejecting={actionPending === req.request_id && rejectMutation.isPending}
                    cardRef={ref}
                  />
                );
              })}
            </div>
          )}

          {/* Keyboard hint bar */}
          {pending.length > 0 && (
            <div className="flex items-center justify-center gap-4 text-[10px] text-muted-foreground py-2 border-t border-border">
              <span>↑↓ navigate</span>
              <span>A approve</span>
              <span>R reject</span>
              <span>Space select</span>
              <button onClick={() => setShowHelp(true)} className="hover:text-foreground">? help</button>
            </div>
          )}
        </>
      )}

      {/* ── HISTORY TAB ───────────────────────────────────────────────────── */}
      {tab === "history" && (
        <div className="space-y-3">
          {historyLoading && (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-14 rounded-xl" />)}
            </div>
          )}
          {!historyLoading && history.length === 0 && (
            <EmptyState title="No history yet" description="Resolved approval requests will appear here." />
          )}
          {history.map((req) => (
            <HistoryRow key={req.request_id} req={req} />
          ))}
        </div>
      )}

      {/* Bulk action confirm modal */}
      <ConfirmModal
        open={bulkAction !== null}
        title={`${bulkAction === "approve" ? "Approve" : "Reject"} ${selected.size} request(s)?`}
        description={
          bulkAction === "reject"
            ? "All selected requests will be rejected and the waiting agents will be notified."
            : "All selected requests will be approved and the waiting agents will resume."
        }
        confirmLabel={bulkAction === "approve" ? "Approve all" : "Reject all"}
        variant={bulkAction === "reject" ? "danger" : "warning"}
        isLoading={batchMutation.isPending}
        onConfirm={() => bulkAction && batchMutation.mutate({ action: bulkAction, note: bulkNote })}
        onCancel={() => setBulkAction(null)}
      />

      {/* Keyboard shortcuts dialog */}
      {showHelp && <ShortcutHelp onClose={() => setShowHelp(false)} />}
    </div>
  );
}
